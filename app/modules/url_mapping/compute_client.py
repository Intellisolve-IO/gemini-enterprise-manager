"""GCP HTTP(S) Load Balancer provisioning for the App URL Mapping module.

Creates, per mapping: a global static IP, a Google-managed SSL certificate for
the custom domain, a URL map that redirects to the target Gemini Enterprise
deep link, a target HTTPS proxy, a global forwarding rule on port 443, and a
matching HTTP (port 80) redirect-to-HTTPS stack on the same reserved IP.

Uses the generated `google-cloud-compute` client rather than hand-rolled REST
(unlike `gemini_licensing.py`'s raw-REST pattern) because Compute's request
objects are large and deeply nested (redirect config, LB scheme enums,
proxy/cert/url-map cross-references) - the generated client's typed requests
and built-in operation polling (`ExtendedOperation.result()`) meaningfully
reduce the bug surface for orchestrating eight resources by hand.

KNOWN LIMITATION - needs validation against a real Gemini Enterprise deep link
before this module is considered done: Compute's `HttpRedirectAction` has no
field for a query string, only host and path. A deep link that requires query
parameters cannot be expressed by a pure LB-level redirect; `validate_target_url`
below refuses those up front rather than silently producing a mapping that loses
required parameters.

Requires the runtime service account to hold `roles/compute.loadBalancerAdmin`
(project-scoped - see terraform/main.tf's `enable_url_mapping_module` variable
and the accepted blast-radius note there).
"""
import logging
from typing import Any, Dict
from urllib.parse import urlparse

from google.api_core.exceptions import NotFound
from google.api_core.extended_operation import ExtendedOperation
from google.cloud import compute_v1

logger = logging.getLogger("gemini_provisioner.url_mapping.compute_client")

_OPERATION_TIMEOUT = 300

# Cert statuses that mean "still working on it" vs. terminal failure states.
# (google-cloud-compute's actual enum - NOT "FAILED_NOT_VISIBLE" /
# "FAILED_RATE_LIMITED", which don't exist in this API version.)
CERT_STATUS_ACTIVE = "ACTIVE"
CERT_STATUSES_IN_PROGRESS = {"PROVISIONING", "MANAGED_CERTIFICATE_STATUS_UNSPECIFIED"}
CERT_STATUSES_FAILED = {"PROVISIONING_FAILED", "PROVISIONING_FAILED_PERMANENTLY", "RENEWAL_FAILED"}


def _wait(operation: ExtendedOperation):
    result = operation.result(timeout=_OPERATION_TIMEOUT)
    if getattr(operation, "error_code", None):
        raise RuntimeError(f"Compute operation failed [{operation.error_code}]: {operation.error_message}")
    return result


def resource_names(mapping_id: str) -> Dict[str, str]:
    prefix = f"ge-urlmap-{mapping_id}"
    return {
        "address_name": f"{prefix}-ip",
        "ssl_cert_name": f"{prefix}-cert",
        "url_map_name": f"{prefix}-map",
        "target_https_proxy_name": f"{prefix}-https-proxy",
        "forwarding_rule_name": f"{prefix}-fr",
        "http_url_map_name": f"{prefix}-http-map",
        "target_http_proxy_name": f"{prefix}-http-proxy",
        "http_forwarding_rule_name": f"{prefix}-http-fr",
    }


def validate_target_url(target_deep_link: str) -> None:
    """Raise ValueError if the target can't be expressed by a pure LB-level
    redirect (see module docstring)."""
    parsed = urlparse(target_deep_link)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Not an absolute URL: {target_deep_link!r}")
    if parsed.query:
        raise ValueError(
            "The target deep link includes a query string, which Compute's URL map "
            "redirect cannot express (it only redirects host and path). Use a deep "
            "link without query parameters, or front this with a small redirecting "
            "backend service instead of a pure load-balancer-level redirect."
        )


def create_static_ip(project_id: str, name: str) -> str:
    """Reserve a global static external IPv4 address; returns the address string."""
    client = compute_v1.GlobalAddressesClient()
    address_resource = compute_v1.Address(name=name, ip_version="IPV4", address_type="EXTERNAL")
    _wait(client.insert(project=project_id, address_resource=address_resource))
    return client.get(project=project_id, address=name).address


def create_managed_certificate(project_id: str, name: str, domain: str) -> None:
    client = compute_v1.SslCertificatesClient()
    cert_resource = compute_v1.SslCertificate(
        name=name,
        type_="MANAGED",
        managed=compute_v1.SslCertificateManagedSslCertificate(domains=[domain]),
    )
    _wait(client.insert(project=project_id, ssl_certificate_resource=cert_resource))


def get_certificate_status(project_id: str, name: str) -> str:
    """One of ACTIVE / PROVISIONING / PROVISIONING_FAILED /
    PROVISIONING_FAILED_PERMANENTLY / RENEWAL_FAILED / MANAGED_CERTIFICATE_STATUS_UNSPECIFIED."""
    client = compute_v1.SslCertificatesClient()
    cert = client.get(project=project_id, ssl_certificate=name)
    status = cert.managed.status
    return getattr(status, "name", str(status))


def create_https_redirect_url_map(project_id: str, name: str, target_deep_link: str) -> None:
    parsed = urlparse(target_deep_link)
    client = compute_v1.UrlMapsClient()
    url_map_resource = compute_v1.UrlMap(
        name=name,
        default_url_redirect=compute_v1.HttpRedirectAction(
            host_redirect=parsed.netloc,
            path_redirect=parsed.path or "/",
            redirect_response_code="MOVED_PERMANENTLY_DEFAULT",
            strip_query=False,
            https_redirect=False,
        ),
    )
    _wait(client.insert(project=project_id, url_map_resource=url_map_resource))


def create_http_to_https_redirect_map(project_id: str, name: str) -> None:
    client = compute_v1.UrlMapsClient()
    url_map_resource = compute_v1.UrlMap(
        name=name,
        default_url_redirect=compute_v1.HttpRedirectAction(
            https_redirect=True,
            redirect_response_code="MOVED_PERMANENTLY_DEFAULT",
            strip_query=False,
        ),
    )
    _wait(client.insert(project=project_id, url_map_resource=url_map_resource))


def create_target_https_proxy(project_id: str, name: str, url_map_name: str, ssl_cert_name: str) -> None:
    client = compute_v1.TargetHttpsProxiesClient()
    proxy_resource = compute_v1.TargetHttpsProxy(
        name=name,
        url_map=f"projects/{project_id}/global/urlMaps/{url_map_name}",
        ssl_certificates=[f"projects/{project_id}/global/sslCertificates/{ssl_cert_name}"],
    )
    _wait(client.insert(project=project_id, target_https_proxy_resource=proxy_resource))


def create_target_http_proxy(project_id: str, name: str, url_map_name: str) -> None:
    client = compute_v1.TargetHttpProxiesClient()
    proxy_resource = compute_v1.TargetHttpProxy(
        name=name,
        url_map=f"projects/{project_id}/global/urlMaps/{url_map_name}",
    )
    _wait(client.insert(project=project_id, target_http_proxy_resource=proxy_resource))


def create_global_forwarding_rule(
    project_id: str, name: str, ip_address: str, target_proxy_name: str,
    port_range: str, proxy_kind: str,
) -> None:
    client = compute_v1.GlobalForwardingRulesClient()
    target = (
        f"projects/{project_id}/global/targetHttpsProxies/{target_proxy_name}"
        if proxy_kind == "https"
        else f"projects/{project_id}/global/targetHttpProxies/{target_proxy_name}"
    )
    rule_resource = compute_v1.ForwardingRule(
        name=name,
        I_p_address=ip_address,
        I_p_protocol="TCP",
        port_range=port_range,
        target=target,
        load_balancing_scheme="EXTERNAL_MANAGED",
    )
    _wait(client.insert(project=project_id, forwarding_rule_resource=rule_resource))


def provision_mapping(project_id: str, mapping_id: str, custom_domain: str, target_deep_link: str) -> Dict[str, Any]:
    """Create every GCP resource for one mapping, in dependency order. Raises on
    the first failure - the caller should persist whatever's in `resource_names`
    as the mapping's `gcp_resources` regardless, so `teardown_mapping` can clean
    up any partial state."""
    validate_target_url(target_deep_link)
    names = resource_names(mapping_id)

    reserved_ip = create_static_ip(project_id, names["address_name"])
    create_managed_certificate(project_id, names["ssl_cert_name"], custom_domain)
    create_https_redirect_url_map(project_id, names["url_map_name"], target_deep_link)
    create_target_https_proxy(
        project_id, names["target_https_proxy_name"], names["url_map_name"], names["ssl_cert_name"]
    )
    create_global_forwarding_rule(
        project_id, names["forwarding_rule_name"], reserved_ip,
        names["target_https_proxy_name"], port_range="443", proxy_kind="https",
    )

    # HTTP -> HTTPS redirect stack, sharing the same reserved IP.
    create_http_to_https_redirect_map(project_id, names["http_url_map_name"])
    create_target_http_proxy(project_id, names["target_http_proxy_name"], names["http_url_map_name"])
    create_global_forwarding_rule(
        project_id, names["http_forwarding_rule_name"], reserved_ip,
        names["target_http_proxy_name"], port_range="80", proxy_kind="http",
    )

    return {**names, "reserved_ip": reserved_ip, "cert_status": "PROVISIONING"}


def teardown_mapping(project_id: str, gcp_resources: Dict[str, str]) -> None:
    """Delete every resource for a mapping, in dependency order (forwarding
    rules and proxies before the url maps/cert/address they reference).
    Tolerates already-deleted (404) resources so a retry after a partial
    failure is safe."""

    def _delete(client, **kwargs):
        try:
            _wait(client.delete(**kwargs))
        except NotFound:
            pass

    if gcp_resources.get("http_forwarding_rule_name"):
        _delete(compute_v1.GlobalForwardingRulesClient(), project=project_id,
                forwarding_rule=gcp_resources["http_forwarding_rule_name"])
    if gcp_resources.get("forwarding_rule_name"):
        _delete(compute_v1.GlobalForwardingRulesClient(), project=project_id,
                forwarding_rule=gcp_resources["forwarding_rule_name"])
    if gcp_resources.get("target_http_proxy_name"):
        _delete(compute_v1.TargetHttpProxiesClient(), project=project_id,
                target_http_proxy=gcp_resources["target_http_proxy_name"])
    if gcp_resources.get("target_https_proxy_name"):
        _delete(compute_v1.TargetHttpsProxiesClient(), project=project_id,
                target_https_proxy=gcp_resources["target_https_proxy_name"])
    if gcp_resources.get("http_url_map_name"):
        _delete(compute_v1.UrlMapsClient(), project=project_id, url_map=gcp_resources["http_url_map_name"])
    if gcp_resources.get("url_map_name"):
        _delete(compute_v1.UrlMapsClient(), project=project_id, url_map=gcp_resources["url_map_name"])
    if gcp_resources.get("ssl_cert_name"):
        _delete(compute_v1.SslCertificatesClient(), project=project_id,
                ssl_certificate=gcp_resources["ssl_cert_name"])
    if gcp_resources.get("address_name"):
        _delete(compute_v1.GlobalAddressesClient(), project=project_id,
                address=gcp_resources["address_name"])

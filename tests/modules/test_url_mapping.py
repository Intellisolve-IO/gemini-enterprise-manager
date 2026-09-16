import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest
from fastapi.testclient import TestClient
from google.api_core.exceptions import NotFound

from app.main import app
from app.modules.url_mapping import compute_client

client = TestClient(app)


# ---------------------------------------------------------------------------
# validate_target_url
# ---------------------------------------------------------------------------
def test_validate_target_url_accepts_clean_url():
    compute_client.validate_target_url("https://geminienterprise.google.com/app/123")


def test_validate_target_url_rejects_query_string():
    with pytest.raises(ValueError, match="query string"):
        compute_client.validate_target_url("https://geminienterprise.google.com/app?x=1")


def test_validate_target_url_rejects_relative_url():
    with pytest.raises(ValueError, match="absolute"):
        compute_client.validate_target_url("/app/123")


# ---------------------------------------------------------------------------
# compute_client: individual resource builders (mocked compute_v1 clients)
# ---------------------------------------------------------------------------
def _op(error_code=None):
    op = MagicMock()
    op.error_code = error_code
    op.result.return_value = None
    return op


def test_create_static_ip_builds_correct_request_and_returns_address():
    mock_client = MagicMock()
    mock_client.insert.return_value = _op()
    mock_client.get.return_value = MagicMock(address="34.1.2.3")
    with patch("app.modules.url_mapping.compute_client.compute_v1.GlobalAddressesClient", return_value=mock_client):
        ip = compute_client.create_static_ip("proj", "ge-urlmap-abc-ip")
    assert ip == "34.1.2.3"
    _, kwargs = mock_client.insert.call_args
    assert kwargs["project"] == "proj"
    assert kwargs["address_resource"].name == "ge-urlmap-abc-ip"
    assert kwargs["address_resource"].address_type == "EXTERNAL"


def test_create_managed_certificate_builds_correct_request():
    mock_client = MagicMock()
    mock_client.insert.return_value = _op()
    with patch("app.modules.url_mapping.compute_client.compute_v1.SslCertificatesClient", return_value=mock_client):
        compute_client.create_managed_certificate("proj", "ge-urlmap-abc-cert", "ai.example.com")
    _, kwargs = mock_client.insert.call_args
    cert = kwargs["ssl_certificate_resource"]
    assert cert.type_ == "MANAGED"
    assert list(cert.managed.domains) == ["ai.example.com"]


def test_get_certificate_status_returns_enum_name():
    mock_client = MagicMock()
    mock_cert = MagicMock()
    mock_cert.managed.status.name = "PROVISIONING"
    mock_client.get.return_value = mock_cert
    with patch("app.modules.url_mapping.compute_client.compute_v1.SslCertificatesClient", return_value=mock_client):
        status = compute_client.get_certificate_status("proj", "ge-urlmap-abc-cert")
    assert status == "PROVISIONING"


def test_wait_raises_on_operation_error():
    op = _op(error_code=13)
    op.error_message = "boom"
    with pytest.raises(RuntimeError, match="boom"):
        compute_client._wait(op)


def test_create_https_redirect_url_map_splits_host_and_path():
    mock_client = MagicMock()
    mock_client.insert.return_value = _op()
    with patch("app.modules.url_mapping.compute_client.compute_v1.UrlMapsClient", return_value=mock_client):
        compute_client.create_https_redirect_url_map(
            "proj", "ge-urlmap-abc-map", "https://geminienterprise.google.com/apps/123"
        )
    _, kwargs = mock_client.insert.call_args
    redirect = kwargs["url_map_resource"].default_url_redirect
    assert redirect.host_redirect == "geminienterprise.google.com"
    assert redirect.path_redirect == "/apps/123"
    assert redirect.strip_query is False


def test_create_global_forwarding_rule_targets_https_proxy():
    mock_client = MagicMock()
    mock_client.insert.return_value = _op()
    with patch("app.modules.url_mapping.compute_client.compute_v1.GlobalForwardingRulesClient",
              return_value=mock_client):
        compute_client.create_global_forwarding_rule(
            "proj", "ge-urlmap-abc-fr", "34.1.2.3", "ge-urlmap-abc-https-proxy",
            port_range="443", proxy_kind="https",
        )
    _, kwargs = mock_client.insert.call_args
    rule = kwargs["forwarding_rule_resource"]
    assert rule.I_p_address == "34.1.2.3"
    assert rule.port_range == "443"
    assert "targetHttpsProxies/ge-urlmap-abc-https-proxy" in rule.target
    assert rule.load_balancing_scheme == "EXTERNAL_MANAGED"


# ---------------------------------------------------------------------------
# provision_mapping / teardown_mapping orchestration
# ---------------------------------------------------------------------------
def test_provision_mapping_calls_every_step_in_order():
    calls = []
    with patch("app.modules.url_mapping.compute_client.create_static_ip",
              side_effect=lambda *a: (calls.append("ip"), "34.1.2.3")[1]), \
         patch("app.modules.url_mapping.compute_client.create_managed_certificate",
              side_effect=lambda *a: calls.append("cert")), \
         patch("app.modules.url_mapping.compute_client.create_https_redirect_url_map",
              side_effect=lambda *a: calls.append("map")), \
         patch("app.modules.url_mapping.compute_client.create_target_https_proxy",
              side_effect=lambda *a: calls.append("https_proxy")), \
         patch("app.modules.url_mapping.compute_client.create_http_to_https_redirect_map",
              side_effect=lambda *a: calls.append("http_map")), \
         patch("app.modules.url_mapping.compute_client.create_target_http_proxy",
              side_effect=lambda *a: calls.append("http_proxy")), \
         patch("app.modules.url_mapping.compute_client.create_global_forwarding_rule",
              side_effect=lambda *a, **kw: calls.append(f"fr-{kw.get('proxy_kind')}")):
        result = compute_client.provision_mapping("proj", "abc123", "ai.example.com",
                                                   "https://geminienterprise.google.com/app")
    assert calls == ["ip", "cert", "map", "https_proxy", "fr-https", "http_map", "http_proxy", "fr-http"]
    assert result["reserved_ip"] == "34.1.2.3"
    assert result["cert_status"] == "PROVISIONING"


def test_provision_mapping_rejects_query_string_before_creating_anything():
    with patch("app.modules.url_mapping.compute_client.create_static_ip") as mock_ip:
        with pytest.raises(ValueError):
            compute_client.provision_mapping("proj", "abc123", "ai.example.com",
                                             "https://geminienterprise.google.com/app?x=1")
    mock_ip.assert_not_called()


def test_teardown_mapping_tolerates_already_deleted_resources():
    resources = {"address_name": "ip1", "ssl_cert_name": "cert1", "url_map_name": "map1",
                 "target_https_proxy_name": "proxy1", "forwarding_rule_name": "fr1"}
    mock_client = MagicMock()
    mock_client.delete.side_effect = NotFound("gone")
    with patch("app.modules.url_mapping.compute_client.compute_v1.GlobalAddressesClient", return_value=mock_client), \
         patch("app.modules.url_mapping.compute_client.compute_v1.SslCertificatesClient", return_value=mock_client), \
         patch("app.modules.url_mapping.compute_client.compute_v1.UrlMapsClient", return_value=mock_client), \
         patch("app.modules.url_mapping.compute_client.compute_v1.TargetHttpsProxiesClient", return_value=mock_client), \
         patch("app.modules.url_mapping.compute_client.compute_v1.GlobalForwardingRulesClient", return_value=mock_client):
        compute_client.teardown_mapping("proj", resources)  # must not raise


# ---------------------------------------------------------------------------
# Router / integration
# ---------------------------------------------------------------------------
class _FakeStore:
    """Minimal in-memory stand-in for the module_config Firestore doc."""

    def __init__(self):
        self.mappings = []

    def get_module_config(self, module_id, defaults):
        return {**defaults, "mappings": self.mappings}

    def update_module_config(self, module_id, updates, defaults):
        if "mappings" in updates:
            self.mappings = updates["mappings"]
        return {**defaults, **updates}


def test_url_mapping_page_disabled_redirects():
    with patch("app.core.module_auth.is_module_enabled", return_value=False):
        r = client.get("/modules/url-mapping", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/?disabled_module=url-mapping"


def test_create_mapping_rejects_query_string():
    with patch("app.core.module_auth.is_module_enabled", return_value=True):
        r = client.post("/modules/url-mapping/api/mappings", json={
            "custom_domain": "ai.example.com",
            "target_deep_link": "https://geminienterprise.google.com/app?x=1",
        })
    assert r.status_code == 400


def test_create_mapping_success_records_active_pending_status():
    store = _FakeStore()
    fake_resources = {"address_name": "a", "reserved_ip": "34.1.2.3", "ssl_cert_name": "c",
                      "cert_status": "PROVISIONING"}
    with patch("app.core.module_auth.is_module_enabled", return_value=True), \
         patch("app.modules.url_mapping.router.get_module_config", side_effect=store.get_module_config), \
         patch("app.modules.url_mapping.router.update_module_config", side_effect=store.update_module_config), \
         patch("app.modules.url_mapping.router.compute_client.provision_mapping", return_value=fake_resources):
        r = client.post("/modules/url-mapping/api/mappings", json={
            "custom_domain": "ai.example.com",
            "target_deep_link": "https://geminienterprise.google.com/app",
        })
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert data["mapping"]["status"] == "provisioning"
    assert data["mapping"]["gcp_resources"]["reserved_ip"] == "34.1.2.3"
    assert len(store.mappings) == 1


def test_create_mapping_records_failure_on_provisioning_error():
    store = _FakeStore()
    with patch("app.core.module_auth.is_module_enabled", return_value=True), \
         patch("app.modules.url_mapping.router.get_module_config", side_effect=store.get_module_config), \
         patch("app.modules.url_mapping.router.update_module_config", side_effect=store.update_module_config), \
         patch("app.modules.url_mapping.router.compute_client.provision_mapping",
              side_effect=RuntimeError("quota exceeded")):
        r = client.post("/modules/url-mapping/api/mappings", json={
            "custom_domain": "ai.example.com",
            "target_deep_link": "https://geminienterprise.google.com/app",
        })
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is False
    assert data["mapping"]["status"] == "failed"
    assert "quota exceeded" in data["mapping"]["last_error"]


def test_refresh_mapping_updates_status_to_active():
    store = _FakeStore()
    store.mappings = [{
        "id": "m1", "custom_domain": "ai.example.com", "target_deep_link": "https://x/y",
        "created_by": "a@b.com", "created_at": "t", "status": "provisioning",
        "gcp_resources": {"ssl_cert_name": "cert1"}, "last_checked_at": None, "last_error": None,
    }]
    with patch("app.core.module_auth.is_module_enabled", return_value=True), \
         patch("app.modules.url_mapping.router.get_module_config", side_effect=store.get_module_config), \
         patch("app.modules.url_mapping.router.update_module_config", side_effect=store.update_module_config), \
         patch("app.modules.url_mapping.router.compute_client.get_certificate_status", return_value="ACTIVE"):
        r = client.post("/modules/url-mapping/api/mappings/m1/refresh")
    assert r.status_code == 200
    assert r.json()["mapping"]["status"] == "active"


def test_refresh_mapping_404_for_unknown_id():
    store = _FakeStore()
    with patch("app.core.module_auth.is_module_enabled", return_value=True), \
         patch("app.modules.url_mapping.router.get_module_config", side_effect=store.get_module_config):
        r = client.post("/modules/url-mapping/api/mappings/does-not-exist/refresh")
    assert r.status_code == 404


def test_delete_mapping_success():
    store = _FakeStore()
    store.mappings = [{
        "id": "m1", "custom_domain": "ai.example.com", "target_deep_link": "https://x/y",
        "created_by": "a@b.com", "created_at": "t", "status": "active",
        "gcp_resources": {}, "last_checked_at": None, "last_error": None,
    }]
    with patch("app.core.module_auth.is_module_enabled", return_value=True), \
         patch("app.modules.url_mapping.router.get_module_config", side_effect=store.get_module_config), \
         patch("app.modules.url_mapping.router.update_module_config", side_effect=store.update_module_config), \
         patch("app.modules.url_mapping.router.compute_client.teardown_mapping"):
        r = client.post("/modules/url-mapping/api/mappings/m1/delete")
    assert r.status_code == 200
    assert store.mappings == []


def test_delete_mapping_404_for_unknown_id():
    store = _FakeStore()
    with patch("app.core.module_auth.is_module_enabled", return_value=True), \
         patch("app.modules.url_mapping.router.get_module_config", side_effect=store.get_module_config):
        r = client.post("/modules/url-mapping/api/mappings/does-not-exist/delete")
    assert r.status_code == 404

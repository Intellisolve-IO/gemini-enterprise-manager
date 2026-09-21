"""Read-only IAM/service-usage introspection for the Health Check module.

Uses the same generic discovery-based `googleapiclient.discovery.build` pattern
already in use for Workspace APIs (`app/workspace_client.py`), rather than adding
new generated-client dependencies (`google-cloud-resource-manager`,
`google-cloud-iam`) for what is only a handful of read-only calls.

Every function takes an optional `target_sa_email` - the environment's own
tenant-owned service account to impersonate, via
`app/core/tenant_credentials.py`, so the audit reflects the identity that
actually performs this environment's work. Falls back to the central app's
own identity when not set (tenant-zero).

Requires the acting service account to hold `roles/iam.securityReviewer` (IAM
policy reads) and `roles/serviceusage.serviceUsageViewer` (enabled-API reads) -
see terraform/main.tf.
"""
import logging
from typing import Any, Dict, List, Optional

from googleapiclient.discovery import build

from app.core import tenant_credentials

logger = logging.getLogger("gemini_provisioner.health_check.iam_client")

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def _credentials(target_sa_email: Optional[str] = None):
    return tenant_credentials.build_credentials(target_sa_email, _SCOPES)


def get_project_iam_bindings(project_id: str, target_sa_email: Optional[str] = None) -> List[Dict[str, Any]]:
    """All IAM policy bindings on the project (role -> members)."""
    service = build("cloudresourcemanager", "v1", credentials=_credentials(target_sa_email), cache_discovery=False)
    policy = service.projects().getIamPolicy(resource=project_id, body={}).execute()
    return policy.get("bindings", [])


def get_service_account_iam_bindings(sa_email: str, target_sa_email: Optional[str] = None) -> List[Dict[str, Any]]:
    """IAM policy bindings on a service account *resource itself* - used to check
    self-impersonation (roles/iam.serviceAccountTokenCreator granted on itself).
    `sa_email` is the resource being inspected; `target_sa_email` is the
    (possibly different) identity doing the inspecting."""
    resource = f"projects/-/serviceAccounts/{sa_email}"
    service = build("iam", "v1", credentials=_credentials(target_sa_email), cache_discovery=False)
    policy = service.projects().serviceAccounts().getIamPolicy(resource=resource).execute()
    return policy.get("bindings", [])


def list_enabled_services(project_id: str, target_sa_email: Optional[str] = None) -> List[str]:
    """Enabled API service names (e.g. "run.googleapis.com") for the project."""
    service = build("serviceusage", "v1", credentials=_credentials(target_sa_email), cache_discovery=False)
    names: List[str] = []
    request = service.services().list(
        parent=f"projects/{project_id}", filter="state:ENABLED", pageSize=200
    )
    while request is not None:
        response = request.execute()
        for item in response.get("services", []):
            config_name = (item.get("config") or {}).get("name")
            names.append(config_name or item.get("name", "").rsplit("/", 1)[-1])
        request = service.services().list_next(previous_request=request, previous_response=response)
    return names

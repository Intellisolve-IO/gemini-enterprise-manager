"""Cross-project service account impersonation for the multi-tenant
conversion: the mechanism behind decision #3 in the approved plan (each
tenant owns their own service account; the central app only ever holds a
narrow, revocable impersonation right on it, never a standing broad
credential or domain-wide trust).

One function covers both cases needed:
- Plain SA-to-SA impersonation (Discovery Engine, Compute, Service Usage,
  Resource Manager - no Workspace subject): `build_credentials(sa_email, scopes)`.
- Domain-Wide-Delegation-via-impersonation (Workspace Directory/Gmail):
  `build_credentials(sa_email, scopes, subject=delegated_admin_email)`.

Verified by reading `google.auth.impersonated_credentials.Credentials`'s
source directly (google-auth 2.58.0), not assumed from documentation: passing
`subject` makes it internally sign a JWT asserting that subject via the IAM
Credentials API (the same signBlob-based flow `app/workspace_client.py`'s
original hand-rolled `iam.Signer` + `service_account.Credentials(subject=...)`
implementation used) and exchange it for a delegated token - one class
correctly handles both cases; no need to hand-roll the signer ourselves or
maintain two separate code paths.

Both paths require the CENTRAL app's own runtime service account to hold
`roles/iam.serviceAccountTokenCreator` on the target service account -
granted to itself today (Terraform's `sa_token_creator_self`, for tenant-zero
and any environment that hasn't configured its own tenant-owned SA yet), and
expected to be granted by each tenant on their own service account once the
onboarding wizard (a later phase) walks them through it.
"""
import logging
from typing import Any, Dict, List, Optional

import google.auth
from google.auth import impersonated_credentials

from app.config import settings

logger = logging.getLogger("gemini_provisioner.tenant_credentials")

_CLOUD_PLATFORM_SCOPE = ["https://www.googleapis.com/auth/cloud-platform"]


def _source_credentials():
    """The central app's own ambient credentials - the identity every
    impersonation call starts from."""
    creds, _ = google.auth.default(scopes=_CLOUD_PLATFORM_SCOPE)
    return creds


def project_id_for(environment: Dict[str, Any]) -> str:
    """The GCP project a call against `environment` should target: the
    environment's own configured project, or the central app's own project
    when it hasn't been configured yet (tenant-zero)."""
    return environment.get("gcp_project_id") or settings.GCP_PROJECT_ID


def build_credentials(
    target_sa_email: Optional[str],
    scopes: List[str],
    subject: Optional[str] = None,
    lifetime: int = 3600,
):
    """Credentials acting as `target_sa_email`. Falls back to the central
    app's own configured runtime service account (self-impersonation) when
    `target_sa_email` is falsy - the correct behavior for tenant-zero and any
    environment that hasn't registered its own service account yet.

    Pass `subject` (a Workspace user to impersonate) for Domain-Wide
    Delegation; omit it for plain Cloud API calls.
    """
    effective_target = target_sa_email or settings.RUNTIME_SERVICE_ACCOUNT_EMAIL
    if not effective_target:
        if subject:
            raise RuntimeError(
                "Domain-Wide Delegation requires a concrete service account to "
                "impersonate. Set RUNTIME_SERVICE_ACCOUNT_EMAIL (the central app's "
                "own identity) or configure this environment's own service account."
            )
        # No impersonation target at all - hand back the source credentials
        # unmodified (local dev with no runtime SA configured).
        return _source_credentials()

    return impersonated_credentials.Credentials(
        source_credentials=_source_credentials(),
        target_principal=effective_target,
        target_scopes=scopes,
        subject=subject,
        lifetime=lifetime,
    )


def test_tenant_impersonation(target_sa_email: str, project_id: str) -> Dict[str, Any]:
    """Onboarding "test connection" step: build impersonated credentials
    targeting `target_sa_email` and make one trivial authenticated call (list
    up to one enabled service in `project_id`) to confirm the IAM binding is
    live. Returns the same {success, message, subject, guidance} shape as
    `WorkspaceClient.test_dwd_connection`, so callers can reuse the existing
    result-card UI verbatim."""
    from googleapiclient.discovery import build

    try:
        creds = build_credentials(target_sa_email, _CLOUD_PLATFORM_SCOPE)
        service = build("serviceusage", "v1", credentials=creds, cache_discovery=False)
        service.services().list(
            parent=f"projects/{project_id}", filter="state:ENABLED", pageSize=1
        ).execute()
        return {
            "success": True,
            "message": f"Successfully impersonated {target_sa_email} and reached project {project_id}.",
            "subject": target_sa_email,
        }
    except Exception as e:
        logger.error("Tenant impersonation test failed for %s (project %s): %s",
                      target_sa_email, project_id, e)
        guidance = (
            "Confirm the central app's runtime service account "
            f"({settings.RUNTIME_SERVICE_ACCOUNT_EMAIL or '(not set)'}) has "
            f"roles/iam.serviceAccountTokenCreator on {target_sa_email}, and that "
            f"{target_sa_email} itself has roles/serviceusage.serviceUsageViewer (or "
            f"broader) on project {project_id}. Cross-project IAM bindings can take a "
            "few minutes to propagate after granting them."
        )
        return {"success": False, "message": str(e), "subject": target_sa_email, "guidance": guidance}

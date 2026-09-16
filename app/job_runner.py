"""Batch entrypoint for the scheduled license sync.

Run as a **Cloud Run Job** (``python -m app.job_runner``), triggered by Cloud
Scheduler through the Cloud Run Admin API.

NOTE - a known, tracked Phase 1 limitation: this still runs sync for exactly
ONE designated tenant/environment (named by SCHEDULED_SYNC_TENANT_ID /
SCHEDULED_SYNC_ENVIRONMENT_ID), not a fan-out across every tenant's
environments. Replacing this with a tick job that scans all due environments
is Phase 5 of the multi-tenant conversion.

``POST .../api/sync/run`` on the web service still exists for the manual
"Run sync now" button in each environment's dashboard.

Exit code: ``0`` on SUCCESS / PARTIAL_SUCCESS, ``1`` on FAILED or an
unhandled exception, so a bad run is surfaced as a failed job execution.
"""
import logging
import sys

from app.config import settings
from app.sync_worker import run_license_sync

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gemini_provisioner.job_runner")


def main() -> int:
    tenant_id = settings.SCHEDULED_SYNC_TENANT_ID
    environment_id = settings.SCHEDULED_SYNC_ENVIRONMENT_ID
    if not tenant_id or not environment_id:
        logger.critical(
            "SCHEDULED_SYNC_TENANT_ID / SCHEDULED_SYNC_ENVIRONMENT_ID are not set - "
            "nothing to run. Set them to the tenant/environment this scheduled job "
            "should sync (see app/job_runner.py's module docstring)."
        )
        return 1

    logger.info("Cloud Run Job: starting scheduled license sync for tenant=%s environment=%s",
                tenant_id, environment_id)
    try:
        result = run_license_sync(tenant_id, environment_id, triggered_by="scheduled")
    except Exception:  # noqa: BLE001 - top-level guard, want the traceback in logs
        logger.critical("Scheduled license sync crashed", exc_info=True)
        return 1

    status = str((result or {}).get("status", "")).upper()
    logger.info(
        "Scheduled license sync finished: status=%s assigned=%s already_held=%s errors=%s",
        status or "UNKNOWN",
        (result or {}).get("licenses_assigned_count"),
        (result or {}).get("licenses_already_held_count"),
        (result or {}).get("errors_count"),
    )
    if status == "FAILED":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

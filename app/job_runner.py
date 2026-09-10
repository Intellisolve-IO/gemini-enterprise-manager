"""Batch entrypoint for the scheduled license sync.

Run as a **Cloud Run Job** (``python -m app.job_runner``), triggered by Cloud
Scheduler through the Cloud Run Admin API. This path never touches the
IAP-protected HTTP service, so it needs no IAP assertion and no
``SYNC_INVOKER_SA_EMAIL`` allow-list - the scheduler just needs
``roles/run.invoker`` on the job.

``POST /api/sync/run`` on the web service still exists for the manual
"Run sync now" button in the dashboard (authorized by IAP + super-admin).

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
    logger.info("Cloud Run Job: starting scheduled license sync")
    try:
        result = run_license_sync(triggered_by="scheduled")
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

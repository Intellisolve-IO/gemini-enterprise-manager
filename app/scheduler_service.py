import logging
from typing import Dict, Any, Optional
from google.cloud import scheduler_v1
from google.protobuf import field_mask_pb2

from app.config import settings

logger = logging.getLogger("gemini_provisioner.scheduler")


class SchedulerService:
    """Client for querying and updating Cloud Scheduler triggers programmatically."""

    def __init__(self):
        self.project_id = settings.GCP_PROJECT_ID
        self.location = settings.CLOUD_SCHEDULER_LOCATION
        self.job_name = settings.CLOUD_SCHEDULER_JOB_NAME
        self.client: Optional[scheduler_v1.CloudSchedulerClient] = None

    def _get_client(self) -> scheduler_v1.CloudSchedulerClient:
        if self.client is None:
            self.client = scheduler_v1.CloudSchedulerClient()
        return self.client

    def get_job_path(self) -> str:
        client = self._get_client()
        return client.job_path(self.project_id, self.location, self.job_name)

    def get_schedule(self) -> Dict[str, Any]:
        """Fetch current schedule string and state from Cloud Scheduler."""
        try:
            client = self._get_client()
            job_path = self.get_job_path()
            job = client.get_job(name=job_path)
            return {
                "available": True,
                "job_name": self.job_name,
                "schedule": job.schedule,
                "time_zone": job.time_zone or "UTC",
                "state": scheduler_v1.Job.State(job.state).name,
            }
        except Exception as e:
            logger.warning("Unable to fetch Cloud Scheduler job '%s': %s", self.job_name, e)
            return {
                "available": False,
                "job_name": self.job_name,
                "schedule": None,
                "time_zone": "UTC",
                "error": str(e),
            }

    def update_schedule(self, new_cron_expression: str) -> Dict[str, Any]:
        """Update Cloud Scheduler job trigger schedule."""
        try:
            client = self._get_client()
            job_path = self.get_job_path()
            
            job = scheduler_v1.Job(
                name=job_path,
                schedule=new_cron_expression
            )
            update_mask = field_mask_pb2.FieldMask(paths=["schedule"])
            
            updated_job = client.update_job(job=job, update_mask=update_mask)
            logger.info("Successfully updated Cloud Scheduler job schedule to '%s'", new_cron_expression)
            return {
                "success": True,
                "schedule": updated_job.schedule,
                "message": f"Cloud Scheduler updated to: {updated_job.schedule}"
            }
        except Exception as e:
            logger.error("Failed to update Cloud Scheduler job '%s': %s", self.job_name, e)
            return {
                "success": False,
                "error": str(e),
                "message": f"Cloud Scheduler update failed: {str(e)}"
            }

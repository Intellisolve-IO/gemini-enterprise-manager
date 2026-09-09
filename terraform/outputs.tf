output "cloud_run_url" {
  description = "The public URL of the deployed Admin Web UI & Sync service."
  value       = google_cloud_run_v2_service.provisioner.uri
}

output "app_service_account_email" {
  description = "The Service Account email used by Cloud Run."
  value       = google_service_account.app_sa.email
}

output "app_service_account_client_id" {
  description = "The unique Client ID of the Service Account (enter this in Google Workspace Admin Console for DWD)."
  value       = google_service_account.app_sa.unique_id
}

output "scheduler_job_name" {
  description = "The Cloud Scheduler job name."
  value       = google_cloud_scheduler_job.sync_job.name
}

output "artifact_registry_repo" {
  description = "The Docker repository URL for pushing images."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docker_repo.name}"
}

output "workload_identity_provider" {
  description = "Workload Identity Provider resource name for GitHub Actions."
  value       = length(google_iam_workload_identity_pool_provider.github_provider) > 0 ? google_iam_workload_identity_pool_provider.github_provider[0].name : "N/A (Provide github_repo in variables to generate)"
}

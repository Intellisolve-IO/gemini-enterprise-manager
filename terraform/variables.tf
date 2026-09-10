# ─────────────────────────────────────────────────────────────────────────────────
# Fill these in via terraform.tfvars, -var flags, or TF_VAR_* env vars.
# Nothing here is tied to a specific GCP project or Workspace domain.
# ─────────────────────────────────────────────────────────────────────────────────

variable "project_id" {
  description = "The GCP Project ID where resources will be provisioned. Required."
  type        = string
  # No default on purpose: set it explicitly so nothing environment-specific is baked in.
}

variable "region" {
  description = "GCP region for Cloud Run, Cloud Scheduler, and Artifact Registry."
  type        = string
  default     = "us-central1"
}

variable "service_name" {
  description = "Name of the Cloud Run service."
  type        = string
  default     = "gemini-license-provisioner"
}

variable "app_service_account_id" {
  description = "Account ID (the part before '@') of the service account that runs Cloud Run and is impersonated by CI/CD."
  type        = string
  default     = "sa-gemini-provisioner"
}

variable "scheduler_service_account_id" {
  description = "Account ID of the service account Cloud Scheduler uses to invoke the sync endpoint via OIDC."
  type        = string
  default     = "sa-scheduler-invoker"
}

variable "artifact_repository_id" {
  description = "Artifact Registry repository ID that holds the container image."
  type        = string
  default     = "gemini-provisioner-docker"
}

variable "scheduler_job_name" {
  description = "Name of the Cloud Scheduler job that triggers periodic license sync."
  type        = string
  default     = "gemini-license-sync-job"
}

variable "delegated_admin_email" {
  description = "Real, active, licensed Google Workspace admin user to impersonate via Domain-Wide Delegation (e.g. workspace-admin@your-domain.com). Must resolve to an existing user or the sync fails with 'invalid_grant: Invalid email or User ID'."
  type        = string
  default     = "workspace-admin@your-domain.com"
}

variable "notification_sender_email" {
  description = "Mailbox to send sync-run notification emails as (Gmail API + DWD). Empty = use delegated_admin_email. Requires the gmail.send scope on the DWD entry."
  type        = string
  default     = ""
}

variable "public_base_url" {
  description = "Public https base URL of the service, used for links in notification emails. Empty = the app learns it from web traffic."
  type        = string
  default     = ""
}

variable "product_id" {
  description = "Google Workspace Product ID for license assignment (e.g. Google-Apps or 101047)."
  type        = string
  default     = "Google-Apps"
}

variable "sku_id" {
  description = "Google Workspace SKU ID for Gemini Enterprise (e.g. 101031 or 1010470001)."
  type        = string
  default     = "101031"
}

variable "initial_cron_expression" {
  description = "Initial cron frequency for Cloud Scheduler (UTC)."
  type        = string
  default     = "0 2 * * *"
}

variable "github_repo" {
  description = "GitHub repository in 'owner/repo' format for Workload Identity Federation (e.g. 'my-org/gemini-license-provisioner'). Leave empty to skip WIF resources."
  type        = string
  default     = ""
}

variable "container_image" {
  description = "Docker container image URI for Cloud Run. Overridden by CI/CD on each deploy."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

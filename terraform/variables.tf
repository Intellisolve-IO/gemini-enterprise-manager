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

variable "sync_job_name" {
  description = "Name of the Cloud Run Job that performs the scheduled license sync (invoked by the scheduler job)."
  type        = string
  default     = "gemini-license-sync-runner"
}

variable "public_base_url" {
  description = "Public https base URL of the service, used for links in notification emails. Empty = links fall back to relative paths."
  type        = string
  default     = ""
}

variable "custom_domain" {
  description = "Custom domain to map to the Cloud Run service (e.g. 'app.example.com'). Leave empty to skip creating a domain mapping and use only the *.run.app URL. Requires the domain's ownership to already be verified for this GCP account/project (Search Console) before Terraform can create the mapping."
  type        = string
  default     = ""
}

variable "initial_cron_expression" {
  description = "Initial cron frequency for Cloud Scheduler (UTC). Applies to the single tenant/environment named by scheduled_sync_tenant_id/scheduled_sync_environment_id until the per-environment scheduling fan-out (Phase 5 of the multi-tenant conversion) replaces this."
  type        = string
  default     = "0 2 * * *"
}

variable "scheduled_sync_tenant_id" {
  description = "The tenant_id the scheduled Cloud Run Job syncs, until per-environment scheduling fan-out replaces this single-job model. Leave empty to disable the scheduled job (it will exit 1 every run until set)."
  type        = string
  default     = ""
}

variable "scheduled_sync_environment_id" {
  description = "The environment_id (within scheduled_sync_tenant_id) the scheduled Cloud Run Job syncs. See scheduled_sync_tenant_id."
  type        = string
  default     = ""
}

variable "firebase_api_key" {
  description = "Firebase Web API key for the sign-in page's client-side SDK config. Not secret - this is a public identifier, same as any Firebase web app. From the Firebase console's project settings."
  type        = string
  default     = ""
}

variable "firebase_project_id" {
  description = "The Firebase project ID (usually the same as project_id, once Firebase is added to this GCP project via the Firebase console). Used to derive the client SDK's authDomain."
  type        = string
  default     = ""
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

variable "enable_url_mapping_module" {
  description = "Grant the runtime service account roles/compute.loadBalancerAdmin so the App URL Mapping module can provision Load Balancer resources. This is a project-scoped role - it lets the service account manage ANY load balancer resource of these types in the project, not just ones this app created. Leave false until you've decided to accept that blast radius (see the note beside the IAM binding in main.tf); it only grants the permission; enabling the module itself still happens at runtime from the admin UI."
  type        = bool
  default     = false
}

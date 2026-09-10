variable "project_id" {
  description = "The GCP Project ID where resources will be provisioned."
  type        = string
  default     = "ge-hoffhouse"
}

variable "region" {
  description = "GCP Region for Cloud Run, Cloud Scheduler, and Artifact Registry."
  type        = string
  default     = "us-central1"
}

variable "service_name" {
  description = "Base name for Cloud Run and related services."
  type        = string
  default     = "gemini-license-provisioner"
}

variable "delegated_admin_email" {
  description = "Real, active, licensed Google Workspace admin user to impersonate via Domain-Wide Delegation (e.g. workspace-admin@your-domain.com). Must resolve to an existing user or the sync fails with 'invalid_grant: Invalid email or User ID'."
  type        = string
  default     = "workspace-admin@your-domain.com"
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
  description = "GitHub repository in 'owner/repo' format for Workload Identity Federation (e.g. 'my-org/gemini-license-provisioner')."
  type        = string
  default     = ""
}

variable "container_image" {
  description = "Docker container image URI for Cloud Run. Set during CI/CD deployment."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

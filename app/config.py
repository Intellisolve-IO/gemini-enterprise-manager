import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    """Central-app-wide configuration only. Everything tenant/environment
    -specific (Workspace domain, delegated admin, license subscription,
    IAM/DWD identity) now lives in Firestore under tenants/{id}/environments/{id}
    - see app/firestore_db.py and app/core/module_config.py - not here."""

    # Google Cloud Project Configuration (the central app's own project)
    # GCP_PROJECT_ID has no usable default - set it via the GCP_PROJECT_ID env var.
    GCP_PROJECT_ID: str = os.getenv("GCP_PROJECT_ID", "")
    GCP_REGION: str = os.getenv("GCP_REGION", "us-central1")

    # Firestore Configuration
    FIRESTORE_DATABASE: str = os.getenv("FIRESTORE_DATABASE", "(default)")

    # Cloud Scheduler Configuration. NOTE: today every tenant/environment
    # shares this single Cloud Scheduler job - the per-environment scheduling
    # fan-out (Phase 5 of the multi-tenant conversion) hasn't landed yet. Until
    # then, the scheduled job runs sync for exactly one designated
    # tenant/environment, named here.
    CLOUD_SCHEDULER_JOB_NAME: str = os.getenv("CLOUD_SCHEDULER_JOB_NAME", "gemini-license-sync-job")
    CLOUD_SCHEDULER_LOCATION: str = os.getenv("CLOUD_SCHEDULER_LOCATION", "us-central1")
    SCHEDULED_SYNC_TENANT_ID: str = os.getenv("SCHEDULED_SYNC_TENANT_ID", "")
    SCHEDULED_SYNC_ENVIRONMENT_ID: str = os.getenv("SCHEDULED_SYNC_ENVIRONMENT_ID", "")

    # Service Account Credentials Override (Optional, local dev only) for the
    # *central app's own* identity - the source credentials every impersonation
    # call starts from.
    SERVICE_ACCOUNT_KEY_PATH: Optional[str] = os.getenv("SERVICE_ACCOUNT_KEY_PATH", None)
    SERVICE_ACCOUNT_KEY_JSON: Optional[str] = os.getenv("SERVICE_ACCOUNT_KEY_JSON", None)

    # The central app's own runtime service account email, used for keyless
    # Domain-Wide Delegation / impersonation via the IAM Service Account
    # Credentials API. Must be set explicitly because the metadata server
    # commonly reports the attached service account as "default".
    RUNTIME_SERVICE_ACCOUNT_EMAIL: Optional[str] = os.getenv("RUNTIME_SERVICE_ACCOUNT_EMAIL", None)

    # Public base URL of this service, used to build links in notification
    # emails. Optional: the app also learns it from incoming requests.
    PUBLIC_BASE_URL: Optional[str] = os.getenv("PUBLIC_BASE_URL", None)

    # --- Firebase Authentication (client-side config; not secret - these are
    # public identifiers embedded in the sign-in page, same as any Firebase
    # web app) ---
    FIREBASE_API_KEY: Optional[str] = os.getenv("FIREBASE_API_KEY", None)
    FIREBASE_PROJECT_ID: Optional[str] = os.getenv("FIREBASE_PROJECT_ID", None)

    @property
    def FIREBASE_AUTH_DOMAIN(self) -> str:
        return os.getenv("FIREBASE_AUTH_DOMAIN") or f"{self.FIREBASE_PROJECT_ID}.firebaseapp.com"

    # Web & Security
    PORT: int = int(os.getenv("PORT", 8080))
    DEBUG: bool = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()

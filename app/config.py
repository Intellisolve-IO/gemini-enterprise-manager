import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    # Google Cloud Project Configuration
    GCP_PROJECT_ID: str = "ge-hoffhouse"
    GCP_REGION: str = "us-central1"
    
    # Google Workspace Configuration
    DELEGATED_ADMIN_EMAIL: str = os.getenv("DELEGATED_ADMIN_EMAIL", "admin@hoffhouse.com")
    
    # Default Product and SKU IDs
    # Product: Google-Apps or 101047
    # SKU: 101031 (Standard / specified) or 1010470001 (Gemini Enterprise)
    PRODUCT_ID: str = os.getenv("PRODUCT_ID", "Google-Apps")
    SKU_ID: str = os.getenv("SKU_ID", "101031")
    
    # Firestore Configuration
    FIRESTORE_DATABASE: str = os.getenv("FIRESTORE_DATABASE", "(default)")
    CONFIG_COLLECTION: str = "config"
    CONFIG_DOC_ID: str = "gemini_provisioner"
    HISTORY_COLLECTION: str = "sync_history"
    
    # Cloud Scheduler Configuration
    CLOUD_SCHEDULER_JOB_NAME: str = os.getenv("CLOUD_SCHEDULER_JOB_NAME", "gemini-license-sync-job")
    CLOUD_SCHEDULER_LOCATION: str = os.getenv("CLOUD_SCHEDULER_LOCATION", "us-central1")
    
    # Service Account Credentials Override (Optional, used for local testing or Secret Manager)
    SERVICE_ACCOUNT_KEY_PATH: Optional[str] = os.getenv("SERVICE_ACCOUNT_KEY_PATH", None)
    SERVICE_ACCOUNT_KEY_JSON: Optional[str] = os.getenv("SERVICE_ACCOUNT_KEY_JSON", None)

    # Runtime service account email, used for keyless Domain-Wide Delegation on Cloud Run
    # via the IAM Service Account Credentials API. Must be set explicitly because the
    # metadata server commonly reports the attached service account as "default".
    RUNTIME_SERVICE_ACCOUNT_EMAIL: Optional[str] = os.getenv("RUNTIME_SERVICE_ACCOUNT_EMAIL", None)

    # Web & Security
    PORT: int = int(os.getenv("PORT", 8080))
    DEBUG: bool = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()

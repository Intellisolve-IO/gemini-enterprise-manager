import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from google.cloud import firestore
from app.config import settings

logger = logging.getLogger("gemini_provisioner.firestore")

_db_client: Optional[firestore.Client] = None

TENANTS_COLLECTION = "tenants"
ENVIRONMENTS_SUBCOLLECTION = "environments"
CONFIG_SUBCOLLECTION = "config"
LICENSE_SYNC_CONFIG_DOC_ID = "license_sync"
HISTORY_SUBCOLLECTION = "sync_history"


def get_firestore_client() -> firestore.Client:
    """Singleton helper to obtain Firestore client."""
    global _db_client
    if _db_client is None:
        try:
            _db_client = firestore.Client(
                project=settings.GCP_PROJECT_ID,
                database=settings.FIRESTORE_DATABASE
            )
            logger.info("Initialized Firestore client for project %s", settings.GCP_PROJECT_ID)
        except Exception as e:
            logger.error("Failed to initialize Firestore client: %s", e)
            raise
    return _db_client


def _environment_ref(tenant_id: str, environment_id: str):
    """The document reference for one tenant's one environment - everything
    below (config, module_config, sync_history, health_check_runs) nests
    under this."""
    return (
        get_firestore_client()
        .collection(TENANTS_COLLECTION).document(tenant_id)
        .collection(ENVIRONMENTS_SUBCOLLECTION).document(environment_id)
    )


def get_config(tenant_id: str, environment_id: str) -> Dict[str, Any]:
    """Retrieve one environment's License Sync configuration from Firestore,
    returning defaults if not yet created."""
    default_config: Dict[str, Any] = {
        "monitored_groups": [],
        "license_config": "",
        "license_label": "",
        "delegated_admin_email": "",
        "cron_expression": "0 2 * * *",
        "notification_emails": [],
        "notify_on": "failures",
        "public_base_url": settings.PUBLIC_BASE_URL or "",
        "last_updated": None,
    }

    try:
        doc_ref = (
            _environment_ref(tenant_id, environment_id)
            .collection(CONFIG_SUBCOLLECTION).document(LICENSE_SYNC_CONFIG_DOC_ID)
        )
        snapshot = doc_ref.get()
        if snapshot.exists:
            data = snapshot.to_dict() or {}
            return {**default_config, **data}
        else:
            logger.info("No config document found for tenant=%s environment=%s. Creating default...",
                        tenant_id, environment_id)
            doc_ref.set(default_config)
            return default_config
    except Exception as e:
        logger.warning("Error fetching config from Firestore (%s). Using fallback defaults.", e)
        return default_config


def update_config(tenant_id: str, environment_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Update one environment's License Sync config in Firestore and return the
    full current configuration."""
    doc_ref = (
        _environment_ref(tenant_id, environment_id)
        .collection(CONFIG_SUBCOLLECTION).document(LICENSE_SYNC_CONFIG_DOC_ID)
    )

    current = get_config(tenant_id, environment_id)
    current.update(updates)
    current["last_updated"] = datetime.now(timezone.utc).isoformat()

    doc_ref.set(current)
    logger.info("Updated License Sync config for tenant=%s environment=%s: %s",
                tenant_id, environment_id, list(updates.keys()))
    return current


def record_sync_history(tenant_id: str, environment_id: str, run_record: Dict[str, Any]) -> str:
    """Record a sync run record under one environment's sync_history collection."""
    col_ref = _environment_ref(tenant_id, environment_id).collection(HISTORY_SUBCOLLECTION)

    if "created_at" not in run_record:
        run_record["created_at"] = datetime.now(timezone.utc).isoformat()

    doc_ref = col_ref.document()
    doc_ref.set(run_record)
    logger.info("Saved sync history document ID %s for tenant=%s environment=%s (status: %s)",
                doc_ref.id, tenant_id, environment_id, run_record.get("status"))
    return doc_ref.id


def get_sync_history(tenant_id: str, environment_id: str, limit: int = 25) -> List[Dict[str, Any]]:
    """Retrieve one environment's recent sync execution history, newest first."""
    try:
        col_ref = _environment_ref(tenant_id, environment_id).collection(HISTORY_SUBCOLLECTION)
        query = col_ref.order_by("started_at", direction=firestore.Query.DESCENDING).limit(limit)
        docs = query.stream()

        history = []
        for d in docs:
            record = d.to_dict()
            record["id"] = d.id
            history.append(record)
        return history
    except Exception as e:
        logger.error("Error retrieving sync history from Firestore: %s", e)
        return []

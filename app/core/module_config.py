"""Generic per-environment, per-module Firestore config: one document per
(tenant, environment, module) under that environment's ``module_config``
subcollection, each holding at least an ``enabled`` flag.

Mirrors the merge-with-defaults / full-document-``.set()`` pattern already used
for the License Sync module's own config doc in ``app/firestore_db.py``.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict

from app.firestore_db import _environment_ref

logger = logging.getLogger("gemini_provisioner.module_config")

MODULE_CONFIG_SUBCOLLECTION = "module_config"


def _doc_ref(tenant_id: str, environment_id: str, module_id: str):
    return (
        _environment_ref(tenant_id, environment_id)
        .collection(MODULE_CONFIG_SUBCOLLECTION).document(module_id)
    )


def get_module_config(tenant_id: str, environment_id: str, module_id: str,
                       defaults: Dict[str, Any]) -> Dict[str, Any]:
    """Merge `defaults` (which should include "enabled") with the stored doc,
    auto-creating it if missing. Falls back to defaults on any Firestore error,
    same fail-open-to-defaults behavior as ``firestore_db.get_config``."""
    base: Dict[str, Any] = {"enabled": False, "last_updated": None, **defaults}
    try:
        doc_ref = _doc_ref(tenant_id, environment_id, module_id)
        snapshot = doc_ref.get()
        if snapshot.exists:
            return {**base, **(snapshot.to_dict() or {})}
        doc_ref.set(base)
        return base
    except Exception as e:
        logger.warning(
            "Error fetching module config for tenant=%s environment=%s module=%r (%s). "
            "Using fallback defaults.", tenant_id, environment_id, module_id, e
        )
        return base


def update_module_config(tenant_id: str, environment_id: str, module_id: str,
                          updates: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    """Read-merge-write the module's config doc, mirroring `firestore_db.update_config`."""
    current = get_module_config(tenant_id, environment_id, module_id, defaults)
    current.update(updates)
    current["last_updated"] = datetime.now(timezone.utc).isoformat()
    _doc_ref(tenant_id, environment_id, module_id).set(current)
    logger.info("Updated module config for tenant=%s environment=%s module=%r: %s",
                tenant_id, environment_id, module_id, list(updates.keys()))
    return current


def is_module_enabled(tenant_id: str, environment_id: str, module_id: str) -> bool:
    """Whether `module_id` is currently enabled for this environment, falling
    back to the module's registry-declared default (True only for
    license-sync) if never toggled."""
    from app.core.modules import get_module  # local import: avoids a circular import

    meta = get_module(module_id)
    default_enabled = meta.default_enabled if meta else False
    return bool(get_module_config(tenant_id, environment_id, module_id,
                                   {"enabled": default_enabled}).get("enabled"))

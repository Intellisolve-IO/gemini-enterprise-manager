"""Generic per-module Firestore config: one document per module id in the
``module_config`` collection, each holding at least an ``enabled`` flag.

Mirrors the merge-with-defaults / full-document-``.set()`` pattern already used for
the license-sync feature's own config doc in ``app/firestore_db.py`` — this is a
*separate* collection, so it never touches that existing doc or its schema.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict

from app.firestore_db import get_firestore_client

logger = logging.getLogger("gemini_provisioner.module_config")

MODULE_CONFIG_COLLECTION = "module_config"


def get_module_config(module_id: str, defaults: Dict[str, Any]) -> Dict[str, Any]:
    """Merge `defaults` (which should include "enabled") with the stored doc,
    auto-creating it if missing. Falls back to defaults on any Firestore error,
    same fail-open-to-defaults behavior as ``firestore_db.get_config``."""
    base: Dict[str, Any] = {"enabled": False, "last_updated": None, **defaults}
    try:
        db = get_firestore_client()
        doc_ref = db.collection(MODULE_CONFIG_COLLECTION).document(module_id)
        snapshot = doc_ref.get()
        if snapshot.exists:
            return {**base, **(snapshot.to_dict() or {})}
        doc_ref.set(base)
        return base
    except Exception as e:
        logger.warning(
            "Error fetching module config for %r (%s). Using fallback defaults.", module_id, e
        )
        return base


def update_module_config(module_id: str, updates: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    """Read-merge-write the module's config doc, mirroring `firestore_db.update_config`."""
    current = get_module_config(module_id, defaults)
    current.update(updates)
    current["last_updated"] = datetime.now(timezone.utc).isoformat()
    db = get_firestore_client()
    db.collection(MODULE_CONFIG_COLLECTION).document(module_id).set(current)
    logger.info("Updated module config for %r: %s", module_id, list(updates.keys()))
    return current


def is_module_enabled(module_id: str) -> bool:
    """Whether `module_id` is currently enabled, falling back to the module's
    registry-declared default (True only for license-sync) if never toggled."""
    from app.core.modules import get_module  # local import: avoids a circular import

    meta = get_module(module_id)
    default_enabled = meta.default_enabled if meta else False
    return bool(get_module_config(module_id, {"enabled": default_enabled}).get("enabled"))

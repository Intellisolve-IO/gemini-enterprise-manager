"""Tenant / environment / membership data layer.

A **tenant** is a company/customer: the unit of billing, membership, and
Firebase-identity grouping. An **environment** is one GCP project + optionally
one GE app + its own module config, schedule, and license pool - a tenant has
1..N environments (this is what lets a company isolate licenses per project,
or run departmental GE apps, without conflating "which company" with "which
department's isolated setup").

Firestore layout:
    tenants/{tenant_id}
    tenants/{tenant_id}/members/{uid}
    tenants/{tenant_id}/environments/{environment_id}
    user_index/{uid}                    # denormalized uid -> tenant_ids, for fast login lookup

See app/firestore_db.py and app/core/module_config.py for the environment-scoped
config/history/module-config documents that live *under* each environment.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.firestore_db import get_firestore_client

logger = logging.getLogger("gemini_provisioner.tenants")

TENANTS_COLLECTION = "tenants"
MEMBERS_SUBCOLLECTION = "members"
ENVIRONMENTS_SUBCOLLECTION = "environments"
USER_INDEX_COLLECTION = "user_index"

VALID_ROLES = ("owner", "admin", "member")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Tenants
# ---------------------------------------------------------------------------
def create_tenant(name: str, created_by_uid: str, primary_domain: str = "") -> str:
    """Create a new tenant and add its creator as owner. Returns the new tenant_id."""
    tenant_id = uuid.uuid4().hex[:12]
    db = get_firestore_client()
    db.collection(TENANTS_COLLECTION).document(tenant_id).set({
        "name": name,
        "primary_domain": primary_domain,
        "created_at": _now(),
        "created_by_uid": created_by_uid,
    })
    return tenant_id


def get_tenant(tenant_id: str) -> Optional[Dict[str, Any]]:
    snapshot = get_firestore_client().collection(TENANTS_COLLECTION).document(tenant_id).get()
    if not snapshot.exists:
        return None
    return {"id": tenant_id, **(snapshot.to_dict() or {})}


def list_tenants_for_uid(uid: str) -> List[Dict[str, Any]]:
    """Every tenant `uid` belongs to, via the user_index denormalization."""
    tenant_ids = get_tenant_ids_for_uid(uid)
    tenants = [get_tenant(tid) for tid in tenant_ids]
    return [t for t in tenants if t is not None]


def get_tenant_ids_for_uid(uid: str) -> List[str]:
    snapshot = get_firestore_client().collection(USER_INDEX_COLLECTION).document(uid).get()
    if not snapshot.exists:
        return []
    return (snapshot.to_dict() or {}).get("tenant_ids", [])


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------
def add_member(tenant_id: str, uid: str, email: str, role: str = "member",
               added_by_uid: Optional[str] = None) -> None:
    """Add (or update the role of) a tenant member, dual-writing the user_index
    lookup used at login. Both writes happen outside a transaction (two small,
    idempotent writes to different documents); a partial failure just means the
    index needs a retry, not silent data loss - the membership doc is the
    source of truth."""
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role: {role!r}. Must be one of {VALID_ROLES}.")

    db = get_firestore_client()
    db.collection(TENANTS_COLLECTION).document(tenant_id) \
        .collection(MEMBERS_SUBCOLLECTION).document(uid).set({
            "email": email,
            "role": role,
            "added_at": _now(),
            "added_by_uid": added_by_uid,
        })

    index_ref = db.collection(USER_INDEX_COLLECTION).document(uid)
    snapshot = index_ref.get()
    tenant_ids = set((snapshot.to_dict() or {}).get("tenant_ids", [])) if snapshot.exists else set()
    tenant_ids.add(tenant_id)
    index_ref.set({"email": email, "tenant_ids": sorted(tenant_ids)})


def get_member(tenant_id: str, uid: str) -> Optional[Dict[str, Any]]:
    snapshot = get_firestore_client().collection(TENANTS_COLLECTION).document(tenant_id) \
        .collection(MEMBERS_SUBCOLLECTION).document(uid).get()
    if not snapshot.exists:
        return None
    return {"uid": uid, **(snapshot.to_dict() or {})}


def list_members(tenant_id: str) -> List[Dict[str, Any]]:
    docs = get_firestore_client().collection(TENANTS_COLLECTION).document(tenant_id) \
        .collection(MEMBERS_SUBCOLLECTION).stream()
    return [{"uid": d.id, **(d.to_dict() or {})} for d in docs]


# ---------------------------------------------------------------------------
# Environments
# ---------------------------------------------------------------------------
def create_environment(tenant_id: str, display_name: str) -> str:
    """Create a new, empty environment stub (status="onboarding"). Returns the
    new environment_id."""
    environment_id = uuid.uuid4().hex[:12]
    db = get_firestore_client()
    db.collection(TENANTS_COLLECTION).document(tenant_id) \
        .collection(ENVIRONMENTS_SUBCOLLECTION).document(environment_id).set({
            "display_name": display_name,
            "gcp_project_id": "",
            "gcp_project_number": "",
            "sa_email": "",
            "delegated_admin_email": "",
            "ge_app_id": "",
            "provisioned_roles": [],
            "provisioned_services": [],
            "status": "onboarding",
            "created_at": _now(),
            "onboarded_at": None,
        })
    return environment_id


def get_environment(tenant_id: str, environment_id: str) -> Optional[Dict[str, Any]]:
    snapshot = get_firestore_client().collection(TENANTS_COLLECTION).document(tenant_id) \
        .collection(ENVIRONMENTS_SUBCOLLECTION).document(environment_id).get()
    if not snapshot.exists:
        return None
    return {"id": environment_id, "tenant_id": tenant_id, **(snapshot.to_dict() or {})}


def list_environments(tenant_id: str) -> List[Dict[str, Any]]:
    docs = get_firestore_client().collection(TENANTS_COLLECTION).document(tenant_id) \
        .collection(ENVIRONMENTS_SUBCOLLECTION).stream()
    return [{"id": d.id, "tenant_id": tenant_id, **(d.to_dict() or {})} for d in docs]


def update_environment(tenant_id: str, environment_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    ref = get_firestore_client().collection(TENANTS_COLLECTION).document(tenant_id) \
        .collection(ENVIRONMENTS_SUBCOLLECTION).document(environment_id)
    ref.update(updates)
    snapshot = ref.get()
    return {"id": environment_id, "tenant_id": tenant_id, **(snapshot.to_dict() or {})}

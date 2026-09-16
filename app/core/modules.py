"""The module registry: the static list of admin-console feature modules and
lookups derived from it. Adding a future module means appending one ``ModuleMeta``
entry here plus mounting its router in ``app/main.py`` — nothing else in the
framework needs to change.
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.core.module_config import is_module_enabled


@dataclass(frozen=True)
class ModuleMeta:
    id: str
    title: str
    icon: str            # FontAwesome class, e.g. "fa-solid fa-id-badge"
    description: str
    base_path: str        # where the landing page's "Open" link goes
    default_enabled: bool = False


MODULES: List[ModuleMeta] = [
    ModuleMeta(
        id="license-sync",
        title="License Sync",
        icon="fa-solid fa-id-badge",
        description="Assign Gemini Enterprise licenses from Google Group membership.",
        base_path="/modules/license-sync",
        default_enabled=True,
    ),
    ModuleMeta(
        id="url-mapping",
        title="App URL Mapping",
        icon="fa-solid fa-link",
        description="Map a custom domain to a Gemini Enterprise app deep link.",
        base_path="/modules/url-mapping",
        default_enabled=False,
    ),
    ModuleMeta(
        id="agent-deployment",
        title="Agent Deployment",
        icon="fa-solid fa-robot",
        description="Register an agent into Gemini Enterprise.",
        base_path="/modules/agent-deployment",
        default_enabled=False,
    ),
    ModuleMeta(
        id="health-check",
        title="Health Check",
        icon="fa-solid fa-heart-pulse",
        description="Audit GE-related IAM, API enablement, licensing, and DWD connectivity.",
        base_path="/modules/health-check",
        default_enabled=False,
    ),
]

_BY_ID: Dict[str, ModuleMeta] = {m.id: m for m in MODULES}


def get_module(module_id: str) -> Optional[ModuleMeta]:
    return _BY_ID.get(module_id)


def enabled_modules_context() -> List[Dict[str, Any]]:
    """Enabled modules, in registry order, as plain dicts for template rendering."""
    out: List[Dict[str, Any]] = []
    for m in MODULES:
        if is_module_enabled(m.id):
            out.append({"id": m.id, "title": m.title, "icon": m.icon, "base_path": m.base_path})
    return out

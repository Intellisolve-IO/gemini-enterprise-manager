"""The single Jinja2Templates instance shared by every module router, so template
lookups (``"license_sync/dashboard.html"``, ``"landing.html"``, ...) all resolve
against the same ``app/templates/`` directory regardless of which router renders them.
"""
from pathlib import Path

from fastapi.templating import Jinja2Templates

APP_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = APP_DIR / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

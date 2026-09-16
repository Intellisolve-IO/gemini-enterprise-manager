"""Thin wrapper around ``Jinja2Templates.TemplateResponse`` that injects the
current enabled-module list into every page's context, so ``base.html``'s nav can
be data-driven without every route having to remember to pass it in.
"""
from typing import Any, Dict

from fastapi import Request
from fastapi.responses import HTMLResponse

from app.core.modules import enabled_modules_context
from app.core.templating import templates


def render(request: Request, name: str, context: Dict[str, Any]) -> HTMLResponse:
    merged = {"enabled_modules": enabled_modules_context(), **context}
    return templates.TemplateResponse(request=request, name=name, context=merged)

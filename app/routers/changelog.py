from fastapi import APIRouter, Depends, Request

from .. import version
from ..auth import login_required
from ..templating import templates

router = APIRouter()


@router.get("/changelog")
def changelog_page(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse(
        "changelog.html",
        {"request": request, "app_version": version.APP_VERSION, "changelog": version.CHANGELOG},
    )

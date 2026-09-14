import shlex
import subprocess

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from .. import activity_log, config
from ..auth import login_required
from ..db import get_db
from ..templating import templates

router = APIRouter()


@router.get("/logs")
def logs_page(
    request: Request,
    user=Depends(login_required),
    db: Session = Depends(get_db),
):
    log_text = ""
    error = None
    try:
        result = subprocess.run(
            shlex.split(config.LOGS_JOURNALCTL),
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            log_text = result.stdout
        else:
            error = (result.stderr or result.stdout or "journalctl gagal, tidak ada detail").strip()
    except subprocess.TimeoutExpired:
        error = "journalctl belum selesai setelah 15 detik."
    except Exception as e:
        error = str(e)

    return templates.TemplateResponse(
        "logs.html",
        {
            "request": request,
            "log_text": log_text, "error": error,
            "activity": activity_log.recent(db, limit=20),
        },
    )

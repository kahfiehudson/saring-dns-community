from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from .. import activity_log, models, unbound_writer
from ..auth import login_required
from ..db import get_db
from ..templating import templates

router = APIRouter()


def _get_settings(db: Session) -> models.UnboundSettings:
    s = db.query(models.UnboundSettings).first()
    if not s:
        s = models.UnboundSettings()
        db.add(s)
        db.commit()
    return s


def _render(request: Request, s: models.UnboundSettings, message=None, error=None):
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "s": s,
            "schedule_choices": unbound_writer.UPDATE_SCHEDULE_CHOICES,
            "message": message,
            "error": error,
        },
    )


@router.get("/settings")
def settings_page(request: Request, user=Depends(login_required), db: Session = Depends(get_db)):
    s = _get_settings(db)
    return _render(request, s)


@router.post("/settings")
def settings_save(
    request: Request,
    block_mode: str = Form("nxdomain"),
    redirect_ip: str = Form(""),
    resolve_mode: str = Form("recursive"),
    forward_upstreams: str = Form(""),
    forward_use_tls: bool = Form(False),
    dnssec_enabled: bool = Form(False),
    qname_minimisation: bool = Form(False),
    access_control_cidrs: str = Form(""),
    log_queries: bool = Form(False),
    verbosity: int = Form(1),
    update_schedule: str = Form("*-*-* 04:00:00"),
    safesearch_google: bool = Form(False),
    safesearch_youtube: bool = Form(False),
    safesearch_bing: bool = Form(False),
    safesearch_duckduckgo: bool = Form(False),
    user=Depends(login_required),
    db: Session = Depends(get_db),
):
    s = _get_settings(db)
    s.block_mode = block_mode
    s.redirect_ip = redirect_ip
    s.resolve_mode = resolve_mode
    s.forward_upstreams = forward_upstreams
    s.forward_use_tls = forward_use_tls
    s.dnssec_enabled = dnssec_enabled
    s.qname_minimisation = qname_minimisation
    # Normalize to one "cidr action" entry per line regardless of how it
    # was typed/pasted (newline- or comma-separated, or a mix) - keeps
    # what's stored (and what the textarea shows next time) consistently
    # easy to add/remove a single entry from.
    s.access_control_cidrs = "\n".join(unbound_writer.parse_cidr_entries(access_control_cidrs))
    s.log_queries = log_queries
    s.verbosity = verbosity
    s.safesearch_google = safesearch_google
    s.safesearch_youtube = safesearch_youtube
    s.safesearch_bing = safesearch_bing
    s.safesearch_duckduckgo = safesearch_duckduckgo

    schedule_changed = update_schedule != s.update_schedule
    if update_schedule in unbound_writer._VALID_SCHEDULES:
        s.update_schedule = update_schedule
    db.commit()

    ok, msg = unbound_writer.apply(s)

    if ok and schedule_changed:
        sched_ok, sched_msg = unbound_writer.apply_update_schedule(s.update_schedule)
        if not sched_ok:
            ok, msg = False, f"Konfigurasi Unbound tersimpan, tapi jadwal update gagal diterapkan: {sched_msg}"

    activity_log.log(db, "Pengaturan DNS disimpan", msg, ok=ok)

    return _render(request, s, message=msg if ok else None, error=None if ok else msg)

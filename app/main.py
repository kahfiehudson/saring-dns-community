from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from . import activity_log, config, db, models
from .db import get_db
from .routers import account, changelog, dashboard, logs
from .routers import settings as settings_router
from .security import verify_password
from .templating import templates

app = FastAPI(title="SARING DNS Dashboard")
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY)
app.mount("/static", StaticFiles(directory=str(config.BASE_DIR / "app" / "static")), name="static")

app.include_router(dashboard.router)
app.include_router(changelog.router)
app.include_router(settings_router.router)
app.include_router(logs.router)
app.include_router(account.router)


@app.on_event("startup")
def on_startup():
    db.init_db()


def _client_ip(request: Request) -> str:
    # Behind scripts/setup_nginx.sh's reverse proxy, request.client.host is
    # just 127.0.0.1 - prefer the forwarded header when present.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


def _complete_login(request: Request, db_session: Session, account: models.User) -> None:
    ip = _client_ip(request)
    account.last_login_at = datetime.now(timezone.utc)
    account.last_login_ip = ip
    activity_log.log(db_session, f"Login: {account.username}", ip)
    request.session["user"] = account.username


@app.post("/login")
def login(
    request: Request, username: str = Form(...), password: str = Form(...),
    db_session: Session = Depends(get_db),
):
    acc = db_session.query(models.User).filter_by(username=username.strip().lower()).first()
    valid = acc is not None and verify_password(password, acc.password_hash)
    if not valid:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Username atau password salah"},
            status_code=401,
        )

    _complete_login(request, db_session, acc)
    return RedirectResponse("/", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from .. import activity_log, models
from ..auth import login_required
from ..db import get_db
from ..security import hash_password, verify_password
from ..templating import templates

router = APIRouter()

MIN_PASSWORD_LENGTH = 8


def _current_user(request: Request, db: Session) -> models.User:
    return db.query(models.User).filter_by(username=request.session.get("user")).first()


def _render(request: Request, account: models.User, message=None, error=None):
    return templates.TemplateResponse(
        "account.html",
        {"request": request, "account": account, "message": message, "error": error},
    )


@router.get("/account")
def account_page(request: Request, user=Depends(login_required), db: Session = Depends(get_db)):
    return _render(request, _current_user(request, db))


@router.post("/account/password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    user=Depends(login_required),
    db: Session = Depends(get_db),
):
    account = _current_user(request, db)
    if not verify_password(current_password, account.password_hash):
        return _render(request, account, error="Password saat ini salah.")
    if len(new_password) < MIN_PASSWORD_LENGTH:
        return _render(request, account, error=f"Password baru minimal {MIN_PASSWORD_LENGTH} karakter.")
    if new_password != confirm_password:
        return _render(request, account, error="Konfirmasi password baru tidak cocok.")

    account.password_hash = hash_password(new_password)
    db.commit()
    activity_log.log(db, f"Password diganti: {account.username}")
    return _render(request, account, message="Password berhasil diganti.")

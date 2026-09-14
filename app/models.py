from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String

from .db import Base


def _utcnow():
    return datetime.now(timezone.utc)


class UnboundSettings(Base):
    """Single-row table holding every dashboard-editable Unbound feature."""

    __tablename__ = "unbound_settings"

    id = Column(Integer, primary_key=True)

    # Block behavior for the TrustPositif blocklist
    block_mode = Column(String, default="nxdomain")  # "nxdomain" | "redirect"
    redirect_ip = Column(String, default="")

    # systemd OnCalendar= expression for trustpositif-update.timer - one of
    # unbound_writer.UPDATE_SCHEDULE_CHOICES, enforced server-side (this is
    # never free text, to keep the sudo-wrapped systemd override script safe).
    update_schedule = Column(String, default="*-*-* 04:00:00")

    # Resolution mode
    resolve_mode = Column(String, default="recursive")  # "recursive" | "forward"
    forward_upstreams = Column(String, default="1.1.1.1@853,8.8.8.8@853")
    forward_use_tls = Column(Boolean, default=True)

    # Security / privacy
    dnssec_enabled = Column(Boolean, default=True)
    qname_minimisation = Column(Boolean, default=True)

    # Access control: comma-separated "cidr action" pairs
    access_control_cidrs = Column(
        String,
        default="127.0.0.0/8 allow,::1/128 allow,0.0.0.0/0 refuse,::/0 refuse",
    )

    # Logging
    log_queries = Column(Boolean, default=False)
    verbosity = Column(Integer, default=1)

    # SafeSearch enforcement (see unbound_writer.SAFESEARCH_TOGGLES) - each
    # forces that provider's own official safe-mode endpoint via Unbound
    # local-data. Off by default so a routine Settings save never silently
    # starts rewriting search results.
    safesearch_google = Column(Boolean, default=False)
    safesearch_youtube = Column(Boolean, default=False)
    safesearch_bing = Column(Boolean, default=False)
    safesearch_duckduckgo = Column(Boolean, default=False)

    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class ActivityLog(Base):
    """Human-readable audit trail of admin actions taken through the
    dashboard (settings saved, domains added/removed, manual Unbound
    restart/reload...) - separate from the raw system log on /logs, which
    is journalctl output rather than a record of *who changed what*."""

    __tablename__ = "activity_log"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=_utcnow, index=True)
    action = Column(String, nullable=False)
    detail = Column(String, default="")
    ok = Column(Boolean, default=True)


class User(Base):
    """Dashboard login account. Single admin account model - no per-account
    roles; whoever can log in has full access to every page."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    # Both null until the first successful login - see main.py's /login route.
    last_login_at = Column(DateTime, nullable=True)
    last_login_ip = Column(String, nullable=True)

from datetime import datetime, timedelta, timezone

from fastapi.templating import Jinja2Templates

from . import config, version

templates = Jinja2Templates(directory=str(config.BASE_DIR / "app" / "templates"))
# Global (not a per-route context var) so every template - base.html's
# footer in particular - can read it without every router needing to pass
# it explicitly.
templates.env.globals["app_version"] = version.APP_VERSION

_WIB = timezone(timedelta(hours=7))  # Asia/Jakarta has no DST, so a fixed offset is exact


def to_wib(value):
    """Timestamps are stored/generated in UTC throughout the app
    (datetime.now(timezone.utc)) - this only affects display."""
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_WIB).strftime("%Y-%m-%d %H:%M:%S WIB")
    except (ValueError, TypeError):
        return value


templates.env.filters["to_wib"] = to_wib


def compact_number(value):
    """1284 -> '1,284'; 12900 -> '12.9K'; 4200000 -> '4.2M' - the stat-tile
    figure contract (proportional figures, auto-compact past four digits)."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return value
    if abs(n) < 10_000:
        return f"{int(n):,}"
    if abs(n) < 1_000_000:
        return f"{n / 1_000:.1f}K"
    return f"{n / 1_000_000:.1f}M"


templates.env.filters["compact_number"] = compact_number

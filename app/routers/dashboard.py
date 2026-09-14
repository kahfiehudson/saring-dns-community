from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import activity_log, resource_monitor, unbound_writer
from ..auth import login_required
from ..db import get_db
from ..templating import templates

router = APIRouter()


def _dns_kpis(stats: dict) -> dict | None:
    """Derive the handful of headline numbers the dashboard leads with from
    raw unbound-control stats - a snapshot since the last stats reset, not a
    time series (nothing here is stored historically)."""
    if not stats:
        return None
    try:
        total = int(stats.get("total.num.queries", 0))
        hits = int(stats.get("total.num.cachehits", 0))
        misses = int(stats.get("total.num.cachemiss", 0))
        recursive = int(stats.get("total.num.recursivereplies", 0))
    except (TypeError, ValueError):
        return None
    denom = hits + misses
    hit_rate = round((hits / denom) * 100, 1) if denom else 0.0
    return {
        "total_queries": total,
        "cache_hits": hits,
        "cache_misses": misses,
        "recursive_replies": recursive,
        "hit_rate": hit_rate,
    }


@router.get("/")
def index(request: Request, user=Depends(login_required)):
    status = unbound_writer.read_status()
    stats = unbound_writer.query_unbound_stats()
    unbound_status = unbound_writer.unbound_service_status()

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "status": status,
            "stats": stats,
            "kpis": _dns_kpis(stats),
            "next_update": unbound_writer.next_update_run(),
            "unbound_status": unbound_status,
        },
    )


@router.post("/unbound/restart")
def unbound_restart(user=Depends(login_required), db: Session = Depends(get_db)):
    ok, msg = unbound_writer.manual_restart_unbound()
    activity_log.log(db, "Restart Unbound (manual)", msg, ok=ok)
    return RedirectResponse("/", status_code=303)


@router.post("/unbound/reload")
def unbound_reload(user=Depends(login_required), db: Session = Depends(get_db)):
    ok, msg = unbound_writer.manual_reload_unbound()
    activity_log.log(db, "Reload Unbound (manual)", msg, ok=ok)
    return RedirectResponse("/", status_code=303)


@router.get("/api/live-stats")
def live_stats(user=Depends(login_required)):
    """Polled every few seconds by the dashboard's "Query Real-time" card to
    compute a live queries-per-second figure client-side (rate = delta
    between two polls / elapsed time) - unbound-control itself has no
    concept of a rate, only cumulative counters. Also carries the live
    Unbound running/not-running badge and the raw counters "Statistik DNS"
    needs, piggy-backed on this same poll rather than adding separate ones
    just for those - it's the same unbound-control call either way."""
    unbound_active = unbound_writer.unbound_service_status()["active"]
    stats = unbound_writer.query_unbound_stats()
    if not stats:
        return {"ok": False, "unbound_active": unbound_active}
    try:
        return {
            "ok": True,
            "unbound_active": unbound_active,
            "ts": datetime.now(timezone.utc).timestamp(),
            "total_queries": int(stats.get("total.num.queries", 0)),
            "cache_hits": int(stats.get("total.num.cachehits", 0)),
            "cache_misses": int(stats.get("total.num.cachemiss", 0)),
            "recursive_replies": int(stats.get("total.num.recursivereplies", 0)),
        }
    except (TypeError, ValueError):
        return {"ok": False, "unbound_active": unbound_active}


@router.get("/api/live-resources")
def live_resources(user=Depends(login_required)):
    """Polled every few seconds by the "Server Real-time" card. CPU% is
    computed client-side from two consecutive polls' raw jiffies, same
    pattern as live_stats()'s QPS - mem/disk/load are already point-in-time
    gauges, returned as-is."""
    cpu_total, cpu_idle = resource_monitor.read_cpu_jiffies()
    return {
        "ok": True,
        "ts": datetime.now(timezone.utc).timestamp(),
        "cpu_total": cpu_total,
        "cpu_idle": cpu_idle,
        "mem_percent": resource_monitor.read_mem_percent(),
        "disk_percent": resource_monitor.read_disk_percent(),
        "load1": resource_monitor.read_load1(),
    }

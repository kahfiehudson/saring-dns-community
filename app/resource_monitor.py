"""Server resource monitoring (CPU/RAM/disk/load) - stdlib only, no psutil,
reading straight from /proc and shutil.disk_usage(). read_cpu_jiffies() is
instantaneous and non-blocking, meant to be called once per HTTP poll from
the "Server Real-time" live card - the browser computes CPU% client-side
from two consecutive polls, exactly like the Query Real-time card computes
QPS (see dashboard.html's shared poll JS)."""
from __future__ import annotations

import os
import shutil


def read_cpu_jiffies() -> tuple[int | None, int | None]:
    """Returns (total_jiffies, idle_jiffies) from the aggregate 'cpu ' line
    in /proc/stat, or (None, None) if unavailable (non-Linux, restricted
    container, ...)."""
    try:
        with open("/proc/stat") as f:
            for line in f:
                if line.startswith("cpu "):
                    fields = [int(x) for x in line.split()[1:]]
                    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)  # idle + iowait
                    return sum(fields), idle
    except Exception:
        pass
    return None, None


def read_mem_percent() -> float | None:
    info = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2 and parts[0].strip() in ("MemTotal", "MemAvailable"):
                    info[parts[0].strip()] = int(parts[1].strip().split()[0])
    except Exception:
        return None
    total = info.get("MemTotal")
    if not total:
        return None
    avail = info.get("MemAvailable", 0)
    return round(100.0 * (1 - avail / total), 1)


def read_disk_percent(path: str = "/") -> float | None:
    try:
        usage = shutil.disk_usage(path)
        if usage.total == 0:
            return None
        return round(100.0 * usage.used / usage.total, 1)
    except Exception:
        return None


def read_load1() -> float | None:
    try:
        return round(os.getloadavg()[0], 2)
    except Exception:
        return None

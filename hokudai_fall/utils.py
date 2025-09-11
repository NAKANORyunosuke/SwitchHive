from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import socket
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def utc_now() -> dt.datetime:
    return dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)


def iso_utc(ts: dt.datetime | None = None) -> str:
    if ts is None:
        ts = utc_now()
    return ts.astimezone(dt.timezone.utc).strftime(ISO_FMT)


def event_id(camera_id: str, seq: int, ts: dt.datetime | None = None) -> str:
    ts_s = (ts or utc_now()).astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts_s}_{camera_id}_fall_{seq:04d}"


def event_dir(base_dir: Path, camera_id: str, ev_id: str, ts: dt.datetime) -> Path:
    y = f"{ts.year:04d}"
    m = f"{ts.month:02d}"
    d = f"{ts.day:02d}"
    return base_dir / camera_id / y / m / d / ev_id


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def serialize_json(d: Dict[str, Any], redact: bool = False) -> str:
    if redact:
        d = dict(d)  # shallow copy
        sys = d.get("system", {})
        sys.pop("host", None)
        d["system"] = sys
    return json.dumps(d, ensure_ascii=False, indent=2)


def disk_free_percent(path: Path) -> float:
    usage = shutil.disk_usage(str(path))
    return (usage.free / usage.total) * 100.0


def list_event_dirs(base_dir: Path) -> List[Path]:
    # return all leaf event directories under base_dir/*/*/*/*/*
    if not base_dir.exists():
        return []
    result: List[Path] = []
    for camera_dir in base_dir.iterdir():
        if not camera_dir.is_dir():
            continue
        for y in camera_dir.iterdir():
            if not y.is_dir():
                continue
            for m in y.iterdir():
                if not m.is_dir():
                    continue
                for d in m.iterdir():
                    if not d.is_dir():
                        continue
                    for e in d.iterdir():
                        if e.is_dir():
                            result.append(e)
    result.sort(key=lambda p: p.stat().st_mtime)
    return result


def remove_dir(p: Path) -> None:
    shutil.rmtree(str(p), ignore_errors=True)


def host_name() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "unknown-host"


def enforce_retention(base_dir: Path, retention_days: int, min_free_pct: float = 5.0) -> None:
    # remove old events beyond retention_days; if free < min, remove oldest until above threshold
    if not base_dir.exists():
        return
    now = dt.datetime.utcnow().timestamp()
    cutoff = now - (retention_days * 86400)
    events = list_event_dirs(base_dir)
    # remove older than cutoff
    for e in events:
        try:
            if e.stat().st_mtime < cutoff:
                remove_dir(e)
        except Exception:
            pass
    # if free space too low, remove oldest regardless of age
    while True:
        try:
            if disk_free_percent(base_dir) >= min_free_pct:
                break
        except Exception:
            break
        events = list_event_dirs(base_dir)
        if not events:
            break
        remove_dir(events[0])

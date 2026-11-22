"""Campaign records: a small, per-user way to group clips and posting settings.

Campaigns are not ad-platform objects. They are saved briefs: a name, the creator and
platforms they target, a footer/tag, and an optional payout estimate. The posting queue
stays the source of truth for what will actually be published.
"""
import copy
import json
import re
import threading
import time
import uuid
from pathlib import Path

from . import config, ctx, platforms

_lock = threading.RLock()
_mem: dict = {}

STATUSES = ("active", "paused", "done")
DEFAULTS = {"name": "", "creator": "", "platforms": [], "status": "active", "footer": "", "rate_per_1k": 0.0,
            "start_at": "", "end_at": "", "notes": ""}


def _path(uid: str | None = None) -> Path:
    uid = uid or ctx.uid()
    d = config.user_dir(uid)
    d.mkdir(parents=True, exist_ok=True)
    return d / "campaigns.json"


def _read(uid: str | None = None) -> list:
    uid = uid or ctx.uid()
    got = _mem.get(uid)
    if got is not None:
        return got
    try:
        data = json.loads(_path(uid).read_text(encoding="utf-8"))
        got = [c for c in data if isinstance(c, dict) and c.get("id")] if isinstance(data, list) else []
    except (OSError, ValueError):
        got = []
    _mem[uid] = got
    return got


def _save(uid: str, rows: list):
    with _lock:
        f = _path(uid)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)
        _mem[uid] = rows
    ctx.bump(uid)


def _clean(data: dict) -> dict:
    out = {}
    for k in DEFAULTS:
        if k not in data or data[k] is None:
            continue
        v = data[k]
        if k == "name":
            v = str(v).strip()[:80]
        elif k == "creator":
            v = str(v).strip()[:60]
        elif k == "platforms":
            v = [platforms.get(x).id for x in (v if isinstance(v, list) else [])][:12]
            v = list(dict.fromkeys(v))
        elif k == "status":
            v = v if v in STATUSES else "active"
        elif k == "rate_per_1k":
            v = max(0.0, min(10000.0, float(v or 0)))
        elif k in ("start_at", "end_at"):
            v = str(v).strip()[:30]
        else:
            v = str(v).strip()[:500]
        out[k] = v
    return out


def all_campaigns() -> list:
    rows = sorted(_read(), key=lambda c: c.get("created", 0), reverse=True)
    return [copy.deepcopy(c) for c in rows]


def get(cid: str):
    if not re.fullmatch(r"[\w\-]{1,60}", str(cid or "")):
        return None
    c = next((x for x in _read() if x.get("id") == cid), None)
    return copy.deepcopy(c) if c else None


def create(data: dict) -> dict:
    c = {**DEFAULTS, **_clean(data)}
    if not c["name"]:
        raise ValueError("Give the campaign a name.")
    c.update(id="cmp_" + uuid.uuid4().hex[:10], created=time.time(), updated=time.time())
    rows = _read()
    rows.append(c)
    _save(ctx.uid(), rows)
    return copy.deepcopy(c)


def update(cid: str, data: dict) -> dict:
    rows = _read()
    c = next((x for x in rows if x.get("id") == cid), None)
    if not c:
        raise KeyError(cid)
    new = _clean(data)
    if "name" in new and not new["name"]:
        raise ValueError("Give the campaign a name.")
    c.update(new, updated=time.time())
    _save(ctx.uid(), rows)
    return copy.deepcopy(c)


def delete(cid: str) -> bool:
    rows = _read()
    keep = [x for x in rows if x.get("id") != cid]
    if len(keep) == len(rows):
        return False
    _save(ctx.uid(), keep)
    return True


def forget(uid: str):
    _mem.pop(uid, None)

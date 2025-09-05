import os, json, time, pathlib
from typing import Dict, Iterable, List, Optional
from datetime import datetime, timedelta, timezone
from .config import settings
from .types import Profile, Classification, BlockResult

pathlib.Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
pathlib.Path(settings.cache_dir).mkdir(parents=True, exist_ok=True)
pathlib.Path(settings.user_data_dir).mkdir(parents=True, exist_ok=True)
pathlib.Path(settings.screenshot_dir).mkdir(parents=True, exist_ok=True)


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


def write_profile_cache(p: Profile):
    handle = p.handle.lstrip("@")
    path = os.path.join(settings.cache_dir, f"{handle}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(p.model_dump(), f, ensure_ascii=False, indent=2)


def append_jsonl(path: str, obj: Dict):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def save_dataset_entry(p: Profile):
    entry = p.model_dump()
    entry["__type"] = "profile"
    append_jsonl(settings.dataset_path, entry)


def save_classification(c: Classification):
    entry = c.model_dump()
    entry["__type"] = "classification"
    entry["saved_at"] = now_iso()
    append_jsonl(settings.results_path, entry)


def read_jsonl(path: str):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def save_block_result(b: BlockResult):
    entry = b.model_dump()
    entry["__type"] = "block"
    entry["saved_at"] = now_iso()
    append_jsonl(settings.results_path, entry)


def _parse_iso(ts: str) -> Optional[datetime]:
    try:
        # saved via time.gmtime without timezone; treat as UTC
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def get_failed_block_handles(
    limit: Optional[int] = None,
    days: Optional[int] = None,
) -> List[str]:
    """Return handles whose latest block result is a failure.

    - Dedupe by handle, considering only the most recent block entry per handle
    - If "days" is provided, only include failures whose latest record is within that window
    - If "limit" is provided, return up to that many handles (most recent first)
    """
    rows = read_jsonl(settings.results_path)
    last_by_handle: Dict[str, Dict] = {}
    # Sort rows by saved_at ascending so later overwrite wins; fallback to insertion order
    def _key(r):
        dt = _parse_iso(r.get("saved_at", "") or "")
        return dt or datetime.min.replace(tzinfo=timezone.utc)
    for r in sorted(rows, key=_key):
        if r.get("__type") != "block":
            continue
        h = r.get("handle")
        if not h:
            continue
        last_by_handle[h] = r

    # Filter to failed latest
    now = datetime.now(timezone.utc)
    out = []
    for h, r in last_by_handle.items():
        if r.get("ok") is True:
            continue
        if days is not None:
            dt = _parse_iso(r.get("saved_at", "") or "")
            if not dt:
                continue
            if dt < (now - timedelta(days=int(days))):
                continue
        out.append((h, _parse_iso(r.get("saved_at", "") or "")))

    # Sort most recent first and apply limit
    out.sort(key=lambda t: t[1] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    handles = [h for h, _ in out]
    if limit is not None:
        try:
            n = int(limit)
            if n >= 0:
                handles = handles[:n]
        except Exception:
            pass
    return handles

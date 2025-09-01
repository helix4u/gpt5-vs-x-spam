import os, json, time, pathlib
from typing import Dict, Iterable
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

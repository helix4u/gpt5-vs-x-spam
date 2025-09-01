import os, json, httpx, re, logging
from typing import List, Any, Optional, Dict
from .types import Profile, Classification
from .config import settings

SYSTEM = (
    "You label X.com user profiles for impersonation or spam. "
    "Output ONLY JSON. Prefer: {\"classifications\":[{handle,label,confidence,reasons}]} or a plain JSON array. "
    "Valid labels: likely_impersonation, likely_spam, likely_legit, uncertain. "
    "Consider reused celebrity names; lookalike handles with digits; salesy bios; crypto; fake giveaways. "
    "Do not hallucinate. Use only provided fields and handles as given."
)


async def _openai_chat(messages, overrides: Optional[Dict[str, Any]] = None):
    ov = overrides or {}
    provider = ov.get("provider") or settings.llm_provider
    base = ov.get("api_base") or settings.llm_api_base
    model = ov.get("model") or settings.llm_model
    api_key = ov.get("api_key") or settings.openai_api_key

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    async with httpx.AsyncClient(base_url=base, timeout=120) as client:
        payload = {"model": model, "messages": messages, "temperature": 0}
        r = await client.post("/chat/completions", json=payload, headers=headers)
        r.raise_for_status()
        return r.json()


def _strip_code_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        # remove ```json ... ``` fences
        s = re.sub(r"^```[a-zA-Z0-9]*\n", "", s)
        s = re.sub(r"\n```\s*$", "", s)
    return s


def _extract_json_array(s: str) -> Any:
    s = _strip_code_fences(s)
    # Try direct parse
    try:
        return json.loads(s)
    except Exception:
        pass
    # Try to find first well-formed JSON array substring
    start = s.find("[")
    if start != -1:
        depth = 0
        for i, ch in enumerate(s[start:], start=start):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start : i + 1])
                    except Exception:
                        break
    # Try to parse an object and pull classifications/items
    try:
        obj = json.loads(_strip_code_fences(s))
        if isinstance(obj, dict):
            for key in ("classifications", "items", "data", "results"):
                if key in obj and isinstance(obj[key], list):
                    return obj[key]
    except Exception:
        pass
    raise ValueError("could not parse JSON response")


def _normalize_handle(h: str) -> str:
    return (h or "").strip().lstrip("@").lower()


def _map_label(val: str) -> str:
    v = (val or "").strip().lower()
    if v in {"likely_impersonation", "impersonation", "impersonator"}:
        return "likely_impersonation"
    if v in {"likely_spam", "spam"}:
        return "likely_spam"
    if v in {"likely_legit", "legit", "genuine", "real"}:
        return "likely_legit"
    return "uncertain"


def _coerce_output(txt: str, profiles: List[Profile]) -> List[Classification]:
    try:
        data = _extract_json_array(txt)
        if isinstance(data, dict) and "classifications" in data:
            data = data["classifications"]
        if not isinstance(data, list):
            raise ValueError("expected list")

        out: list[Classification] = []
        # map normalized handle -> original scraped handle
        norm_to_orig = {_normalize_handle(p.handle): p.handle for p in profiles}
        seen_norm: set[str] = set()
        for item in data:
            if not isinstance(item, dict):
                continue
            ih = _normalize_handle(str(item.get("handle", "")))
            if ih in norm_to_orig:
                handle = norm_to_orig[ih]
                seen_norm.add(ih)
                label = _map_label(str(item.get("label", "uncertain")))
                try:
                    conf = float(item.get("confidence", 0.5))
                except Exception:
                    conf = 0.5
                reasons = item.get("reasons", [])
                if isinstance(reasons, str):
                    reasons = [reasons]
                if not isinstance(reasons, list):
                    reasons = ["coerce_reasons"]
                out.append(Classification(handle=handle, label=label, confidence=conf, reasons=reasons))
        # fill missing with uncertain but mark reason
        for p in profiles:
            nh = _normalize_handle(p.handle)
            if nh not in seen_norm:
                out.append(Classification(handle=p.handle, label="uncertain", confidence=0.25, reasons=["missing_prediction"]))
        return out
    except Exception as e:
        logging.getLogger(__name__).warning("classifier parse failed: %s", e)
        return [
            Classification(handle=p.handle, label="uncertain", confidence=0.01, reasons=["parse_error"])  # type: ignore[arg-type]
            for p in profiles
        ]


async def classify_profiles(profiles: List[Profile], overrides: Optional[Dict[str, Any]] = None) -> List[Classification]:
    content = json.dumps([p.model_dump() for p in profiles], ensure_ascii=False)
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": content},
    ]
    resp = await _openai_chat(messages, overrides=overrides)
    txt = resp["choices"][0]["message"]["content"]
    return _coerce_output(txt, profiles)

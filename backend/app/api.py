from fastapi import FastAPI, Query, Header, Body
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
import asyncio
from .types import SearchResponse, Profile, Classification, BlockResult
from .scraper import scrape_search_users
from .classifier import classify_profiles
from .actions import block_handles
from .storage import save_classification, read_jsonl
from .config import settings
from fastapi.responses import StreamingResponse
import json
from collections import defaultdict


app = FastAPI(title="gpt5-vs-x-spam")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/api/search", response_model=SearchResponse)
async def api_search(
    query: str = Query(..., min_length=1),
    max_results: int = 30,
    classify: bool = True,
    # optional runtime overrides for LLM
    llm_provider: Optional[str] = None,
    llm_api_base: Optional[str] = None,
    llm_model: Optional[str] = None,
    openai_api_key: Optional[str] = None,
    x_openai_key: Optional[str] = Header(default=None, alias="x-openai-key"),
    headless: Optional[bool] = None,
):
    # Optionally override headless mode at runtime
    if headless is not None:
        settings.headless = bool(headless)

    profiles: List[Profile] = await scrape_search_users(query, max_results=max_results)
    classes: Optional[List[Classification]] = None
    if classify and profiles:
        overrides = {}
        if llm_provider:
            overrides["provider"] = llm_provider
        if llm_api_base:
            overrides["api_base"] = llm_api_base
        if llm_model:
            overrides["model"] = llm_model
        key = openai_api_key or x_openai_key
        if key:
            overrides["api_key"] = key
        classes = await classify_profiles(profiles, overrides=overrides)
        for c in classes:
            save_classification(c)
    return SearchResponse(profiles=profiles, classifications=classes)


@app.post("/api/block", response_model=List[BlockResult])
async def api_block(handles: List[str]):
    return await block_handles(handles)


def _sse_pack(event: str, data) -> str:
    txt = json.dumps(data, ensure_ascii=False)
    # Support multi-line data per SSE rules
    lines = "\n".join([f"data: {line}" for line in txt.splitlines()])
    return f"event: {event}\n{lines}\n\n"


@app.get("/api/search_stream")
async def api_search_stream(
    query: str = Query(..., min_length=1),
    max_results: int = 30,
    classify: bool = True,
    llm_provider: Optional[str] = None,
    llm_api_base: Optional[str] = None,
    llm_model: Optional[str] = None,
    openai_api_key: Optional[str] = None,
):
    async def gen():
        yield _sse_pack("status", {"message": "starting", "query": query})
        yield _sse_pack("status", {"message": "navigating"})
        yield _sse_pack("status", {"message": "scraping"})
        profiles: List[Profile] = await scrape_search_users(query, max_results=max_results)
        yield _sse_pack("status", {"message": "extracted", "count": len(profiles)})
        yield _sse_pack("profiles", [p.model_dump() for p in profiles])
        if classify and profiles:
            yield _sse_pack("status", {"message": "classifying"})
            overrides = {}
            if llm_provider:
                overrides["provider"] = llm_provider
            if llm_api_base:
                overrides["api_base"] = llm_api_base
            if llm_model:
                overrides["model"] = llm_model
            if openai_api_key:
                overrides["api_key"] = openai_api_key
            classes = await classify_profiles(profiles, overrides=overrides)
            for c in classes:
                save_classification(c)
            yield _sse_pack("classification", [c.model_dump() for c in classes])
        yield _sse_pack("done", {"ok": True})

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/history/days")
async def api_history_days():
    rows = read_jsonl(settings.results_path)
    counts = defaultdict(lambda: {"classification": 0, "block": 0})
    for r in rows:
        t = r.get("__type")
        day = (r.get("saved_at") or r.get("scraped_at") or "").split("T")[0] or "unknown"
        if t in ("classification", "block"):
            counts[day][t] = counts[day].get(t, 0) + 1
    days = [
        {"day": d, "counts": v}
        for d, v in sorted(counts.items(), key=lambda kv: kv[0], reverse=True)
    ]
    return {"days": days}


@app.get("/api/history/items")
async def api_history_items(day: str, typ: str = "all", limit: int = 100, offset: int = 0):
    rows = read_jsonl(settings.results_path)
    out = []
    for r in rows:
        t = r.get("__type")
        d = (r.get("saved_at") or r.get("scraped_at") or "").split("T")[0]
        if day and d != day:
            continue
        if typ != "all" and t != typ:
            continue
        out.append(r)
    out.sort(key=lambda x: x.get("saved_at", ""), reverse=True)
    return {"items": out[offset : offset + limit], "total": len(out)}


@app.post("/api/block_from_post", response_model=List[BlockResult])
async def api_block_from_post(payload: dict = Body(...)):
    from .actions import handles_from_post
    url = payload.get("url", "").strip()
    kind = (payload.get("kind") or "any").strip().lower()
    limit = int(payload.get("limit", 100))
    if not url:
        return []
    handles = await handles_from_post(url, kind=kind, limit=limit)
    if not handles:
        return []
    return await block_handles(handles)

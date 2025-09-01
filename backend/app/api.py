from fastapi import FastAPI, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
import asyncio
from .types import SearchResponse, Profile, Classification, BlockResult
from .scraper import scrape_search_users
from .scraper_sync import scrape_search_users_sync as scrape_sync
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

        # progress streaming via queue
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def on_new(added_profiles: List[Profile], count: int, target: int):
            try:
                payload = {
                    "added": [p.model_dump() for p in added_profiles],
                    "count": count,
                    "target": target,
                }
                loop.call_soon_threadsafe(queue.put_nowait, ("progress", payload))
            except Exception:
                pass

        # run scraper in thread
        async def run_scrape():
            return await asyncio.to_thread(scrape_sync, query, max_results, on_new)

        scrape_task = asyncio.create_task(run_scrape())

        profiles: List[Profile] = []
        scraping = True
        while scraping:
            done, pending = await asyncio.wait(
                {scrape_task, asyncio.create_task(queue.get())},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if scrape_task in done:
                profiles = scrape_task.result()
                # drain queue for any remaining progress
                while not queue.empty():
                    kind, data = await queue.get()
                    if kind == "progress":
                        yield _sse_pack("progress", data)
                        if data.get("added"):
                            yield _sse_pack("profiles_chunk", data["added"])  # optional
                scraping = False
                break
            else:
                # we got a progress item
                kind, data = list(done)[0].result()
                if kind == "progress":
                    yield _sse_pack("progress", data)
                    if data.get("added"):
                        yield _sse_pack("profiles_chunk", data["added"])  # optional

        # final payload
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

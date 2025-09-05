from fastapi import FastAPI, Query, Header
import logging
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
import asyncio
from .types import SearchResponse, Profile, Classification, BlockResult
from .scraper_sync import scrape_search_users_sync as scrape_sync, scrape_user_list_sync
from .classifier import classify_profiles
from .actions import block_handles, block_handles_sync, open_login_window_sync
from .pause import pause as pause_scopes, resume as resume_scopes
from .storage import save_classification, read_jsonl, get_failed_block_handles
from .config import settings
from fastapi.responses import StreamingResponse, JSONResponse
import json
from collections import defaultdict
import uuid
from .logging_config import init_logging


init_logging()
logger = logging.getLogger("app.api")
app = FastAPI(title="gpt5-vs-x-spam")

# Track running operations for pause/resume by operation_id
active_operations: dict[str, dict] = {}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    logger.debug("/health")
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
    logger.info("/api/search query=%r max_results=%s classify=%s", query, max_results, classify)
    if headless is not None:
        settings.headless = bool(headless)

    # Use sync Playwright scraper in a worker thread to avoid mixing async/sync Playwright
    try:
        profiles: List[Profile] = await asyncio.to_thread(scrape_sync, query, max_results, None, None)
    except RuntimeError as e:
        if "human_check" in str(e):
            logger.warning("search human_check detected: %s", e)
            return JSONResponse({"error": "human_check", "message": "Human verification required on X. Please resolve in the browser window."}, status_code=403)
        logger.exception("search scrape_failed: %s", e)
        return JSONResponse({"error": "scrape_failed", "message": str(e)}, status_code=500)
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
        logger.info("classifying %d profiles", len(profiles))
        classes = await classify_profiles(profiles, overrides=overrides)
        for c in classes:
            save_classification(c)
    logger.info("/api/search done profiles=%d classified=%s", len(profiles or []), bool(classes))
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
        logger.info("/api/search_stream query=%r max_results=%s classify=%s", query, max_results, classify)
        # operation id for pause/resume
        op_id = uuid.uuid4().hex[:8]
        active_operations[op_id] = {"paused": False, "scope": "scrape"}
        yield _sse_pack("status", {"message": "operation", "operation_id": op_id})
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

        def on_evt(evt: dict):
            try:
                loop.call_soon_threadsafe(queue.put_nowait, ("evt", evt))
            except Exception:
                pass

        # run scraper in thread
        async def run_scrape():
            return await asyncio.to_thread(scrape_sync, query, max_results, on_new, on_evt)

        scrape_task = asyncio.create_task(run_scrape())

        profiles: List[Profile] = []
        scraping = True
        while scraping:
            done, pending = await asyncio.wait(
                {scrape_task, asyncio.create_task(queue.get())},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if scrape_task in done:
                try:
                    profiles = scrape_task.result()
                except Exception as e:
                    # Surface scraper failure as SSE error and terminate stream gracefully
                    logger.exception("search_stream scrape_failed: %s", e)
                    yield _sse_pack("error", {"message": "scrape_failed", "detail": str(e)})
                    yield _sse_pack("done", {"ok": False})
                    return
                # drain queue for any remaining progress/events
                while not queue.empty():
                    kind, data = await queue.get()
                    if kind == "progress":
                        yield _sse_pack("progress", data)
                        if data.get("added"):
                            yield _sse_pack("profiles_chunk", data["added"])  # optional
                    elif kind == "evt":
                        if data.get("kind") == "rate_limit_wait":
                            yield _sse_pack("status", {"message": "rate_limit_wait", "seconds": data.get("seconds", 0)})
                        elif data.get("kind") == "human_check":
                            yield _sse_pack("status", {"message": "human_check"})
                            yield _sse_pack("done", {"ok": False})
                            return
                        elif data.get("kind") == "paused":
                            yield _sse_pack("status", {"message": "paused", "scope": data.get("scope")})
                scraping = False
                break
            else:
                # we got a progress/event item
                kind, data = list(done)[0].result()
                if kind == "progress":
                    yield _sse_pack("progress", data)
                    if data.get("added"):
                        yield _sse_pack("profiles_chunk", data["added"])  # optional
                elif kind == "evt":
                    if data.get("kind") == "rate_limit_wait":
                        yield _sse_pack("status", {"message": "rate_limit_wait", "seconds": data.get("seconds", 0)})
                    elif data.get("kind") == "human_check":
                        logger.warning("search_stream human_check event; terminating")
                        yield _sse_pack("status", {"message": "human_check"})
                        yield _sse_pack("done", {"ok": False})
                        return
                    elif data.get("kind") == "paused":
                        yield _sse_pack("status", {"message": "paused", "scope": data.get("scope")})

        # final payload
        yield _sse_pack("status", {"message": "extracted", "count": len(profiles)})
        yield _sse_pack("profiles", [p.model_dump() for p in profiles])
        if classify and profiles:
            yield _sse_pack("status", {"message": "classifying", "total": len(profiles)})
            overrides = {}
            if llm_provider:
                overrides["provider"] = llm_provider
            if llm_api_base:
                overrides["api_base"] = llm_api_base
            if llm_model:
                overrides["model"] = llm_model
            if openai_api_key:
                overrides["api_key"] = openai_api_key

            # chunked classification
            chunk_size = 25
            all_classes: List[Classification] = []
            for i in range(0, len(profiles), chunk_size):
                chunk = profiles[i:i+chunk_size]
                try:
                    logger.debug("classifying_chunk offset=%d count=%d", i, len(chunk))
                    yield _sse_pack("status", {"message": "classifying_chunk", "offset": i, "count": len(chunk)})
                    chunk_classes = await classify_profiles(chunk, overrides=overrides)
                    for c in chunk_classes:
                        save_classification(c)
                    all_classes.extend(chunk_classes)
                    yield _sse_pack("classification_chunk", [c.model_dump() for c in chunk_classes])
                    # adaptive sleep
                    await asyncio.sleep(1)
                except Exception as e:
                    logger.exception("classification_failed offset=%d: %s", i, e)
                    yield _sse_pack("error", {"message": "classification_failed", "offset": i, "detail": str(e)})

            yield _sse_pack("classification", [c.model_dump() for c in all_classes])
        yield _sse_pack("done", {"ok": True})
        active_operations.pop(op_id, None)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/pause")
async def api_pause(scope: str = Query("all")):
    pause_scopes(scope)
    return {"ok": True, "scope": scope}


@app.post("/api/resume")
async def api_resume(scope: str = Query("all")):
    resume_scopes(scope)
    return {"ok": True, "scope": scope}


@app.post("/api/pause_operation")
async def pause_operation(operation_id: str = Query(...)):
    if operation_id in active_operations:
        active_operations[operation_id]["paused"] = True
        # Map op -> scope pause (global pause manager used by scrapers)
        scope = active_operations[operation_id].get("scope", "scrape")
        pause_scopes(scope)
        return {"status": "paused", "operation_id": operation_id}
    return {"status": "not_found", "operation_id": operation_id}


@app.post("/api/resume_operation")
async def resume_operation(operation_id: str = Query(...)):
    if operation_id in active_operations:
        active_operations[operation_id]["paused"] = False
        scope = active_operations[operation_id].get("scope", "scrape")
        resume_scopes(scope)
        return {"status": "resumed", "operation_id": operation_id}
    return {"status": "not_found", "operation_id": operation_id}


@app.get("/api/user_list_stream")
async def api_user_list_stream(
    user: str = Query(..., min_length=1),
    list_type: str = Query("followers"),
    max_results: int = 100,
    classify: bool = True,
    llm_provider: Optional[str] = None,
    llm_api_base: Optional[str] = None,
    llm_model: Optional[str] = None,
    openai_api_key: Optional[str] = None,
):
    async def gen():
        logger.info("/api/user_list_stream user=%r list_type=%s max_results=%s classify=%s", user, list_type, max_results, classify)
        op_id = uuid.uuid4().hex[:8]
        active_operations[op_id] = {"paused": False, "scope": "scrape"}
        yield _sse_pack("status", {"message": "operation", "operation_id": op_id})
        yield _sse_pack("status", {"message": "starting", "user": user, "list": list_type})
        yield _sse_pack("status", {"message": "navigating"})
        yield _sse_pack("status", {"message": "scraping"})

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

        def on_evt(evt: dict):
            try:
                loop.call_soon_threadsafe(queue.put_nowait, ("evt", evt))
            except Exception:
                pass

        async def run_scrape():
            return await asyncio.to_thread(scrape_user_list_sync, user, list_type, max_results, on_new, on_evt)

        scrape_task = asyncio.create_task(run_scrape())
        profiles: List[Profile] = []
        scraping = True
        while scraping:
            done, pending = await asyncio.wait(
                {scrape_task, asyncio.create_task(queue.get())},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if scrape_task in done:
                try:
                    profiles = scrape_task.result()
                except Exception as e:
                    logger.exception("user_list_stream scrape_failed: %s", e)
                    yield _sse_pack("error", {"message": "scrape_failed", "detail": str(e)})
                    yield _sse_pack("done", {"ok": False})
                    return
                while not queue.empty():
                    kind, data = await queue.get()
                    if kind == "progress":
                        yield _sse_pack("progress", data)
                        if data.get("added"):
                            yield _sse_pack("profiles_chunk", data["added"])  # optional
                    elif kind == "evt":
                        if data.get("kind") == "rate_limit_wait":
                            yield _sse_pack("status", {"message": "rate_limit_wait", "seconds": data.get("seconds", 0)})
                        elif data.get("kind") == "human_check":
                            yield _sse_pack("status", {"message": "human_check"})
                            yield _sse_pack("done", {"ok": False})
                            return
                        elif data.get("kind") == "paused":
                            yield _sse_pack("status", {"message": "paused", "scope": data.get("scope")})
                scraping = False
                break
            else:
                kind, data = list(done)[0].result()
                if kind == "progress":
                    yield _sse_pack("progress", data)
                    if data.get("added"):
                        yield _sse_pack("profiles_chunk", data["added"])  # optional
                elif kind == "evt":
                    if data.get("kind") == "rate_limit_wait":
                        yield _sse_pack("status", {"message": "rate_limit_wait", "seconds": data.get("seconds", 0)})
                    elif data.get("kind") == "human_check":
                        logger.warning("user_list_stream human_check event; terminating")
                        yield _sse_pack("status", {"message": "human_check"})
                        yield _sse_pack("done", {"ok": False})
                        return
                    elif data.get("kind") == "paused":
                        yield _sse_pack("status", {"message": "paused", "scope": data.get("scope")})

        yield _sse_pack("status", {"message": "extracted", "count": len(profiles)})
        yield _sse_pack("profiles", [p.model_dump() for p in profiles])

        if classify and profiles:
            yield _sse_pack("status", {"message": "classifying", "total": len(profiles)})
            overrides = {}
            if llm_provider:
                overrides["provider"] = llm_provider
            if llm_api_base:
                overrides["api_base"] = llm_api_base
            if llm_model:
                overrides["model"] = llm_model
            if openai_api_key:
                overrides["api_key"] = openai_api_key

            # chunked classification
            chunk_size = 25
            all_classes: List[Classification] = []
            for i in range(0, len(profiles), chunk_size):
                chunk = profiles[i:i+chunk_size]
                try:
                    yield _sse_pack("status", {"message": "classifying_chunk", "offset": i, "count": len(chunk)})
                    chunk_classes = await classify_profiles(chunk, overrides=overrides)
                    for c in chunk_classes:
                        save_classification(c)
                    all_classes.extend(chunk_classes)
                    yield _sse_pack("classification_chunk", [c.model_dump() for c in chunk_classes])
                    # adaptive sleep
                    await asyncio.sleep(1)
                except Exception as e:
                    yield _sse_pack("error", {"message": "classification_failed", "offset": i, "detail": str(e)})

            yield _sse_pack("classification", [c.model_dump() for c in all_classes])
        yield _sse_pack("done", {"ok": True})
        active_operations.pop(op_id, None)

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


@app.post("/api/login")
async def api_login():
    logger.info("/api/login")
    ok = await asyncio.to_thread(open_login_window_sync)
    return {"ok": bool(ok)}
@app.get("/api/block_stream")
async def api_block_stream(
    handles: str | None = Query(None, description="comma-separated handles"),
    retry_failed: bool = Query(False, description="if true, ignore handles and retry previously failed"),
    limit: int | None = Query(None, description="max handles to retry when retry_failed is true"),
    days: int | None = Query(None, description="only retry failures from the last N days when retry_failed is true"),
):
    logger.info("/api/block_stream retry_failed=%s limit=%s days=%s handles=%s", retry_failed, limit, days, handles)
    if retry_failed:
        hs = get_failed_block_handles(limit=limit, days=days)
    else:
        if not handles:
            return StreamingResponse(iter([_sse_pack("done", {"ok": False})]), media_type="text/event-stream")
        hs = [h.strip() for h in handles.split(',') if h.strip()]
    async def gen():
        op_id = uuid.uuid4().hex[:8]
        active_operations[op_id] = {"paused": False, "scope": "block"}
        total = len(hs)
        yield _sse_pack("status", {"message": "operation", "operation_id": op_id})
        yield _sse_pack("status", {"message": "starting", "total": total})
        if retry_failed:
            yield _sse_pack("status", {"message": "retrying_failed", "found": total})
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def on_progress(evt: dict):
            try:
                if evt.get("kind") == "progress":
                    logger.debug("block progress %s/%s", evt.get("done"), evt.get("total"))
                elif evt.get("kind") == "rate_limit_wait":
                    logger.debug("block rate_limit_wait %ss", evt.get("seconds"))
                elif evt.get("kind") == "human_check":
                    logger.warning("block human_check event; terminating")
                loop.call_soon_threadsafe(queue.put_nowait, evt)
            except Exception:
                pass

        async def run_block():
            return await asyncio.to_thread(block_handles_sync, hs, on_progress)

        task = asyncio.create_task(run_block())
        results: List[BlockResult] = []
        blocking = True
        while blocking:
            done, pending = await asyncio.wait({task, asyncio.create_task(queue.get())}, return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                try:
                    results = task.result()
                except Exception as e:
                    logger.exception("block_failed: %s", e)
                    yield _sse_pack("error", {"message": "block_failed", "detail": str(e)})
                    yield _sse_pack("done", {"ok": False})
                    return
                # drain
                while not queue.empty():
                    evt = await queue.get()
                    if evt.get("kind") == "progress":
                        yield _sse_pack("progress", evt)
                    elif evt.get("kind") == "rate_limit_wait":
                        yield _sse_pack("status", {"message": "rate_limit_wait", "seconds": evt.get("seconds", 0)})
                    elif evt.get("kind") == "human_check":
                        yield _sse_pack("status", {"message": "human_check"})
                        yield _sse_pack("done", {"ok": False})
                        return
                    elif evt.get("kind") == "paused":
                        yield _sse_pack("status", {"message": "paused", "scope": evt.get("scope")})
                blocking = False
                break
            else:
                evt = list(done)[0].result()
                if evt.get("kind") == "progress":
                    yield _sse_pack("progress", evt)
                elif evt.get("kind") == "rate_limit_wait":
                    yield _sse_pack("status", {"message": "rate_limit_wait", "seconds": evt.get("seconds", 0)})
                elif evt.get("kind") == "human_check":
                    yield _sse_pack("status", {"message": "human_check"})
                    yield _sse_pack("done", {"ok": False})
                    return
                elif evt.get("kind") == "paused":
                    yield _sse_pack("status", {"message": "paused", "scope": evt.get("scope")})

        yield _sse_pack("status", {"message": "completed", "done": len(results), "total": total})
        yield _sse_pack("results", [r.model_dump() for r in results])
        yield _sse_pack("done", {"ok": True})
        active_operations.pop(op_id, None)

    return StreamingResponse(gen(), media_type="text/event-stream")

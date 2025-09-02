from typing import List, Optional, Callable
from playwright.sync_api import TimeoutError as PwTimeout
from .actions import _ensure_ctx
from .config import settings
from .types import Profile
from .storage import write_profile_cache, save_dataset_entry, now_iso

SEARCH_URL = "https://x.com/search?q={q}&src=typed_query&f=user"


def _init_strip_suggestions(page):
    """Remove suggestion sidebars and keep them removed."""
    try:
        page.evaluate(
            """
            (function() {
                const remove = () => {
                    const sel = [
                        'section[aria-label="Who to follow"]',
                        'section[aria-label="Timeline: Who to follow"]',
                        'section[aria-label="You might like"]',
                        'aside[aria-label="Who to follow"]',
                        'aside[aria-label="Timeline: Who to follow"]',
                        'aside[aria-label="You might like"]'
                    ].join(',');
                    document.querySelectorAll(sel).forEach(el => el.remove());
                    try {
                        const xp = '/html/body/div[1]/div/div/div[2]/main/div/div/div/div[2]/div/div[2]/div/div/div/div[4]/div/aside';
                        const node = document.evaluate(xp, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                        if (node) node.remove();
                    } catch {}
                };
                // expose for manual re-use
                window.__removeSuggestions = remove;
                remove();
                if (!window.__removeSuggestionsObserver) {
                    const obs = new MutationObserver(remove);
                    obs.observe(document.body, {subtree: true, childList: true});
                    window.__removeSuggestionsObserver = obs;
                }
            })();
            """
        )
    except Exception:
        pass


def _safe_inner_text(locator) -> str | None:
    try:
        return locator.inner_text()
    except Exception:
        return None


def _parse_profile_cell(cell, query: str) -> Optional[Profile]:
    try:
        texts = cell.locator("span").all_inner_texts()
        handle = next((t for t in texts if t.strip().startswith("@")), None)
        name = texts[0] if texts else None

        bio_el = cell.locator('[dir="auto"]').last
        bio = _safe_inner_text(bio_el)

        a = cell.locator("a").first
        href = a.get_attribute("href")
        profile_url = f"https://x.com{href}" if href and href.startswith("/") else href

        avatar = cell.locator('img[src*="profile_images"]').first
        avatar_url = avatar.get_attribute("src")

        verified_count = cell.locator('[data-testid="icon-verified"]').count()
        verified = verified_count > 0

        if not handle:
            return None
        return Profile(
            name=name,
            handle=handle,
            profile_url=profile_url,
            avatar_url=avatar_url,
            bio=bio,
            verified=verified,
            scraped_at=now_iso(),
            query=query,
        )
    except Exception:
        return None


def _is_suggestion_cell(cell) -> bool:
    """Check if a user cell belongs to a suggestion block."""
    try:
        return cell.evaluate(
            """
            el => el.closest(
                'section[aria-label="Who to follow"],\
                section[aria-label="Timeline: Who to follow"],\
                section[aria-label="You might like"],\
                aside[aria-label="Who to follow"],\
                aside[aria-label="Timeline: Who to follow"],\
                aside[aria-label="You might like"]'
            ) !== null
            """
        )
    except Exception:
        return False


def _collect_profiles_incremental(page, query: str, max_results: int = 40, on_new: Optional[Callable[[List[Profile], int, int], None]] = None) -> List[Profile]:
    """Incrementally harvest visible cells each scroll, tracking uniques.

    This avoids relying on DOM indices (which shift under virtualization)
    and instead parses all currently visible UserCells every iteration.
    """
    cells = page.locator('[data-testid="UserCell"]')
    out_by_handle: dict[str, Profile] = {}
    max_iters = max(10, int(settings.scrape_scroll_max_iters))
    step = max(600, int(settings.scrape_scroll_step_px))
    stable = 0
    last_uniques = 0

    for _ in range(max_iters):
        try:
            page.evaluate("window.__removeSuggestions && window.__removeSuggestions();")
        except Exception:
            pass
        count = cells.count()
        added_step: List[Profile] = []
        # Parse all currently visible cells (windowed list)
        for i in range(count):
            cell_i = cells.nth(i)
            if _is_suggestion_cell(cell_i):
                continue
            prof = _parse_profile_cell(cell_i, query)
            if prof and prof.handle not in out_by_handle:
                out_by_handle[prof.handle] = prof
                added_step.append(prof)
                if len(out_by_handle) >= max_results:
                    break
        # Emit progress
        if on_new and added_step:
            try:
                on_new(added_step, len(out_by_handle), max_results)
            except Exception:
                pass
        # Stop conditions
        if len(out_by_handle) >= max_results:
            break
        # Stability is based on unique growth, not DOM count
        if len(out_by_handle) == last_uniques:
            stable += 1
        else:
            stable = 0
            last_uniques = len(out_by_handle)
        if stable >= max(3, int(settings.scrape_scroll_stable_iters)):
            break
        # Scroll to load more
        try:
            if count > 0:
                cells.nth(count - 1).scroll_into_view_if_needed(timeout=800)
        except Exception:
            pass
        try:
            page.mouse.wheel(0, step)
        except Exception:
            try:
                page.evaluate("window.scrollBy(0, arguments[0])", step)
            except Exception:
                pass
        page.wait_for_timeout(max(400, int(settings.scrape_scroll_wait_ms)))

    return list(out_by_handle.values())


def _scroll_for_more(page, max_results: int):
    cells = page.locator('[data-testid="UserCell"]')
    stable = 0
    last_count = 0
    max_iters = max(10, int(settings.scrape_scroll_max_iters))
    for _ in range(max_iters):
        count = cells.count()
        if count >= max_results:
            break
        # mark stability
        if count == last_count:
            stable += 1
        else:
            stable = 0
            last_count = count
        # bring last cell into view to trigger lazy-loading
        try:
            if count > 0:
                cells.nth(count - 1).scroll_into_view_if_needed(timeout=800)
        except Exception:
            pass
        # scroll
        step = max(600, int(settings.scrape_scroll_step_px))
        try:
            page.mouse.wheel(0, step)
        except Exception:
            try:
                page.evaluate("window.scrollBy(0, arguments[0])", step)
            except Exception:
                pass
        # wait for new cells or timeout (no complex JS to avoid parse issues)
        try:
            page.wait_for_timeout(max(400, int(settings.scrape_scroll_wait_ms)))
        except Exception:
            page.wait_for_timeout(max(250, int(settings.scrape_scroll_wait_ms)))
        # stop if no growth after several steps
        if stable >= max(3, int(settings.scrape_scroll_stable_iters)):
            break


def scrape_search_users_sync(query: str, max_results: int = 40, on_new: Optional[Callable[[List[Profile], int, int], None]] = None) -> List[Profile]:
    pw, browser, ctx, owned = _ensure_ctx()
    if ctx is None:
        return []

    # Reduce automation fingerprints
    try:
        ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)
    except Exception:
        pass

    page = ctx.new_page()
    page.goto(SEARCH_URL.format(q=query))
    # allow manual login... or already logged session cookies
    page.wait_for_timeout(max(800, int(settings.slow_mo_ms)))
    try:
        page.wait_for_selector('[data-testid="UserCell"]', timeout=15000)
    except PwTimeout:
        profiles = []
    else:
        _init_strip_suggestions(page)
        # incrementally collect unique profiles while scrolling (calls on_new as items appear)
        profiles = _collect_profiles_incremental(page, query, max_results=max_results, on_new=on_new)

    # Close only if we created this context here
    if owned:
        try:
            ctx.close()
        except Exception:
            pass
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        try:
            if pw:
                pw.stop()
        except Exception:
            pass

    for prof in profiles:
        write_profile_cache(prof)
        save_dataset_entry(prof)
    return profiles


def scrape_user_list_sync(username: str, list_type: str = "followers", max_results: int = 100, on_new: Optional[Callable[[List[Profile], int, int], None]] = None) -> List[Profile]:
    user = username.lstrip('@')
    segment = "followers" if list_type.lower() == "followers" else "following"
    url = f"https://x.com/{user}/{segment}"
    pw, browser, ctx, owned = _ensure_ctx()
    if ctx is None:
        return []

    try:
        ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)
    except Exception:
        pass

    page = ctx.new_page()
    page.goto(url)
    page.wait_for_timeout(max(800, int(settings.slow_mo_ms)))
    try:
        page.wait_for_selector('[data-testid="UserCell"]', timeout=15000)
    except PwTimeout:
        profiles: List[Profile] = []
    else:
        _init_strip_suggestions(page)
        profiles = _collect_profiles_incremental(page, query=f"{segment}:{user}", max_results=max_results, on_new=on_new)

    if owned:
        try:
            ctx.close()
        except Exception:
            pass
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        try:
            if pw:
                pw.stop()
        except Exception:
            pass

    for prof in profiles:
        write_profile_cache(prof)
        save_dataset_entry(prof)
    return profiles

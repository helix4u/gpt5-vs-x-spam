from typing import List
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
from .config import settings
from .types import Profile
from .storage import write_profile_cache, save_dataset_entry, now_iso

SEARCH_URL = "https://x.com/search?q={q}&src=typed_query&f=user"


def _safe_inner_text(locator) -> str | None:
    try:
        return locator.inner_text()
    except Exception:
        return None


def _extract_profiles(page, query: str, max_results: int = 40) -> List[Profile]:
    results: List[Profile] = []
    cells = page.locator('[data-testid="UserCell"]')
    total = cells.count()
    limit = min(total, max_results)
    for i in range(limit):
        cell = cells.nth(i)
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
                continue
            p = Profile(
                name=name,
                handle=handle,
                profile_url=profile_url,
                avatar_url=avatar_url,
                bio=bio,
                verified=verified,
                scraped_at=now_iso(),
                query=query,
            )
            results.append(p)
        except Exception:
            continue
    return results


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


def scrape_search_users_sync(query: str, max_results: int = 40) -> List[Profile]:
    with sync_playwright() as p:
        # Prefer persistent context to preserve login session
        ctx = None
        browser = None
        if settings.user_data_dir:
            ctx = p.chromium.launch_persistent_context(
                settings.user_data_dir,
                headless=settings.headless,
                args=settings.chromium_args,
                user_agent=settings.user_agent,
                slow_mo=settings.slow_mo_ms,
            )
        else:
            browser = p.chromium.launch(
                headless=settings.headless,
                args=settings.chromium_args,
                slow_mo=settings.slow_mo_ms,
            )
            ctx = browser.new_context(user_agent=settings.user_agent)

        # Reduce automation fingerprints
        ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        page = ctx.new_page()
        page.goto(SEARCH_URL.format(q=query))
        # allow manual login... or already logged session cookies
        page.wait_for_timeout(max(800, int(settings.slow_mo_ms)))
        try:
            page.wait_for_selector('[data-testid="UserCell"]', timeout=15000)
        except PwTimeout:
            profiles = []
        else:
            # attempt to scroll to load more up to requested max_results
            _scroll_for_more(page, max_results=max_results)
            profiles = _extract_profiles(page, query, max_results=max_results)
        if browser:
            browser.close()
        else:
            ctx.close()
    for prof in profiles:
        write_profile_cache(prof)
        save_dataset_entry(prof)
    return profiles

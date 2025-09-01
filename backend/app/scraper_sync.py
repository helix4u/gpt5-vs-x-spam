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


def _scroll_for_more(page, max_results: int, max_scrolls: int = 40, step_px: int = 1400, wait_ms: int = 350):
    cells = page.locator('[data-testid="UserCell"]')
    last = 0
    stable = 0
    for _ in range(max_scrolls):
        count = cells.count()
        if count >= max_results:
            break
        # scroll down by a chunk
        try:
            page.mouse.wheel(0, step_px)
        except Exception:
            try:
                page.evaluate("window.scrollBy(0, arguments[0])", step_px)
            except Exception:
                pass
        page.wait_for_timeout(wait_ms)
        now = cells.count()
        if now <= count:
            stable += 1
        else:
            stable = 0
        if stable >= 3:
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
        page.wait_for_timeout(1500)
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

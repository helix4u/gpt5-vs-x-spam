import asyncio, random, time, re
from typing import List
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
from .config import settings
from .types import BlockResult
from .storage import save_block_result

# Globals to keep an optional login window alive
_LOGIN_STATE = {"pw": None, "browser": None, "ctx": None}

def _ensure_ctx() -> tuple:
    """Get a Playwright sync context, reusing the login context if present.

    Returns (pw, browser, ctx, owned) where owned indicates whether this
    function created a new Playwright instance that should be closed by caller.
    """
    try:
        ctx = _LOGIN_STATE.get("ctx")
        if ctx is not None:
            # Validate the context by creating a temp page
            try:
                p = ctx.new_page()
                p.close()
                return (_LOGIN_STATE.get("pw"), _LOGIN_STATE.get("browser"), ctx, False)
            except Exception:
                # stale context; drop it
                close_login_window_sync()
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = None
        if settings.user_data_dir:
            ctx = pw.chromium.launch_persistent_context(
                settings.user_data_dir,
                headless=settings.headless,
                args=settings.chromium_args,
                user_agent=settings.user_agent,
                slow_mo=settings.slow_mo_ms,
            )
        else:
            browser = pw.chromium.launch(headless=settings.headless, args=settings.chromium_args)
            ctx = browser.new_context(user_agent=settings.user_agent)
        try:
            ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
        except Exception:
            pass
        return (pw, browser, ctx, True)
    except Exception:
        return (None, None, None, False)

def open_login_window_sync(start_url: str = "https://x.com/login") -> bool:
    """Open a visible Chromium window at X login using the persistent profile.

    Keeps Playwright objects alive in module globals so the window stays open
    after the request returns.
    """
    try:
        # Reuse existing context or create a new one (visible)
        ctx = _LOGIN_STATE.get("ctx")
        pw = _LOGIN_STATE.get("pw")
        browser = _LOGIN_STATE.get("browser")
        if ctx is None:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
            browser = None
            if settings.user_data_dir:
                ctx = pw.chromium.launch_persistent_context(
                    settings.user_data_dir,
                    headless=False,
                    args=settings.chromium_args,
                    user_agent=settings.user_agent,
                    slow_mo=settings.slow_mo_ms,
                )
            else:
                browser = pw.chromium.launch(headless=False, args=settings.chromium_args)
                ctx = browser.new_context(user_agent=settings.user_agent)
            try:
                ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
            except Exception:
                pass
        page = ctx.new_page()
        page.goto(start_url)
        _LOGIN_STATE.update({"pw": pw, "browser": browser, "ctx": ctx})
        return True
    except Exception:
        return False

def close_login_window_sync() -> bool:
    try:
        ctx = _LOGIN_STATE.get("ctx")
        br = _LOGIN_STATE.get("browser")
        pw = _LOGIN_STATE.get("pw")
        if ctx:
            try:
                ctx.close()
            except Exception:
                pass
        if br:
            try:
                br.close()
            except Exception:
                pass
        if pw:
            try:
                pw.stop()
            except Exception:
                pass
        _LOGIN_STATE.update({"pw": None, "browser": None, "ctx": None})
        return True
    except Exception:
        return False


class RateLimiterSync:
    def __init__(self, max_actions=800, window_sec=900):
        self.max_actions = max_actions
        self.window_sec = window_sec
        self.actions: list[float] = []

    def tick(self):
        now = time.time()
        self.actions = [t for t in self.actions if now - t < self.window_sec]
        if len(self.actions) >= self.max_actions:
            wait = self.window_sec - (now - self.actions[0]) + 1
            time.sleep(max(1, int(wait)))
            return self.tick()
        self.actions.append(now)

    def jitter(self):
        # Tightened jitter — aim for human-ish but snappy (< ~260ms)
        lo = max(60, min(settings.min_action_jitter_ms, 220))
        hi = max(lo + 30, min(settings.max_action_jitter_ms, 260))
        ms = random.randint(lo, hi)
        time.sleep(ms / 1000.0)


def _click_first(page, selectors: list[str], timeout_ms=6000) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel)
            loc.wait_for(state="visible", timeout=timeout_ms)
            loc.first.click()
            return True
        except Exception:
            continue
    return False


def _ensure_view_profile(page) -> None:
    # Handle restricted interstitials like "This account is temporarily restricted"
    interstitial_selectors = [
        'div[role="button"]:has-text("View profile")',
        'div[role="button"]:has-text("Yes, view profile")',
        'button:has-text("View profile")',
        'button:has-text("View")',
    ]
    if _click_first(page, interstitial_selectors, timeout_ms=1200):
        page.wait_for_timeout(200)


def _home_scroll(page):
    try:
        page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass


def _debug_shot(page, handle: str, tag: str):
    if not settings.debug_screenshots:
        return
    try:
        safe = handle.lstrip('@').replace('/', '_')
        path = f"{settings.screenshot_dir}/{safe}-{int(time.time()*1000)}-{tag}.png"
        page.screenshot(path=path, full_page=False)
    except Exception:
        pass


def _open_overflow(page) -> bool:
    """Open the profile header 'More' menu using robust fallbacks.

    Order:
    1) button[data-testid=userActions]
    2) [data-testid=userActions][role=button] or its inner [data-testid=overflow]
    3) Any role=button with aria-label containing "More", excluding sidebar/live widgets
    """
    # 1) Exact button variant
    try:
        btn = page.locator('button[data-testid="userActions"]').first
        btn.wait_for(state="visible", timeout=1200)
        btn.click()
        page.locator('div[role="menu"]').first.wait_for(state="visible", timeout=1200)
        return True
    except Exception:
        pass

    # 2) Container acting as a button or inner overflow
    try:
        ua = page.locator('[data-testid="userActions"]').first
        ua.wait_for(state="attached", timeout=1200)
        role = (ua.get_attribute('role') or '').lower()
        aria = (ua.get_attribute('aria-label') or '').lower()
        if role == 'button' or ('more' in aria):
            ua.click()
            page.locator('div[role="menu"]').first.wait_for(state="visible", timeout=1200)
            return True
        el = ua.locator('[data-testid="overflow"]').first
        el.wait_for(state="visible", timeout=1200)
        el.click()
        page.locator('div[role="menu"]').first.wait_for(state="visible", timeout=1200)
        return True
    except Exception:
        pass

    # 3) Global fallback: any "More" button not in sidebar/live/placement widgets
    try:
        ok = page.evaluate(
            """
            () => {
              const isBad = (el) => {
                // Exclude right sidebar modules and space/live widgets
                if (el.closest('[data-testid="sidebarColumn"]')) return true;
                if (el.closest('[data-testid="placementTracking"]')) return true;
                // Many space/live buttons wrap the pill contents inside the button
                // so we also check descendants instead of ancestors only
                if (el.querySelector('[data-testid="pill-contents-container"]')) return true;
                const aria = ((el.getAttribute('aria-label')||'') + ' ' + ((el.closest('[aria-label]')||{}).getAttribute?.('aria-label')||'')).toLowerCase();
                if (aria.includes('broadcast') || aria.includes('space')) return true;
                return false;
              };
              const byAria = Array.from(document.querySelectorAll('button[role="button"][aria-label]'));
              for (const b of byAria) {
                const label = (b.getAttribute('aria-label')||'').toLowerCase();
                if (!label.includes('more')) continue;
                if (isBad(b)) continue;
                try { b.click(); return true; } catch {}
              }
              const all = Array.from(document.querySelectorAll('[role="button"]'));
              for (const b of all) {
                const t = (b.innerText||'').trim().toLowerCase();
                if (t==='more' || t==='more actions') {
                  if (isBad(b)) continue;
                  try { b.click(); return true; } catch {}
                }
              }
              return false;
            }
            """
        )
        if ok:
            page.locator('div[role="menu"]').first.wait_for(state="visible", timeout=1200)
            return True
    except Exception:
        pass
    return False


def _confirm_block(page) -> bool:
    if _click_first(
        page,
        [
            'div[role="button"][data-testid="confirmationSheetConfirm"]',
            'div[data-testid="confirmationSheetConfirm"]',
            'div[role="dialog"] button:has-text("Block")',
        ],
        timeout_ms=1500,
    ):
        return True
    # JS fallback inside dialog/sheet: click any button whose text starts with Block
    try:
        return bool(
            page.evaluate(
                """
                () => {
                  const root = document.querySelector('[data-testid="sheetDialog"]') || document.querySelector('div[role="dialog"]') || document;
                  const nodes = Array.from(root.querySelectorAll('button,[role="button"],div[role="button"]'));
                  for (const el of nodes) {
                    const t = (el.innerText||'').trim().toLowerCase();
                    if (t.startsWith('block')) { try { el.click(); return true; } catch {} }
                  }
                  return false;
                }
                """
            )
        )
    except Exception:
        return False


def _block_ui_sync(page) -> bool:
    if not _open_overflow(page):
        return False
    try:
        menu = page.locator('div[role="menu"]').first
        menu.wait_for(state="visible", timeout=1500)
    except Exception:
        return False
    # Try a series of strategies to click Block (menu-scoped)
    clicked = _click_first(
        menu,
        [
            'div[role="menuitem"]:has-text("Block ")',
            'div[role="menuitem"]:has-text("Block @")',
            'div[role="menuitem"]:has-text("Block")',
            '[data-testid="block"]',
            'button:has-text("Block")',
        ],
        timeout_ms=1500,
    )
    if not clicked:
        # Role-based query then JS text match
        try:
            menu.get_by_role("menuitem", name=re.compile(r"^Block", re.I)).first.click(timeout=1200)
            clicked = True
        except Exception:
            try:
                clicked = bool(
                    page.evaluate(
                        """
                        () => {
                          const menu = document.querySelector('div[role="menu"]');
                          if(!menu) return false;
                          const nodes = Array.from(menu.querySelectorAll('[role="menuitem"],button,div'));
                          for (const el of nodes) {
                            const t = (el.innerText||'').trim().toLowerCase();
                            if (t.startsWith('block')) { try { (el.querySelector('button,div,span')||el).click(); return true; } catch {} }
                          }
                          return false;
                        }
                        """
                    )
                )
            except Exception:
                clicked = False
    if not clicked:
        return False
    if not _confirm_block(page):
        return False
    # Minimal settle
    page.wait_for_timeout(120)
    return True


def block_handles_sync(handles: List[str]) -> List[BlockResult]:
    rl = RateLimiterSync(max_actions=settings.actions_per_15min)
    out: List[BlockResult] = []
    pw, browser, ctx, owned = _ensure_ctx()
    if ctx is None:
        return [BlockResult(handle=h, ok=False, error="ctx_unavailable") for h in handles]
    page = ctx.new_page()
    try:
        page.set_viewport_size({"width": 1280, "height": 900})
    except Exception:
        pass
    try:
        page.set_default_timeout(1500)
        page.set_default_navigation_timeout(5000)
    except Exception:
        pass

    for h in handles:
        handle = h if h.startswith("@") else f"@{h}"
        url = f"https://x.com/{handle.lstrip('@')}"
        try:
            rl.tick()
            page.goto(url, wait_until="domcontentloaded", timeout=5000)
            _ensure_view_profile(page)
            _home_scroll(page)
            _debug_shot(page, handle, "loaded")

            # If not logged in UI is missing actions; give clearer error
            try:
                page.locator('[data-testid="userActions"]').wait_for(state="attached", timeout=1200)
            except Exception:
                _debug_shot(page, handle, "no_user_actions")
                out.append(BlockResult(handle=handle, ok=False, error="not_logged_in_or_ui_changed"))
                rl.jitter()
                continue

            ok = _block_ui_sync(page)
            _debug_shot(page, handle, "after_block_attempt")
            rl.jitter()
            br = BlockResult(handle=handle, ok=ok, error=None if ok else "ui_failed")
            out.append(br)
            try:
                save_block_result(br)
            except Exception:
                pass
        except PwTimeout:
            _debug_shot(page, handle, "timeout")
            br = BlockResult(handle=handle, ok=False, error="timeout")
            out.append(br)
            try:
                save_block_result(br)
            except Exception:
                pass
        except Exception as e:
            _debug_shot(page, handle, "exception")
            br = BlockResult(handle=handle, ok=False, error=str(e))
            out.append(br)
            try:
                save_block_result(br)
            except Exception:
                pass

    # Close only if we created this context locally
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
    return out


async def block_handles(handles: List[str]) -> List[BlockResult]:
    # Run sync flow in a worker thread to avoid Windows asyncio subprocess issues
    return await asyncio.to_thread(block_handles_sync, handles)


# report stub... platform ui and reasons vary... intentionally minimal
async def report_handles(handles: List[str], reason_text: str = "impersonation"):
    return [{"handle": h, "ok": False, "error": "report_stub"} for h in handles]

import asyncio, random, time, re
from typing import List, Callable
import logging
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
from .config import settings
from .types import BlockResult
from .storage import save_block_result
from .pause import wait_if_paused
from .detect import is_human_check as _is_human_check

logger = logging.getLogger("app.actions")

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
        logger.debug("created new Playwright context (owned=%s)", True)
        return (pw, browser, ctx, True)
    except Exception:
        logger.exception("failed to create Playwright context")
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
        logger.exception("failed to open login window")
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
        logger.exception("failed to close login window")
        return False


class RateLimiterSync:
    def __init__(self, max_actions=800, window_sec=900):
        self.max_actions = max_actions
        self.window_sec = window_sec
        self.actions: list[float] = []

    def tick(self, on_wait: Callable[[int], None] | None = None) -> int:
        """Record an action; if at limit, sleep until a slot frees.

        Returns the total seconds slept (0 if none). Optionally invokes
        on_wait(seconds_remaining) every second while waiting so UIs can
        render an accurate countdown.
        """
        slept = 0
        while True:
            now = time.time()
            self.actions = [t for t in self.actions if now - t < self.window_sec]
            if len(self.actions) < self.max_actions:
                self.actions.append(now)
                return int(slept)
            # seconds until the oldest action ages out of the window
            wait_total = self.window_sec - (now - self.actions[0]) + 1
            remaining = max(1, int(wait_total))
            # Emit a live countdown and sleep in 1s steps
            end = time.time() + remaining
            while True:
                rem = int(max(0, round(end - time.time())))
                if rem <= 0:
                    break
                if on_wait:
                    try:
                        on_wait(rem)
                    except Exception:
                        pass
                time.sleep(1)
                slept += 1

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


def _strip_placements(page) -> None:
    """Physically remove placement/Spaces widgets to prevent any interaction.

    This is a belt-and-suspenders approach in addition to pointer-events: none.
    """
    try:
        page.evaluate(
            """
            () => {
              try {
                document.querySelectorAll('[data-testid="placementTracking"]').forEach(n => n.remove());
              } catch {}
            }
            """
        )
    except Exception:
        pass


def _dismiss_spaces_dialog(page) -> None:
    try:
        page.evaluate(
            """
            () => {
              try {
                const dialogs = Array.from(document.querySelectorAll('div[role="dialog"]'));
                for (const d of dialogs){
                  const t = (d.innerText||'').toLowerCase();
                  if (t.includes('start listening') || t.includes('listen anonymously') || t.includes('space ')){
                    const btn = d.querySelector('[aria-label="Close"], [aria-label*="close" i], [data-testid="modalClose"], [role="button"][aria-label*="close" i]');
                    if (btn) { try { btn.click(); } catch {} }
                  }
                }
              } catch {}
            }
            """
        )
    except Exception:
        pass


def _is_forbidden_click(locator) -> bool:
    """Return True if the node is part of Spaces/Live/placement/sidebar widgets.

    This protects against accidental clicks even when fallback JS would match
    an element inside primary content that still represents a Space/placement.
    """
    try:
        return bool(
            locator.evaluate(
                """
                (el) => {
                  const xAllow = '/html/body/div[1]/div/div/div[2]/main/div/div/div/div[1]';
                  const allowRoot = document.evaluate(xAllow, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                  const inAllowRoot = !!(allowRoot && (el === allowRoot || allowRoot.contains(el)));
                  if (!inAllowRoot) return true;
                  const has = (n, sel) => !!(n && (n.matches?.(sel) || n.closest?.(sel)));
                  if (has(el, '[data-testid="sidebarColumn"]')) return true;
                  if (has(el, '[data-testid="placementTracking"]')) return true;
                  if (el.querySelector?.('[data-testid="pill-contents-container"]')) return true;
                  // Heuristics on labels/text
                  const badTerms = ['space', 'broadcast', 'is hosting', 'is listening', 'live on x'];
                  const text = (el.innerText||'').toLowerCase();
                  if (badTerms.some(t => text.includes(t))) return true;
                  let n = el;
                  while (n && n.nodeType === 1){
                    const aria = (n.getAttribute?.('aria-label')||'').toLowerCase();
                    if (badTerms.some(t => aria.includes(t))) return true;
                    if (n.getAttribute?.('data-testid') === 'primaryColumn') break;
                    n = n.parentElement;
                  }
                  // Geometric guard: click center must lie within allowRoot rect
                  try {
                    const r = el.getBoundingClientRect();
                    const cx = r.left + r.width/2, cy = r.top + r.height/2;
                    const ar = allowRoot.getBoundingClientRect();
                    if (!(cx >= ar.left && cx <= ar.right && cy >= ar.top && cy <= ar.bottom)) return true;
                  } catch {}
                  return false;
                }
                """
            )
        )
    except Exception:
        return False


def _safe_click(locator, wait_menu=False) -> bool:
    try:
        locator.wait_for(state="visible", timeout=1200)
        if _is_forbidden_click(locator):
            return False
        # One more sweep before we click
        try:
            pg = locator.page
            _strip_placements(pg)
            _dismiss_spaces_dialog(pg)
        except Exception:
            pass
        locator.click()
        if wait_menu:
            locator.page.locator('div[role="menu"]').last.wait_for(state="visible", timeout=1200)
        return True
    except Exception:
        return False


def _open_overflow(page) -> bool:
    """Open the profile header 'More' menu using robust fallbacks.

    Order:
    1) button[data-testid=userActions]
    2) [data-testid=userActions][role=button] or its inner [data-testid=overflow]
    3) Any role=button with aria-label containing "More", excluding sidebar/live widgets
    """
    # Scope all queries to the allowed root only; if not present, abort to avoid sidebar clicks
    try:
        primary = page.locator('xpath=/html/body/div[1]/div/div/div[2]/main/div/div/div/div[1]').first
        primary.wait_for(state="attached", timeout=2000)
    except Exception:
        return False

    # Hard safety: disable pointer events in sidebar and forbidden root so accidental clicks do nothing
    try:
        page.evaluate(
            """
            () => {
              // Disable entire sidebar by data-testid
              if (!document.getElementById('aa-no-click-sidebar-style')) {
                const st = document.createElement('style');
                st.id = 'aa-no-click-sidebar-style';
                st.textContent = [
                  '[data-testid="sidebarColumn"], [data-testid="sidebarColumn"] * { pointer-events: none !important; }',
                  '[data-testid="placementTracking"], [data-testid="placementTracking"] * { pointer-events: none !important; }',
                  'button[aria-label*="space" i], [role="button"][aria-label*="space" i] { pointer-events: none !important; }',
                  'button[aria-label*="broadcast" i], [role="button"][aria-label*="broadcast" i] { pointer-events: none !important; }'
                ].join('\n');
                document.head.appendChild(st);
              }
              // Disable the explicit forbidden root via XPath
              try {
                const xp = '//*[@id="react-root"]/div/div/div[2]/main/div/div/div/div[2]';
                const node = document.evaluate(xp, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                if (node) node.style.pointerEvents = 'none';
              } catch {}
            }
            """
        )
    except Exception:
        pass

    # Remove placements proactively and dismiss any Spaces dialog
    _strip_placements(page)
    _dismiss_spaces_dialog(page)

    # 1) Exact button variant (inside primaryColumn)
    try:
        btn = primary.locator('button[data-testid="userActions"]').first
        if _safe_click(btn, wait_menu=True):
            return True
    except Exception:
        pass

    # 2) Container acting as a button or inner overflow (inside primaryColumn)
    try:
        ua = primary.locator('[data-testid="userActions"]').first
        ua.wait_for(state="attached", timeout=1200)
        role = (ua.get_attribute('role') or '').lower()
        aria = (ua.get_attribute('aria-label') or '').lower()
        if role == 'button' or ('more' in aria):
            if _safe_click(ua, wait_menu=True):
                return True
        el = ua.locator('[data-testid="overflow"]').first
        if _safe_click(el, wait_menu=True):
            return True
    except Exception:
        pass

    # 3) Fallback: search for a "More" button strictly inside primaryColumn using locators
    try:
        # Candidates likely to be the overflow toggle; iterate a few and click the first safe one
        candidate_selectors = [
            'button[aria-label*="More" i]',
            '[role="button"]:has-text("More actions")',
            '[role="button"]:has-text("More")',
        ]
        for sel in candidate_selectors:
            try:
                locs = primary.locator(sel)
                cnt = min(max(locs.count(), 0), 5)
                for i in range(cnt):
                    cand = locs.nth(i)
                    if _safe_click(cand, wait_menu=True):
                        return True
            except Exception:
                continue
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
        menu = page.locator('div[role="menu"]').last
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
                          const primary = document.querySelector('[data-testid="primaryColumn"]') || document.body;
                          const prect = primary.getBoundingClientRect();
                          const menus = Array.from(document.querySelectorAll('div[role="menu"]'));
                          // Prefer the last visible menu whose horizontal center overlaps the primary column
                          const candidates = menus.filter(m => {
                            const r = m.getBoundingClientRect();
                            const cx = r.left + r.width/2;
                            return r.width > 0 && r.height > 0 && cx >= prect.left && cx <= prect.right;
                          });
                          const menu = (candidates.at(-1)) || (menus.at(-1)) || menus[0];
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


def block_handles_sync(handles: List[str], on_progress=None) -> List[BlockResult]:
    logger.info("block_handles_sync starting count=%d", len(handles))
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

    abort = False
    for h in handles:
        # pause support between handles
        wait_if_paused("block", on_progress)
        handle = h if h.startswith("@") else f"@{h}"
        url = f"https://x.com/{handle.lstrip('@')}"
        try:
            def _notify_wait(sec: int):
                if on_progress:
                    try:
                        on_progress({"kind": "rate_limit_wait", "seconds": int(sec)})
                    except Exception:
                        pass
            # Honor pause before consuming a rate-limit slot
            wait_if_paused("block", on_progress)
            logger.debug("blocking %s", handle)
            rl.tick(on_wait=_notify_wait)
            page.goto(url, wait_until="domcontentloaded", timeout=5000)
            # Human-check detection
            try:
                if _is_human_check(page):
                    if on_progress:
                        try:
                            on_progress({"kind": "human_check"})
                        except Exception:
                            pass
                    logger.warning("human_check detected while blocking %s", handle)
                    abort = True
                    break
            except Exception:
                pass
            # Preemptively remove placements/spaces and disable sidebar clicks before any other interaction
            try:
                _strip_placements(page)
                page.evaluate(
                    """
                    () => {
                      if (!document.getElementById('aa-no-click-sidebar-style')) {
                        const st = document.createElement('style');
                        st.id = 'aa-no-click-sidebar-style';
                        st.textContent = [
                          '[data-testid="sidebarColumn"], [data-testid="sidebarColumn"] * { pointer-events: none !important; }',
                          '[data-testid="placementTracking"], [data-testid="placementTracking"] * { pointer-events: none !important; }',
                          'button[aria-label*="space" i], [role="button"][aria-label*="space" i] { pointer-events: none !important; }',
                          'button[aria-label*="broadcast" i], [role="button"][aria-label*="broadcast" i] { pointer-events: none !important; }'
                        ].join('\n');
                        document.head.appendChild(st);
                      }
                    }
                    """
                )
            except Exception:
                pass
            _ensure_view_profile(page)
            _home_scroll(page)
            _debug_shot(page, handle, "loaded")

            # Ensure the primary column is present and contains the actions
            try:
                primary = page.locator('xpath=/html/body/div[1]/div/div/div[2]/main/div/div/div/div[1]').first
                primary.wait_for(state="attached", timeout=2000)
                primary.locator('[data-testid="userActions"]').first.wait_for(state="attached", timeout=1500)
            except Exception:
                _debug_shot(page, handle, "no_user_actions_or_primary")
                out.append(BlockResult(handle=handle, ok=False, error="not_logged_in_or_ui_changed_or_no_primary"))
                rl.jitter()
                continue

            ok = _block_ui_sync(page)
            _debug_shot(page, handle, "after_block_attempt")
            # honor pause before post-action jitter
            wait_if_paused("block", on_progress)
            rl.jitter()
            br = BlockResult(handle=handle, ok=ok, error=None if ok else "ui_failed")
            out.append(br)
            try:
                save_block_result(br)
            except Exception:
                pass
            logger.info("block result handle=%s ok=%s", handle, ok)
            # progress callback
            if on_progress:
                try:
                    on_progress({"kind": "progress", "done": len(out), "total": len(handles), "result": br.model_dump()})
                except Exception:
                    pass
        except PwTimeout:
            _debug_shot(page, handle, "timeout")
            br = BlockResult(handle=handle, ok=False, error="timeout")
            out.append(br)
            logger.warning("timeout blocking %s", handle)
            try:
                save_block_result(br)
            except Exception:
                pass
            if on_progress:
                try:
                    on_progress({"kind": "progress", "done": len(out), "total": len(handles), "result": br.model_dump()})
                except Exception:
                    pass
        except Exception as e:
            _debug_shot(page, handle, "exception")
            br = BlockResult(handle=handle, ok=False, error=str(e))
            out.append(br)
            logger.exception("exception blocking %s: %s", handle, e)
            try:
                save_block_result(br)
            except Exception:
                pass
            if on_progress:
                try:
                    on_progress({"kind": "progress", "done": len(out), "total": len(handles), "result": br.model_dump()})
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
    logger.info("block_handles_sync done count=%d", len(out))
    return out


async def block_handles(handles: List[str]) -> List[BlockResult]:
    # Run sync flow in a worker thread to avoid Windows asyncio subprocess issues
    return await asyncio.to_thread(block_handles_sync, handles)


# report stub... platform ui and reasons vary... intentionally minimal
async def report_handles(handles: List[str], reason_text: str = "impersonation"):
    return [{"handle": h, "ok": False, "error": "report_stub"} for h in handles]

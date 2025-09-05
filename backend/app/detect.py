from __future__ import annotations
import logging

logger = logging.getLogger("app.detect")

def is_human_check(page) -> bool:
    """Detect human verification/captcha or access challenges on X.

    Heuristics-based: checks URL, common text markers, and challenge iframes.
    """
    try:
        url = (page.url or "").lower()
    except Exception:
        url = ""
    try:
        body_text = (page.inner_text("body") or "").lower()
    except Exception:
        body_text = ""
    # URL patterns seen during challenges
    if any(k in url for k in ["/account/access", "/challenge", "/captcha", "verify", "safety"]):
        return True
    # Textual markers
    markers = [
        "captcha",
        "recaptcha",
        "are you a robot",
        "confirm you are a human",
        "help us confirm",
        "verify your identity",
        "unusual activity",
        "suspicious activity",
        "complete a quick check",
    ]
    if any(m in body_text for m in markers):
        return True
    # reCAPTCHA/Arkose iframes
    try:
        if page.locator('iframe[src*="recaptcha"], iframe[title*="challenge" i]').count() > 0:
            return True
    except Exception:
        pass
    return False

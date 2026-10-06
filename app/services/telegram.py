import logging

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)


def configured() -> bool:
    s = get_settings()
    return bool(s.telegram_bot_token and s.telegram_chat_id)


def send(text: str) -> bool:
    s = get_settings()
    if not configured():
        log.info("telegram not configured; alert skipped")
        return False
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage",
            json={"chat_id": s.telegram_chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": False},
            timeout=20,
        )
        if r.status_code != 200:
            log.warning("telegram send failed", extra={"status": r.status_code, "body": r.text[:300]})
            return False
        return True
    except httpx.HTTPError as exc:
        log.warning("telegram error", extra={"err": str(exc)})
        return False

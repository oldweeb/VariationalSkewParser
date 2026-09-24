"""Report a Variational market's OI-limit status to Telegram.

Markets are defined in ``MARKETS`` so expanding beyond ONE only requires adding
another Market entry; the polling and alert state are maintained per market.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html import escape
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class Market:
    underlying: str
    instrument_type: str


# Add new markets here when they need monitoring.
MARKETS: tuple[Market, ...] = (Market("ONE", "perpetual_future"),)
API_BASE_URL = "https://omni.variational.io/api/metadata/v2/risk_limits"


class LiveMessageUnavailableError(RuntimeError):
    """Telegram explicitly says that the saved live message cannot be edited."""


def decimal_value(value: Any, field_name: str) -> Decimal:
    """Convert an API numeric field to Decimal without precision loss."""
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{field_name} is missing or not numeric")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name} is not numeric: {value!r}") from exc


def find_risk_limit(payload: Any) -> tuple[Decimal, Decimal]:
    """Find the record containing both required fields in a flexible API body.

    The endpoint may return one record directly, a list, or wrap results under a
    key such as ``data``. Keeping this traversal here isolates API-shape changes.
    """
    if isinstance(payload, dict):
        if "current_skew_usd" in payload and "skew_limit_usd" in payload:
            return (
                decimal_value(payload["current_skew_usd"], "current_skew_usd"),
                decimal_value(payload["skew_limit_usd"], "skew_limit_usd"),
            )
        for value in payload.values():
            try:
                return find_risk_limit(value)
            except LookupError:
                continue
    elif isinstance(payload, list):
        for value in payload:
            try:
                return find_risk_limit(value)
            except LookupError:
                continue
    raise LookupError("Response contains no current_skew_usd/skew_limit_usd pair")


def is_oi_limit_reached(current_skew: Decimal, skew_limit: Decimal) -> bool:
    """Variational reaches the OI limit once current skew exceeds its limit."""
    return current_skew > skew_limit


def fetch_risk_limit(market: Market, timeout: float) -> tuple[Decimal, Decimal]:
    url = f"{API_BASE_URL}?{urlencode({'underlying': market.underlying, 'instrument_type': market.instrument_type})}"
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "variational-skew-monitor/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return find_risk_limit(json.load(response))


def format_oi_limit_message(market: Market, current_skew: Decimal, skew_limit: Decimal, oi_limit_reached: bool) -> str:
    """Create the startup/transition message sent to Telegram."""
    status = "🔴 <b>OI limit reached</b>" if oi_limit_reached else "🟢 <b>OI limit not reached</b>"
    return (
        f"<b>{escape(market.underlying)} OI status</b>\n"
        f"{status}\n\n"
        f"Current skew\n<code>${current_skew:,.2f}</code>\n\n"
        f"Skew limit\n<code>${skew_limit:,.2f}</code>"
    )


def format_live_status_message(
    market: Market, current_skew: Decimal, skew_limit: Decimal, oi_limit_reached: bool, observed_at: datetime
) -> str:
    """Create the single message that is edited on every successful poll."""
    status = "🔴 <b>OI limit reached</b>" if oi_limit_reached else "🟢 <b>OI limit not reached</b>"
    return (
        f"<b>{escape(market.underlying)} OI · Live status</b>\n"
        f"{status}\n\n"
        f"Current skew\n<code>${current_skew:,.2f}</code>\n\n"
        f"Skew limit\n<code>${skew_limit:,.2f}</code>\n\n"
        f"Updated <code>{observed_at:%Y-%m-%d %H:%M:%S UTC}</code>"
    )


def telegram_request(token: str, method: str, body: dict[str, str | int], timeout: float) -> dict[str, Any]:
    """Call the Telegram Bot API and return its JSON response."""
    request = Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=urlencode(body).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        result = json.load(response)
    if not result.get("ok"):
        raise RuntimeError(f"Telegram rejected {method}: {result}")
    return result


def send_telegram_alert(token: str, chat_id: str, message: str, timeout: float) -> int:
    """Send a Telegram message and return its ID for possible future edits."""
    result = telegram_request(token, "sendMessage", {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}, timeout)
    try:
        return int(result["result"]["message_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Telegram returned no message ID: {result}") from exc


def edit_telegram_message(token: str, chat_id: str, message_id: int, message: str, timeout: float) -> None:
    """Update the existing live-status message without adding chat noise."""
    try:
        telegram_request(
            token,
            "editMessageText",
            {"chat_id": chat_id, "message_id": message_id, "text": message, "parse_mode": "HTML"},
            timeout,
        )
    except HTTPError as exc:
        # Only a definitive Telegram 400 response should make us discard the
        # ID. Transient network failures can happen after Telegram has already
        # processed the edit, so replacing the card would create chat spam.
        try:
            error = json.loads(exc.read().decode("utf-8", errors="replace"))
            description = str(error.get("description", "")).lower()
        except (json.JSONDecodeError, UnicodeDecodeError):
            description = ""
        unavailable_markers = (
            "message to edit not found",
            "message can't be edited",
            "message_id_invalid",
        )
        if exc.code == 400 and any(marker in description for marker in unavailable_markers):
            raise LiveMessageUnavailableError(description) from exc
        raise


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Set {name} before starting the monitor")
    return value


def load_dotenv(path: str = ".env") -> None:
    """Load simple KEY=VALUE entries without overriding shell environment values."""
    try:
        with open(path, encoding="utf-8") as dotenv:
            for raw_line in dotenv:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


def main() -> None:
    load_dotenv()
    token = required_env("TELEGRAM_BOT_TOKEN")
    chat_id = required_env("TELEGRAM_CHAT_ID")
    interval = float(os.getenv("POLL_INTERVAL_SECONDS", "1"))
    timeout = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "10"))
    if interval <= 0 or timeout <= 0:
        raise RuntimeError("POLL_INTERVAL_SECONDS and REQUEST_TIMEOUT_SECONDS must be positive")

    # None means no status has been reported yet. The first successful poll
    # posts the bot's initial state; later messages are transition-only.
    previous_oi_limit_reached: dict[Market, bool | None] = {market: None for market in MARKETS}
    # One editable dashboard message per market. It is recreated if the user
    # deletes it or Telegram cannot edit it anymore.
    live_message_ids: dict[Market, int | None] = {market: None for market in MARKETS}
    logging.info("Started monitoring %s every %ss", ", ".join(m.underlying for m in MARKETS), interval)

    while True:
        started = time.monotonic()
        for market in MARKETS:
            try:
                current_skew, skew_limit = fetch_risk_limit(market, timeout)
                oi_limit_reached = is_oi_limit_reached(current_skew, skew_limit)
                previous = previous_oi_limit_reached[market]
                logging.info(
                    "%s: current_skew_usd=%s, skew_limit_usd=%s, oi_limit_reached=%s",
                    market.underlying,
                    current_skew,
                    skew_limit,
                    oi_limit_reached,
                )
                if previous is None or previous != oi_limit_reached:
                    message = format_oi_limit_message(market, current_skew, skew_limit, oi_limit_reached)
                    send_telegram_alert(token, chat_id, message, timeout)
                    logging.warning("OI-limit status sent for %s: %s", market.underlying, oi_limit_reached)
                previous_oi_limit_reached[market] = oi_limit_reached

                live_message = format_live_status_message(
                    market, current_skew, skew_limit, oi_limit_reached, datetime.now(timezone.utc)
                )
                try:
                    message_id = live_message_ids[market]
                    if message_id is None:
                        live_message_ids[market] = send_telegram_alert(token, chat_id, live_message, timeout)
                        logging.info("Created live-status message for %s", market.underlying)
                    else:
                        edit_telegram_message(token, chat_id, message_id, live_message, timeout)
                except LiveMessageUnavailableError:
                    # Telegram explicitly says this message no longer exists
                    # or is not editable, so a replacement is appropriate.
                    live_message_ids[market] = None
                    logging.warning("Live-status message is unavailable for %s; will recreate it", market.underlying)
                except (HTTPError, URLError, TimeoutError):
                    # Keep the ID on temporary Telegram/network failures and
                    # retry the edit on the next poll instead of posting anew.
                    logging.warning("Temporary failure updating live status for %s; keeping current message", market.underlying)
                except Exception:
                    # Unknown failures are also non-destructive to the saved
                    # ID; an operator can see the traceback without chat spam.
                    logging.exception("Could not update live status for %s; keeping current message", market.underlying)
            except (HTTPError, URLError, TimeoutError, ValueError, LookupError, json.JSONDecodeError) as exc:
                logging.error("Could not poll %s: %s", market.underlying, exc)
            except Exception:
                # Credentials/configuration failures should be visible with a traceback.
                logging.exception("Unexpected failure while polling %s", market.underlying)

        time.sleep(max(0, interval - (time.monotonic() - started)))


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(message)s")
    main()

# Variational skew monitor

Polls Variational's Risk Limits endpoint every second for `ONE` perpetual
futures. The monitor defines `oi_limit_reached` as
`current_skew_usd > skew_limit_usd`.

It posts the current `oi_limit_reached` state to Telegram after the first
successful request on startup, then posts again only when that state changes
between `true` and `false`. Messages use Telegram HTML formatting and show a
red/green status indicator plus human-readable USD values.

In addition, the bot maintains a separate **Live status** message. On each
successful one-second poll it edits this one message with the latest values and
UTC timestamp, so the chat does not fill with routine updates. If that message
is deleted, the bot creates a replacement on the next successful poll.

## Run

1. Copy `.env.example` to `.env` and add the Telegram bot token and destination
   chat ID. Do not commit `.env`.
2. In PowerShell, set the values for the running shell, then start the service:

```powershell
$env:TELEGRAM_BOT_TOKEN = "your-token"
$env:TELEGRAM_CHAT_ID = "your-chat-id"
python .\skew_monitor.py
```

The first successful poll always sends the bot's initial status. A failed API
request does not alter the previous state or produce a false transition.
To monitor another market later, add a `Market` entry to `MARKETS` in
`skew_monitor.py`; polling and transition state are already per-market.

## Verify

```powershell
python -m unittest -v
```

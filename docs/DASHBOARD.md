# Dashboard and webhook

## Production stack

- **Entry:** [servers/start_async_webhook.py](../servers/start_async_webhook.py) → [servers/async_webhook_server.py](../servers/async_webhook_server.py).
- **Static UI:** pre-built assets under `static/dashboard/` (served by aiohttp).
- **Legacy / dev chart server:** [gui/chart_html.py](../gui/chart_html.py), [gui/master_control.html](../gui/master_control.html) — see [gui/README.md](../gui/README.md) if present.

## CORS

- `aiohttp_cors` is required (pinned in [requirements.txt](../requirements.txt)); see [CHANGELOG.md](CHANGELOG.md).

# Deployment

## Railway (dashboard + webhook)

Production companion is the aiohttp app started by [Procfile](../Procfile) → `python3 servers/start_async_webhook.py`. Config: [railway.json](../railway.json), [Dockerfile](../Dockerfile).

**Details:** [reference/deployment/DEPLOYMENT_GUIDE.md](reference/deployment/DEPLOYMENT_GUIDE.md), [reference/deployment/DEPLOY_QUICK_START.md](reference/deployment/DEPLOY_QUICK_START.md).

## Docker

Python-only image; dashboard SPA is pre-built under `static/dashboard/`. See root [README.md](../README.md) § Docker and [scripts/build.sh](../scripts/build.sh).

## Long-running strategies (VPS / Mac)

- [scripts/run_overnight.sh](../scripts/run_overnight.sh), [scripts/start_all.sh](../scripts/start_all.sh), [scripts/stop_all.sh](../scripts/stop_all.sh).
- Operational commands: [PLAYBOOK.md](PLAYBOOK.md).

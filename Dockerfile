# -----------------------------------------------------------------------------
# Dashboard SPA is pre-built and committed under static/dashboard/.
# Production serves it directly from there (see servers/async_webhook_server.py
# static-route setup). No Node/npm or frontend build stage in this image.
# -----------------------------------------------------------------------------
FROM python:3.12-slim

WORKDIR /app

# Faster asyncio on Linux when uvloop is installed (requirements.txt); honored in core/logging_setup.py
ENV USE_UVLOOP=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["python3", "servers/start_async_webhook.py"]

# Use Python 3.12 as base image
FROM python:3.12-slim

# Install Node.js for frontend build
RUN apt-get update && apt-get install -y \
    nodejs \
    npm \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy package.json for frontend dependencies
COPY frontend/package*.json ./frontend/
RUN cd frontend && npm ci --prefer-offline --no-audit

# Copy the rest of the application
COPY . .

# Build frontend (no secrets needed here)
RUN bash build.sh

# Expose port (Railway will override with $PORT)
EXPOSE 8080

# Start the application
# Secrets will be available as environment variables at runtime, not build time
CMD ["python3", "servers/start_async_webhook.py"]


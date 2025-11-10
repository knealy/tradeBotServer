# Use Python 3.12 as base image
FROM python:3.12-slim

# Install Node.js 20.x for frontend build
RUN apt-get update && apt-get install -y \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy frontend package files
COPY frontend/package.json frontend/package-lock.json ./frontend/

# Install frontend dependencies (use npm install since we have lock file)
WORKDIR /app/frontend
RUN npm install --prefer-offline --no-audit

# Go back to app root and copy everything
WORKDIR /app
COPY . .

# Build frontend (no secrets needed here)
RUN bash build.sh

# Expose port (Railway will override with $PORT)
EXPOSE 8080

# Start the application
# Secrets will be available as environment variables at runtime, not build time
CMD ["python3", "servers/start_async_webhook.py"]


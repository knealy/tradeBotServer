# Use Python 3.12 as base image
FROM python:3.12-slim

# Install system dependencies including Node.js 18+ for frontend build
RUN apt-get update && apt-get install -y \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Build frontend first
# Copy package files explicitly to ensure package-lock.json is included
COPY frontend/package.json ./frontend/
COPY frontend/package-lock.json ./frontend/
WORKDIR /app/frontend
RUN npm ci --prefer-offline --no-audit

# Copy frontend source and build
COPY frontend/ .
RUN npm run build

# Return to app root
WORKDIR /app

# Copy the rest of the application
COPY . .

# Expose port (Railway will override with $PORT)
EXPOSE 8080

# Start the application with frontend serving
CMD ["python3", "servers/start_async_webhook.py"]


# Use Python 3.12 as base image
FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire application (including pre-built frontend)
COPY . .

# Expose port (Railway will override with $PORT)
EXPOSE 8080

# Start the application with frontend serving
CMD ["python3", "servers/start_async_webhook.py"]


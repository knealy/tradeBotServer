#!/bin/bash
# Setup script for local PostgreSQL database

set -e

echo "🐘 Setting up local PostgreSQL database..."

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker and try again."
    exit 1
fi

# Start PostgreSQL container
echo "📦 Starting PostgreSQL container..."
docker-compose up -d postgres

# Wait for PostgreSQL to be ready
echo "⏳ Waiting for PostgreSQL to be ready..."
timeout=30
counter=0
until docker exec trading_bot_postgres pg_isready -U postgres > /dev/null 2>&1; do
    sleep 1
    counter=$((counter + 1))
    if [ $counter -ge $timeout ]; then
        echo "❌ PostgreSQL failed to start within $timeout seconds"
        exit 1
    fi
done

echo "✅ PostgreSQL is ready!"

# Set DATABASE_URL for local development
export DATABASE_URL="postgresql://postgres:postgres@localhost:5432/trading_bot"

echo ""
echo "📋 Database connection string:"
echo "   DATABASE_URL=postgresql://postgres:postgres@localhost:5432/trading_bot"
echo ""
echo "💡 Add this to your .env file:"
echo "   DATABASE_URL=postgresql://postgres:postgres@localhost:5432/trading_bot"
echo ""
echo "✅ Local database setup complete!"
echo ""
echo "To stop the database:"
echo "   docker-compose down"
echo ""
echo "To view logs:"
echo "   docker-compose logs -f postgres"

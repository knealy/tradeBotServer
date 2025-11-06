"""
Environment variable loader for TopStepX Trading Bot
This module loads environment variables from .env file if it exists
"""

import os
from pathlib import Path

def load_env_file():
    """Load environment variables from .env file if it exists"""
    env_file = Path('.env')
    
    if env_file.exists():
        print("📁 Loading environment variables from .env file...")
        with open(env_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    # Remove quotes if present
                    value = value.strip('\'"')
                    # Strip inline comments (everything after #)
                    if '#' in value:
                        value = value.split('#')[0].strip()
                    os.environ[key] = value
        print("✅ Environment variables loaded successfully")
    else:
        # Check if running on Railway (Railway sets RAILWAY_ENVIRONMENT)
        if os.getenv('RAILWAY_ENVIRONMENT'):
            print("ℹ️  Using Railway environment variables (no .env file needed)")
            # Debug: Print some key environment variables
            print(f"🔍 POSITION_SIZE: {os.getenv('POSITION_SIZE')}")
            print(f"🔍 IGNORE_NON_ENTRY_SIGNALS: {os.getenv('IGNORE_NON_ENTRY_SIGNALS')}")
            print(f"🔍 TP1_FRACTION: {os.getenv('TP1_FRACTION')}")
        else:
            print("⚠️  No .env file found, using system environment variables")

    # Set safe defaults for new SDK feature toggles
    # Only set default if not already loaded from .env
    if 'USE_PROJECTX_SDK' not in os.environ:
        os.environ['USE_PROJECTX_SDK'] = '0'
        print(f"ℹ️  USE_PROJECTX_SDK not found in .env, defaulting to '0'")
    else:
        print(f"✅ USE_PROJECTX_SDK={os.environ.get('USE_PROJECTX_SDK')} (from .env or environment)")
    
    # Set cache expiration defaults (in minutes)
    if 'CACHE_TTL_MARKET_HOURS' not in os.environ:
        os.environ['CACHE_TTL_MARKET_HOURS'] = '2'  # 2 minutes during market hours (high volatility)
    if 'CACHE_TTL_OFF_HOURS' not in os.environ:
        os.environ['CACHE_TTL_OFF_HOURS'] = '15'  # 15 minutes during off-hours (low volatility)
    if 'CACHE_TTL_DEFAULT' not in os.environ:
        os.environ['CACHE_TTL_DEFAULT'] = '5'  # 5 minutes default fallback
    
    # Set cache format and memory cache defaults
    if 'CACHE_FORMAT' not in os.environ:
        os.environ['CACHE_FORMAT'] = 'parquet'  # Use Parquet for faster caching (or 'pickle' for compatibility)
    if 'MEMORY_CACHE_MAX_SIZE' not in os.environ:
        os.environ['MEMORY_CACHE_MAX_SIZE'] = '50'  # Cache up to 50 symbol/timeframe combinations in memory
    
    # Set WebSocket connection pool defaults
    if 'WEBSOCKET_POOL_MAX_SIZE' not in os.environ:
        os.environ['WEBSOCKET_POOL_MAX_SIZE'] = '5'  # Max 5 concurrent WebSocket connections in pool
    
    # Set prefetch defaults
    if 'PREFETCH_ENABLED' not in os.environ:
        os.environ['PREFETCH_ENABLED'] = 'true'  # Enable prefetch for common symbols/timeframes
    if 'PREFETCH_SYMBOLS' not in os.environ:
        os.environ['PREFETCH_SYMBOLS'] = 'MNQ,ES,NQ,MES'  # Common symbols to prefetch
    if 'PREFETCH_TIMEFRAMES' not in os.environ:
        os.environ['PREFETCH_TIMEFRAMES'] = '1m,5m'  # Common timeframes to prefetch

# Load environment variables when this module is imported
load_env_file()

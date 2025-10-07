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
                    os.environ[key] = value
        print("✅ Environment variables loaded successfully")
    else:
        print("⚠️  No .env file found, using system environment variables")

# Load environment variables when this module is imported
load_env_file()

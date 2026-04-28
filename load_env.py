"""
Environment variable loader for TopStepX Trading Bot
This module loads environment variables from .env file if it exists
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def load_env_file():
    """Load environment variables from .env file if it exists"""
    env_file = Path('.env')
    
    if env_file.exists():
        logger.info("Loading environment variables from .env file")
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
        logger.info("Environment variables loaded from .env")
    else:
        # Check if running on Railway (Railway sets RAILWAY_ENVIRONMENT)
        if os.getenv('RAILWAY_ENVIRONMENT'):
            logger.info("Using Railway environment variables (no .env file)")
            logger.debug("POSITION_SIZE=%s IGNORE_NON_ENTRY_SIGNALS=%s TP1_FRACTION=%s",
                         os.getenv('POSITION_SIZE'), os.getenv('IGNORE_NON_ENTRY_SIGNALS'),
                         os.getenv('TP1_FRACTION'))
        else:
            logger.warning("No .env file found, using system environment variables")

    # Set safe defaults for new SDK feature toggles
    # Only set default if not already loaded from .env
    if 'USE_PROJECTX_SDK' not in os.environ:
        os.environ['USE_PROJECTX_SDK'] = '0'
        logger.debug("USE_PROJECTX_SDK not in .env, defaulting to 0")
    else:
        logger.debug("USE_PROJECTX_SDK=%s (from .env or environment)", os.environ.get('USE_PROJECTX_SDK'))
    
    # Set prefetch defaults
    if 'PREFETCH_ENABLED' not in os.environ:
        os.environ['PREFETCH_ENABLED'] = 'true'  # Enable prefetch for common symbols/timeframes
    if 'PREFETCH_SYMBOLS' not in os.environ:
        os.environ['PREFETCH_SYMBOLS'] = 'MNQ,ES,NQ,MES'  # Common symbols to prefetch
    if 'PREFETCH_TIMEFRAMES' not in os.environ:
        os.environ['PREFETCH_TIMEFRAMES'] = '1m,5m'  # Common timeframes to prefetch


# Load environment variables when this module is imported
load_env_file()

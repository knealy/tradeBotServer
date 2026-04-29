"""
Historical Data Loader for Backtesting

Loads and processes historical OHLCV data from multiple sources.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from pathlib import Path
import logging
import json

logger = logging.getLogger(__name__)


class HistoricalDataLoader:
    """
    Loads historical OHLCV data for backtesting.
    
    Supports:
    - CSV files (OHLCV format)
    - JSON files
    - TopStepX API (via broker adapter)
    - Multiple timeframes
    - Date range filtering
    - Resampling
    """
    
    def __init__(self, broker_adapter=None):
        """
        Initialize data loader.
        
        Args:
            broker_adapter: Optional TopStepX adapter for API data
        """
        self.broker_adapter = broker_adapter
        self._data_cache: Dict[str, Any] = {}
    
    async def load_from_api(
        self,
        symbol: str,
        timeframe: str = "1m",
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 10000
    ) -> pd.DataFrame:
        """
        Load historical data from TopStepX API.
        
        Args:
            symbol: Trading symbol (e.g., "MNQ")
            timeframe: Bar interval (30s, 1m, 5m, 15m, 1h, 1d)
            start_date: Start date
            end_date: End date
            limit: Maximum number of bars
            
        Returns:
            DataFrame with OHLCV data
        """
        if not self.broker_adapter:
            raise ValueError("Broker adapter required for API data loading")

        import pandas as pd

        logger.info(f"Loading {symbol} {timeframe} data from API ({start_date} to {end_date})")
        
        # Fetch bars from broker
        bars = await self.broker_adapter.get_historical_data(
            symbol=symbol,
            timeframe=timeframe,
            start_time=start_date,
            end_time=end_date,
            limit=limit
        )
        
        if not bars:
            raise ValueError(f"No data returned from API for {symbol}")
        
        # Convert to DataFrame
        data = []
        for bar in bars:
            data.append({
                'timestamp': bar.timestamp,
                'open': bar.open,
                'high': bar.high,
                'low': bar.low,
                'close': bar.close,
                'volume': bar.volume
            })
        
        df = pd.DataFrame(data)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
        df.sort_index(inplace=True)
        
        logger.info(f"✅ Loaded {len(df)} bars from API")
        return df
    
    def load_from_csv(
        self,
        filepath: str,
        symbol: str,
        timestamp_col: str = 'timestamp',
        date_format: Optional[str] = None
    ) -> pd.DataFrame:
        """
        Load historical data from CSV file.
        
        Expected CSV format (flexible case):
        timestamp,open,high,low,close,volume  OR
        Time,Open,High,Low,Close,Volume
        
        Args:
            filepath: Path to CSV file
            symbol: Trading symbol
            timestamp_col: Name of timestamp column (auto-detected)
            date_format: Optional date format string
            
        Returns:
            DataFrame with OHLCV data
        """
        import pandas as pd

        logger.info(f"Loading {symbol} data from CSV: {filepath}")
        
        # Read CSV
        df = pd.read_csv(filepath)
        
        # Auto-detect column names (case-insensitive)
        column_mapping = {}
        for col in df.columns:
            col_lower = col.lower()
            if col_lower in ['timestamp', 'time', 'date', 'datetime']:
                column_mapping[col] = 'timestamp'
            elif col_lower == 'open':
                column_mapping[col] = 'open'
            elif col_lower == 'high':
                column_mapping[col] = 'high'
            elif col_lower == 'low':
                column_mapping[col] = 'low'
            elif col_lower == 'close':
                column_mapping[col] = 'close'
            elif col_lower == 'volume':
                column_mapping[col] = 'volume'
        
        # Rename columns to standard lowercase
        df.rename(columns=column_mapping, inplace=True)
        
        # Find timestamp column
        if 'timestamp' not in df.columns:
            raise ValueError(f"No timestamp column found. Available columns: {list(df.columns)}")
        
        # Convert timestamp column
        if date_format:
            df['timestamp'] = pd.to_datetime(df['timestamp'], format=date_format)
        else:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        # Set index
        df.set_index('timestamp', inplace=True)
        df.sort_index(inplace=True)
        
        # Validate required columns
        required_cols = ['open', 'high', 'low', 'close', 'volume']
        missing = [col for col in required_cols if col not in df.columns]
        if missing:
            raise ValueError(f"CSV missing required columns: {missing}. Available: {list(df.columns)}")
        
        # Convert to numeric
        for col in required_cols:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Drop rows with NaN
        df.dropna(inplace=True)
        
        logger.info(f"✅ Loaded {len(df)} bars from CSV")
        return df
    
    def load_from_json(
        self,
        filepath: str,
        symbol: str
    ) -> pd.DataFrame:
        """
        Load historical data from JSON file.
        
        Expected JSON format:
        [
          {"timestamp": "2024-01-01T09:30:00", "open": 25300, "high": 25310, ...},
          ...
        ]
        
        Args:
            filepath: Path to JSON file
            symbol: Trading symbol
            
        Returns:
            DataFrame with OHLCV data
        """
        import pandas as pd

        logger.info(f"Loading {symbol} data from JSON: {filepath}")
        
        with open(filepath, 'r') as f:
            data = json.load(f)
        
        df = pd.DataFrame(data)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
        df.sort_index(inplace=True)
        
        logger.info(f"✅ Loaded {len(df)} bars from JSON")
        return df
    
    def resample(
        self,
        data: pd.DataFrame,
        target_timeframe: str
    ) -> pd.DataFrame:
        """
        Resample data to different timeframe.
        
        Args:
            data: Input DataFrame
            target_timeframe: Target timeframe (e.g., "5m", "15m", "1h")
            
        Returns:
            Resampled DataFrame
        """
        # Parse timeframe
        freq_map = {
            's': 'S',
            'm': 'T',
            'h': 'H',
            'd': 'D'
        }
        
        # Extract number and unit
        if target_timeframe[-1] in freq_map:
            num = int(target_timeframe[:-1]) if target_timeframe[:-1].isdigit() else 1
            unit = freq_map[target_timeframe[-1]]
            freq = f"{num}{unit}"
        else:
            raise ValueError(f"Invalid timeframe: {target_timeframe}")
        
        # Resample OHLCV data
        resampled = data.resample(freq).agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        })
        
        # Drop incomplete bars (last bar if incomplete)
        resampled.dropna(inplace=True)
        
        logger.info(f"Resampled from {len(data)} bars to {len(resampled)} bars ({target_timeframe})")
        return resampled
    
    def filter_date_range(
        self,
        data: pd.DataFrame,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> pd.DataFrame:
        """
        Filter data by date range.
        
        Args:
            data: Input DataFrame
            start_date: Start date (inclusive)
            end_date: End date (inclusive)
            
        Returns:
            Filtered DataFrame
        """
        filtered = data.copy()
        
        if start_date:
            filtered = filtered[filtered.index >= start_date]
        
        if end_date:
            filtered = filtered[filtered.index <= end_date]
        
        logger.info(f"Filtered to {len(filtered)} bars ({start_date} to {end_date})")
        return filtered
    
    def validate_data(self, data: pd.DataFrame) -> bool:
        """
        Validate OHLCV data integrity.
        
        Checks:
        - Required columns present
        - No NaN values
        - High >= Low
        - High >= Open, Close
        - Low <= Open, Close
        - Volume >= 0
        
        Args:
            data: DataFrame to validate
            
        Returns:
            True if valid
        """
        required_cols = ['open', 'high', 'low', 'close', 'volume']
        
        # Check columns
        missing = [col for col in required_cols if col not in data.columns]
        if missing:
            logger.error(f"Missing columns: {missing}")
            return False
        
        # Check for NaN
        if data[required_cols].isnull().any().any():
            logger.error("Data contains NaN values")
            return False
        
        # Validate OHLC relationships
        invalid_high = (data['high'] < data['low']).sum()
        invalid_high_open = (data['high'] < data['open']).sum()
        invalid_high_close = (data['high'] < data['close']).sum()
        invalid_low_open = (data['low'] > data['open']).sum()
        invalid_low_close = (data['low'] > data['close']).sum()
        invalid_volume = (data['volume'] < 0).sum()
        
        if invalid_high > 0:
            logger.error(f"{invalid_high} bars have high < low")
            return False
        if invalid_high_open > 0:
            logger.warning(f"{invalid_high_open} bars have high < open")
        if invalid_high_close > 0:
            logger.warning(f"{invalid_high_close} bars have high < close")
        if invalid_low_open > 0:
            logger.warning(f"{invalid_low_open} bars have low > open")
        if invalid_low_close > 0:
            logger.warning(f"{invalid_low_close} bars have low > close")
        if invalid_volume > 0:
            logger.error(f"{invalid_volume} bars have negative volume")
            return False
        
        logger.info("✅ Data validation passed")
        return True
    
    def add_indicators(
        self,
        data: pd.DataFrame,
        indicators: Optional[List[str]] = None
    ) -> pd.DataFrame:
        """
        Add common technical indicators to data.
        
        Args:
            data: Input DataFrame
            indicators: List of indicators to add (default: all common ones)
            
        Returns:
            DataFrame with indicators added
        """
        import numpy as np
        import pandas as pd

        df = data.copy()
        
        if indicators is None:
            indicators = ['sma_20', 'ema_89', 'ema_233', 'atr_14', 'rsi_14']
        
        # Simple Moving Averages
        if any('sma' in ind for ind in indicators):
            for period in [20, 50, 200]:
                if f'sma_{period}' in indicators:
                    df[f'sma_{period}'] = df['close'].rolling(window=period).mean()
        
        # Exponential Moving Averages
        if any('ema' in ind for ind in indicators):
            for period in [9, 21, 50, 89, 200, 233]:
                if f'ema_{period}' in indicators or f'ema_{period}' == indicators:
                    df[f'ema_{period}'] = df['close'].ewm(span=period, adjust=False).mean()
        
        # ATR (Average True Range)
        if any('atr' in ind for ind in indicators):
            for period in [7, 14, 21]:
                if f'atr_{period}' in indicators:
                    high_low = df['high'] - df['low']
                    high_close = np.abs(df['high'] - df['close'].shift())
                    low_close = np.abs(df['low'] - df['close'].shift())
                    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
                    df[f'atr_{period}'] = true_range.rolling(window=period).mean()
        
        # RSI (Relative Strength Index)
        if any('rsi' in ind for ind in indicators):
            for period in [14]:
                if f'rsi_{period}' in indicators:
                    delta = df['close'].diff()
                    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
                    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
                    rs = gain / loss
                    df[f'rsi_{period}'] = 100 - (100 / (1 + rs))
        
        logger.info(f"✅ Added {len([i for i in indicators if i in df.columns])} indicators")
        return df
    
    def get_sample_data(
        self,
        symbol: str = "MNQ",
        days: int = 30,
        timeframe: str = "1m"
    ) -> pd.DataFrame:
        """
        Generate sample data for testing (random walk with realistic properties).
        
        Args:
            symbol: Symbol name
            days: Number of days of data
            timeframe: Bar interval
            
        Returns:
            DataFrame with synthetic OHLCV data
        """
        import numpy as np
        import pandas as pd

        logger.info(f"Generating {days} days of sample data for {symbol} ({timeframe})")
        
        # Parse timeframe to minutes
        if timeframe.endswith('m'):
            minutes = int(timeframe[:-1])
        elif timeframe.endswith('s'):
            minutes = int(timeframe[:-1]) / 60
        elif timeframe.endswith('h'):
            minutes = int(timeframe[:-1]) * 60
        else:
            minutes = 1
        
        # Calculate number of bars
        bars_per_day = (24 * 60) / minutes
        num_bars = int(days * bars_per_day)
        
        # Generate timestamps
        end = datetime.now()
        start = end - timedelta(days=days)
        # pandas >=2.2 removed alias "T" for minutes; Timedelta is unambiguous
        bar_td = pd.Timedelta(minutes=float(minutes)) if minutes >= 1.0 / 60 else pd.Timedelta(seconds=60)
        timestamps = pd.date_range(start=start, periods=num_bars, freq=bar_td)
        
        # Generate price data (random walk with trend and volatility)
        base_price = 25000.0  # Starting price for MNQ
        volatility = 0.002  # 0.2% per bar
        trend = 0.0001  # Slight upward trend
        
        returns = np.random.normal(trend, volatility, num_bars)
        prices = base_price * (1 + returns).cumprod()
        
        # Generate OHLC from close prices
        data = []
        for i, (ts, close) in enumerate(zip(timestamps, prices)):
            bar_volatility = close * 0.001  # 0.1% bar range
            high = close + np.random.uniform(0, bar_volatility)
            low = close - np.random.uniform(0, bar_volatility)
            open_price = np.random.uniform(low, high)
            volume = int(np.random.uniform(100, 1000))
            
            data.append({
                'timestamp': ts,
                'open': open_price,
                'high': high,
                'low': low,
                'close': close,
                'volume': volume
            })
        
        df = pd.DataFrame(data)
        df.set_index('timestamp', inplace=True)
        
        logger.info(f"✅ Generated {len(df)} sample bars")
        return df

"""
Export Historical Data for Backtesting

Fetches historical data from TopStepX API and exports to CSV format
suitable for backtesting.

Usage:
    python scripts/export_history.py --symbol=MNQ --timeframe=1m --days=90
    python scripts/export_history.py --symbol=MES --timeframe=5m --start=2024-01-01 --end=2024-12-31
"""

import sys
import os
import asyncio
import argparse
from pathlib import Path
from datetime import datetime, timedelta

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from trading_bot import TopStepXTradingBot


async def export_history(
    symbol: str,
    timeframe: str = "1m",
    days: int = None,
    start_date: str = None,
    end_date: str = None,
    output_file: str = None
):
    """
    Export historical data to CSV.
    
    Args:
        symbol: Trading symbol (MNQ, MES, etc.)
        timeframe: Bar interval (30s, 1m, 5m, etc.)
        days: Number of days (alternative to start/end)
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        output_file: Output CSV filename
    """
    print("="*80)
    print("HISTORICAL DATA EXPORT FOR BACKTESTING")
    print("="*80)
    print(f"Symbol: {symbol}")
    print(f"Timeframe: {timeframe}")
    
    # Initialize bot
    api_key = os.getenv('PROJECT_X_API_KEY') or os.getenv('TOPSETPX_API_KEY')
    username = os.getenv('PROJECT_X_USERNAME') or os.getenv('TOPSETPX_USERNAME')
    
    if not api_key or not username:
        print("❌ Error: API credentials not found in environment variables")
        print("   Set PROJECT_X_API_KEY and PROJECT_X_USERNAME")
        return
    
    bot = TopStepXTradingBot(api_key=api_key, username=username)
    
    try:
        # Authenticate
        print("\n🔐 Authenticating...")
        await bot.authenticate()
        print("✅ Authenticated")
        
        # Calculate date range
        if days:
            end_dt = datetime.now()
            start_dt = end_dt - timedelta(days=days)
            print(f"Date Range: {start_dt.date()} to {end_dt.date()} ({days} days)")
        elif start_date and end_date:
            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            end_dt = datetime.strptime(end_date, '%Y-%m-%d')
            print(f"Date Range: {start_date} to {end_date}")
        else:
            # Default: last 30 days
            end_dt = datetime.now()
            start_dt = end_dt - timedelta(days=30)
            print(f"Date Range: {start_dt.date()} to {end_dt.date()} (default 30 days)")
        
        # Fetch historical data
        print(f"\n📊 Fetching {symbol} {timeframe} bars...")
        bars = await bot.broker_adapter.get_historical_data(
            symbol=symbol,
            timeframe=timeframe,
            start_time=start_dt,
            end_time=end_dt,
            limit=10000  # Max bars
        )
        
        if not bars:
            print("❌ No data returned")
            return
        
        print(f"✅ Fetched {len(bars)} bars")
        
        # Generate output filename
        if not output_file:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = f"historical_data/{symbol}_{timeframe}_{timestamp}.csv"
        
        # Create directory
        os.makedirs(os.path.dirname(output_file) if os.path.dirname(output_file) else ".", exist_ok=True)
        
        # Export to CSV
        print(f"\n💾 Exporting to CSV: {output_file}")
        
        with open(output_file, 'w') as f:
            # Write header
            f.write("timestamp,open,high,low,close,volume\n")
            
            # Write data
            for bar in bars:
                timestamp = bar.timestamp.strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"{timestamp},{bar.open},{bar.high},{bar.low},{bar.close},{bar.volume}\n")
        
        print(f"✅ Exported {len(bars)} bars to {output_file}")
        
        # Show backtest command
        print("\n" + "="*80)
        print("BACKTEST THIS DATA:")
        print("="*80)
        print(f"\npython core/backtest_executor.py \\")
        print(f"  --strategy=ma_crossover \\")
        print(f"  --symbol={symbol} \\")
        print(f"  --csv={output_file}")
        
        print(f"\n# With Monte Carlo:")
        print(f"python core/backtest_executor.py \\")
        print(f"  --strategy=ma_crossover \\")
        print(f"  --symbol={symbol} \\")
        print(f"  --csv={output_file} \\")
        print(f"  --monte-carlo=1000")
        
        print("\n✅ Export complete!")
    
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Export historical data for backtesting')
    
    # Required arguments
    parser.add_argument('--symbol', type=str, required=True,
                       help='Trading symbol (MNQ, MES, etc.)')
    
    # Data range arguments
    parser.add_argument('--timeframe', type=str, default='1m',
                       help='Bar interval (30s, 1m, 5m, 15m, 1h, 1d)')
    parser.add_argument('--days', type=int,
                       help='Number of days to export')
    parser.add_argument('--start', type=str,
                       help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', type=str,
                       help='End date (YYYY-MM-DD)')
    
    # Output argument
    parser.add_argument('--output', type=str,
                       help='Output CSV filename (default: historical_data/<symbol>_<timeframe>_<timestamp>.csv)')
    
    args = parser.parse_args()
    
    await export_history(
        symbol=args.symbol,
        timeframe=args.timeframe,
        days=args.days,
        start_date=args.start,
        end_date=args.end,
        output_file=args.output
    )


if __name__ == '__main__':
    asyncio.run(main())

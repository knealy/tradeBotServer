# Multi-Account Trading Guide

## Overview

This guide explains how to run the overnight range strategy (or any strategy) across multiple accounts simultaneously.

## Recommended Approach: Multiple Bot Instances

**✅ RECOMMENDED: Run separate bot instances, one per account**

### Why Multiple Instances?

1. **Better Isolation**: Failures in one account don't affect others
2. **Independent Rate Limits**: Each instance has its own rate limiter (60 calls/60s)
3. **Simpler Architecture**: No code changes needed - just run multiple processes
4. **Easier Monitoring**: Separate logs and metrics per account
5. **Independent Configuration**: Different settings per account via environment variables

### Architecture Comparison

#### Multiple Instances (Recommended)
```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Bot Instance 1 │     │  Bot Instance 2 │     │  Bot Instance 3 │
│  Account: A     │     │  Account: B     │     │  Account: C     │
│                 │     │                 │     │                 │
│  Rate Limit:    │     │  Rate Limit:    │     │  Rate Limit:    │
│  60 calls/60s   │     │  60 calls/60s   │     │  60 calls/60s   │
│                 │     │                 │     │                 │
│  Strategy:      │     │  Strategy:      │     │  Strategy:      │
│  OvernightRange │     │  OvernightRange │     │  OvernightRange │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

**Total API Capacity**: 180 calls/60s (3 × 60)

#### Copy Trading (Not Recommended)
```
┌─────────────────────────────────────────────────┐
│         Single Bot Instance                     │
│                                                 │
│  Rate Limit: 60 calls/60s (shared)             │
│                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │ Account A│  │ Account B│  │ Account C│    │
│  └──────────┘  └──────────┘  └──────────┘    │
│       ↓             ↓             ↓            │
│  Strategy must route orders to correct account │
│  (Requires significant code refactoring)        │
└─────────────────────────────────────────────────┘
```

**Total API Capacity**: 60 calls/60s (shared)

## Quick Start

### Option 1: Using the Multi-Account Script

```bash
# Start bots for multiple accounts
./scripts/start_multi_account.sh <account_id1> <account_id2> <account_id3>

# Or set environment variable
export MULTI_ACCOUNT_IDS="account_id1,account_id2,account_id3"
./scripts/start_multi_account.sh

# Stop all instances
./scripts/stop_multi_account.sh
```

### Option 2: Manual Setup (Terminal/Tmux)

```bash
# Terminal 1 - Account 1
export TOPSTEPX_ACCOUNT_ID="account_id_1"
python trading_bot.py

# Terminal 2 - Account 2
export TOPSTEPX_ACCOUNT_ID="account_id_2"
python trading_bot.py

# Terminal 3 - Account 3
export TOPSTEPX_ACCOUNT_ID="account_id_3"
python trading_bot.py
```

### Option 3: Using Tmux (Recommended for Production)

The `start_multi_account.sh` script automatically uses tmux if available:

```bash
# Start all accounts in tmux session
./scripts/start_multi_account.sh account1 account2 account3

# Attach to session
tmux attach -t multi_account_trading

# List windows
tmux list-windows -t multi_account_trading

# Switch between accounts
# Ctrl+B then window number (0, 1, 2, etc.)
```

## Configuration Per Account

Each instance can have different configuration via environment variables:

### Account-Specific Environment Variables

```bash
# Account 1 configuration
export TOPSTEPX_ACCOUNT_ID="account_1"
export OVERNIGHT_RANGE_SYMBOLS="MNQ"
export MARKET_OPEN_TIME="08:00"
export STRATEGY_QUANTITY="1"

# Account 2 configuration (different settings)
export TOPSTEPX_ACCOUNT_ID="account_2"
export OVERNIGHT_RANGE_SYMBOLS="MES"
export MARKET_OPEN_TIME="09:30"
export STRATEGY_QUANTITY="2"
```

### Using .env Files

Create separate `.env` files for each account:

```bash
# .env.account1
TOPSTEPX_ACCOUNT_ID=account_1
OVERNIGHT_RANGE_SYMBOLS=MNQ
MARKET_OPEN_TIME=08:00

# .env.account2
TOPSTEPX_ACCOUNT_ID=account_2
OVERNIGHT_RANGE_SYMBOLS=MES
MARKET_OPEN_TIME=09:30
```

Then start each instance with its own env file:

```bash
# Terminal 1
export $(cat .env.account1 | xargs)
python trading_bot.py

# Terminal 2
export $(cat .env.account2 | xargs)
python trading_bot.py
```

## Resource Considerations

### Memory Usage
- Each bot instance uses ~50-200MB RAM (depending on strategies)
- 3 accounts = ~150-600MB total

### API Rate Limits
- **Per Instance**: 60 calls per 60 seconds
- **Multiple Instances**: Each gets its own 60/60s limit
- **Total Capacity**: N instances × 60 calls/60s

### Database
- All instances share the same database (if using SQLite/PostgreSQL)
- Each instance tracks its own account state
- No conflicts - account_id is used as primary key

### WebSocket Connections
- Each instance maintains its own WebSocket connection
- TopStepX API should handle multiple connections from same user

## Monitoring Multiple Instances

### Log Files

Each instance writes to its own log file:

```bash
# View all logs
tail -f logs/trading_bot_*.log

# View specific account
tail -f logs/trading_bot_account_123.log

# Search across all logs
grep "ERROR" logs/trading_bot_*.log
```

### Process Management

```bash
# Check running instances
ps aux | grep trading_bot.py

# Check tmux sessions
tmux list-sessions

# Monitor resource usage
top -p $(pgrep -f trading_bot.py | tr '\n' ',' | sed 's/,$//')
```

## Alternative: Copy Trading (Advanced)

If you need copy trading within a single instance, you would need to:

1. **Refactor Strategy Base Class**:
   - Add `account_id` parameter to all order placement methods
   - Modify `place_range_break_orders()` to accept account list

2. **Modify Order Execution**:
   - Update `OrderExecutor` to handle multiple accounts
   - Route orders to correct account based on strategy configuration

3. **Update Strategy Logic**:
   ```python
   # Pseudo-code for copy trading
   async def place_range_break_orders(self, symbol: str, account_ids: List[str]):
       for account_id in account_ids:
           await self.trading_bot.switch_account(account_id)
           # Place order for this account
           await self.trading_bot.place_stop_order(...)
   ```

4. **Handle Rate Limits**:
   - Share single rate limiter across all accounts
   - May hit limits faster with multiple accounts

**⚠️ This approach is NOT recommended** due to complexity and shared rate limits.

## Troubleshooting

### Issue: Rate Limit Errors

**Symptom**: `429 Too Many Requests` errors

**Solution**: 
- Each instance has its own rate limiter
- If still hitting limits, reduce number of instances or increase API rate limit (if allowed by TopStepX)

### Issue: Database Locked

**Symptom**: `database is locked` errors

**Solution**:
- SQLite doesn't handle concurrent writes well
- Consider using PostgreSQL for production multi-account setups
- Or use separate database files per account

### Issue: WebSocket Disconnections

**Symptom**: Frequent reconnections

**Solution**:
- Each instance maintains its own WebSocket
- Check network stability
- Verify TopStepX allows multiple connections

## Best Practices

1. **Use Tmux/Screen**: Keeps sessions alive if SSH disconnects
2. **Separate Logs**: Each account gets its own log file
3. **Monitor Resources**: Watch CPU/memory usage with multiple instances
4. **Startup Order**: Start instances with small delays (1-2 seconds) to avoid API bursts
5. **Graceful Shutdown**: Use `stop_multi_account.sh` script to cleanly stop all instances

## Example: Running 3 Accounts

```bash
# 1. Set account IDs
export MULTI_ACCOUNT_IDS="12345678,87654321,11223344"

# 2. Start all instances
./scripts/start_multi_account.sh

# 3. Monitor in tmux
tmux attach -t multi_account_trading

# 4. Check logs
tail -f logs/trading_bot_*.log

# 5. Stop all
./scripts/stop_multi_account.sh
```

## Summary

**✅ Use Multiple Instances** for:
- Better isolation and fault tolerance
- Independent rate limits
- Simpler implementation (no code changes)
- Easier monitoring and debugging

**❌ Avoid Copy Trading** unless:
- You have specific requirements for shared state
- You're willing to refactor significant code
- You understand the rate limit implications

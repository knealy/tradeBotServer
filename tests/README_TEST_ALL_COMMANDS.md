# Trading Bot Command Test Suite

## Overview

`test_all_commands.py` is a comprehensive test suite that exercises all available commands in the trading bot, measures execution times, and generates detailed performance reports.

## Features

- ✅ Tests all major commands (contracts, accounts, quotes, depth, positions, orders, etc.)
- ⏱️ Measures execution time for each command
- 📊 Generates detailed performance reports
- 💾 Can export results to JSON
- 🎯 Identifies slowest and fastest commands
- ❌ Reports failures with error messages

## Usage

### Basic Usage

```bash
# Run all tests with default settings
python tests/test_all_commands.py

# Use a specific account ID
python tests/test_all_commands.py --account-id 12345678

# Test with a different symbol
python tests/test_all_commands.py --symbol MES

# Save results to JSON file
python tests/test_all_commands.py --output test_results.json

# Enable verbose logging
python tests/test_all_commands.py --verbose
```

### Command Line Options

- `--account-id ACCOUNT_ID`: Specify account ID to use for tests (if not provided, uses first available account)
- `--symbol SYMBOL`: Symbol to use for quote/depth/history tests (default: MNQ)
- `--output FILE`: Save test results to JSON file
- `--verbose`: Enable verbose/debug logging

## Test Coverage

The test suite covers the following commands:

1. **contracts** - Fetch available contracts
2. **accounts** - List accounts
3. **account_info** - Get account information
4. **account_balance** - Get account balance
5. **quote** - Get market quote for symbol
6. **depth** - Get market depth for symbol
7. **positions** - Get open positions
8. **orders** - Get open orders
9. **history** - Get historical data
10. **compliance** - Check compliance status
11. **risk** - Get risk metrics
12. **trades** - Get trade history

## Output Format

### Console Output

```
======================================================================
TRADING BOT COMMAND TEST SUITE
======================================================================
Test Symbol: MNQ
Account ID: 12694476
======================================================================

Testing: contracts            ... ✅  245.32 ms
Testing: accounts             ... ✅  189.45 ms
Testing: quote                ... ✅   45.23 ms
...

======================================================================
TEST REPORT
======================================================================

📊 Summary:
   Total Tests:     12
   Successful:      11 ✅
   Failed:           1 ❌
   Success Rate:    91.7%

⏱️  Performance:
   Total Time:      1234.56 ms
   Average Time:    112.23 ms
   Fastest:         23.45 ms
   Slowest:         456.78 ms

⚡ Fastest Command:
   quote                  - 23.45 ms

🐌 Slowest Command:
   history                - 456.78 ms
```

### JSON Output

When using `--output`, the script generates a JSON file with:

```json
{
  "summary": {
    "total_tests": 12,
    "successful": 11,
    "failed": 1,
    "success_rate": 91.67,
    "total_elapsed_ms": 1234.56,
    "avg_time_ms": 112.23,
    "min_time_ms": 23.45,
    "max_time_ms": 456.78
  },
  "results": [
    {
      "command": "quote",
      "success": true,
      "elapsed_ms": 23.45,
      "timestamp": "2025-12-04T12:00:00+00:00"
    },
    ...
  ],
  "errors": [
    {
      "command": "depth",
      "success": false,
      "elapsed_ms": 123.45,
      "error": "SignalR connection failed",
      "timestamp": "2025-12-04T12:00:00+00:00"
    }
  ],
  "slowest": {...},
  "fastest": {...}
}
```

## Exit Codes

- `0`: Success rate >= 80%
- `1`: Success rate >= 50% but < 80%
- `2`: Success rate < 50%

## Requirements

- Python 3.8+
- Valid TopStepX API credentials (in `.env` or environment variables)
- `trading_bot.py` in parent directory

## Notes

- Some tests require an account to be selected (positions, orders, etc.)
- Tests that require account access will be skipped if no account is available
- The script uses cached data when possible to speed up tests
- SignalR connection errors are expected in some environments and are handled gracefully


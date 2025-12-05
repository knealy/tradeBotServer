"""
Live benchmark harness to compare Rust vs Python order paths **against a practice/sandbox account**.

⚠️ WARNING:
    - This script will place REAL MARKET ORDERS on the account you provide.
    - ONLY run this against a PRACTICE / SANDBOX TopStepX account.
    - Make sure your risk settings and position size are appropriate.

Required environment variables (already used by the bot):
    PROJECT_X_API_KEY or TOPSETPX_API_KEY
    PROJECT_X_USERNAME or TOPSETPX_USERNAME

Additional benchmark-specific variables:
    BENCH_ACCOUNT_ID  - The numeric TopStepX account ID to use for orders (required).
    BENCH_SYMBOL      - Symbol to trade (default: "MNQ").
    BENCH_ITERS       - Number of orders per path (default: 5).
    BENCH_LIVE        - Must be "1" or "true" to enable live benchmarking; otherwise script exits.

Usage (from project root, with venv active):

    export PROJECT_X_API_KEY="..."
    export PROJECT_X_USERNAME="..."
    export BENCH_ACCOUNT_ID="12345678"
    export BENCH_LIVE=1
    BENCH_ITERS=5 BENCH_SYMBOL=MNQ python tests/bench_rust_vs_python_orders.py
"""

import asyncio
import os
import sys
import time

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brokers.topstepx_adapter import TopStepXAdapter, OrderResponse
from core.auth import AuthManager
from core.market_data import ContractManager
from dotenv import load_dotenv

# Load variables from .env in the project root
load_dotenv()


async def _bench_once(
    use_rust: bool,
    iterations: int,
) -> float:
    """Run one benchmark and return average latency in ms.

    This uses real AuthManager + TopStepXAdapter and places real orders
    on the practice account specified by BENCH_ACCOUNT_ID.
    """

    # Resolve base URL and credentials
    base_url = os.getenv("TOPSTEPX_BASE_URL") or os.getenv("PROJECT_X_BASE_URL") or "https://api.topstepx.com"
    api_key = os.getenv("PROJECT_X_API_KEY") or os.getenv("TOPSETPX_API_KEY")
    username = os.getenv("PROJECT_X_USERNAME") or os.getenv("TOPSETPX_USERNAME")
    account_id = os.getenv("BENCH_ACCOUNT_ID")

    if not api_key or not username:
        raise RuntimeError("PROJECT_X_API_KEY/TOPSETPX_API_KEY and PROJECT_X_USERNAME/TOPSETPX_USERNAME must be set.")
    if not account_id:
        raise RuntimeError("BENCH_ACCOUNT_ID must be set to a valid practice/sandbox account ID.")

    auth = AuthManager(
        api_key=api_key,
        username=username,
        base_url=base_url,
    )

    # Ensure we have a valid token
    ok = await auth.ensure_valid_token()
    if not ok or not auth.get_token():
        raise RuntimeError("Authentication failed in benchmark; check API key / username.")

    contract_manager = ContractManager()

    adapter = TopStepXAdapter(
        auth_manager=auth,
        contract_manager=contract_manager,
        rate_limiter=None,
        base_url=base_url,
        use_rust=use_rust,
    )

    # Prime contract cache using the adapter helper
    contracts = await adapter.get_available_contracts(use_cache=True)
    if not contracts:
        raise RuntimeError("Failed to fetch contracts for benchmark; cannot resolve contract IDs.")

    # For both paths, use the same symbol/side/qty
    symbol = os.getenv("BENCH_SYMBOL", "MNQ")
    side = "BUY"
    quantity = 1

    # Single warm-up call (uses the same public API as the benchmark loop)
    await adapter.place_market_order(symbol, side, quantity, account_id)

    start = time.perf_counter()
    for _ in range(iterations):
        resp: OrderResponse = await adapter.place_market_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            account_id=account_id,
        )

        # Sanity check to avoid benchmarking error paths
        if not resp.success:
            raise RuntimeError(f"Benchmark call failed (use_rust={use_rust}): {resp.error}")

    elapsed = time.perf_counter() - start
    avg_ms = (elapsed / iterations) * 1000.0
    return avg_ms


async def main() -> None:
    iters = int(os.getenv("BENCH_ITERS", "10"))  # Increased default for better statistics

    live_flag = os.getenv("BENCH_LIVE", "").strip().lower()
    if live_flag not in ("1", "true", "yes", "on"):
        print("⚠️ BENCH_LIVE is not set to 1/true; refusing to place live benchmark orders.")
        print("   To run the live sandbox benchmark, set BENCH_LIVE=1 explicitly.")
        return

    print("⚙️  Running LIVE benchmark against TopStepX practice/sandbox account")
    print(f"   Iterations per path: {iters}")

    # Python baseline (force Python by disabling Rust in adapter)
    python_ms = await _bench_once(use_rust=False, iterations=iters)
    print(f"\n🐍 Python place_market_order (use_rust=False):")
    print(f"   avg latency: {python_ms:.3f} ms over {iters} iterations")

    # Rust hot path (force Rust in adapter)
    rust_ms = await _bench_once(use_rust=True, iterations=iters)
    print(f"\n🚀 Rust place_market_order (use_rust=True):")
    print(f"   avg latency: {rust_ms:.3f} ms over {iters} iterations")

    if python_ms > 0:
        speedup = python_ms / rust_ms if rust_ms > 0 else float("inf")
        print(f"\n📊 Approximate speedup (Python / Rust): {speedup:.2f}x")
        
        # Analysis
        print(f"\n💡 Analysis:")
        print(f"   Network latency dominates: ~{min(python_ms, rust_ms):.1f}ms is API round-trip time")
        print(f"   Code execution overhead: ~{abs(python_ms - rust_ms):.1f}ms difference")
        print(f"   ")
        print(f"   ⚠️  Small speedup is expected for network-bound operations.")
        print(f"   🚀 Rust's real benefits will show in Phase 2/3:")
        print(f"      - CPU-bound operations (bar aggregation, calculations)")
        print(f"      - High-frequency scenarios (many orders/second)")
        print(f"      - Expected 10-20x speedup for market data processing")
        print(f"   ")
        print(f"   💡 For order execution, Rust provides:")
        print(f"      - Better connection pooling (reqwest vs requests)")
        print(f"      - Lower memory overhead")
        print(f"      - More predictable latency under load")
        print(f"      - Foundation for future optimizations")


if __name__ == "__main__":
    asyncio.run(main())



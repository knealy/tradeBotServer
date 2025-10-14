#!/usr/bin/env python3
"""
Unified startup script to run the TradingView webhook server with TopStepX trading bot.

Features:
- Loads environment (.env via load_env.py)
- Authenticates TopStepX API
- Selects account (by --account-id or first available)
- Starts HTTP webhook server
- Prints TradingView setup instructions with example JSON payload
"""

import os
import sys
import asyncio
import signal
import logging
import argparse

from trading_bot import TopStepXTradingBot
from webhook_server import WebhookServer


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def _print_tradingview_instructions(host: str, port: int, account_name: str, public_url: str | None = None) -> None:
    """Print clear TradingView alert setup instructions and example payload.

    If a public_url is provided (e.g., from ngrok), prefer that for TradingView.
    """
    local_url = f"http://{host}:{port}/"
    url = public_url or local_url

    print("\n" + "=" * 72)
    print("TRADINGVIEW ALERT SETUP")
    print("=" * 72)
    print("1) Create/Edit your Alert in TradingView")
    print("2) Set 'Webhook URL' to:")
    print(f"   {url}")
    if not public_url:
        # Check if running on Railway
        if os.getenv('RAILWAY_ENVIRONMENT'):
            railway_url = f"https://{os.getenv('RAILWAY_PUBLIC_DOMAIN', 'tvwebhooks.up.railway.app')}/"
            print(f"   Railway HTTPS URL: {railway_url}")
        else:
            print("   Note: Railway provides HTTPS automatically - no tunneling needed!")
    print("3) Use the following JSON in the alert Message field:")
    print()
    # Example payload matching webhook_server._extract_trade_info expectations (Discord-style embed)
    example_payload = {
        "embeds": [
            {
                "title": "Open Long [MNQ1!]",  # Examples: "Open Long [MNQ1!]", "TP2 hit for short [MNQ1!]"
                "description": "Session PnL: $ +80.9 points",
                "fields": [
                    {"name": "Entry", "value": "19500.00"},
                    {"name": "Stop", "value": "19400.00"},
                    {"name": "Target 1", "value": "19580.00"},
                    {"name": "Target 2", "value": "19640.00"}
                ]
            }
        ]
    }
    import json as _json
    print("```json")
    print(_json.dumps(example_payload, indent=2))
    print("```")
    print()
    print("Signal title rules supported:")
    print("- Open Long / Open Short")
    print("- Stop Out Long / Stop Out Short")
    print("- Trim/Close Long / Trim/Close Short (treated as TP1)")
    print("- TP1 hit for long/short, TP2 hit for long/short, TP3 hit ...")
    print("- Close Long / Close Short / Exit Long / Exit Short / Session Close")
    print()
    if public_url:
        print(f"Webhook is listening on {url} and will trade on account: {account_name}")
    else:
        print(f"Webhook is listening on Railway (HTTPS) and will trade on account: {account_name}")
    print("=" * 72)


async def _bootstrap(args: argparse.Namespace) -> None:
    # Load env - handle Railway deployment gracefully
    try:
        import load_env  # noqa: F401  (side-effect: loads .env)
        logger.info("✅ Environment variables loaded from .env file")
    except Exception as e:
        logger.info("ℹ️  Using system environment variables (Railway deployment)")
        # This is normal for Railway - no .env file needed

    api_key = os.getenv("PROJECT_X_API_KEY")
    username = os.getenv("PROJECT_X_USERNAME")

    if not api_key or not username:
        logger.error("Missing API credentials. Set PROJECT_X_API_KEY and PROJECT_X_USERNAME")
        sys.exit(1)

    # Initialize bot
    bot = TopStepXTradingBot(api_key=api_key, username=username)

    # Authenticate
    if not await bot.authenticate():
        logger.error("Authentication failed with TopStepX API")
        sys.exit(1)

    # Get accounts
    accounts = await bot.list_accounts()
    if not accounts:
        logger.error("No accounts available")
        sys.exit(1)

    # Select account by --account-id, environment variable, or error out
    # Railway stringifies all env vars, so normalize properly
    account_id_to_find = args.account_id
    if not account_id_to_find:
        # Check environment variables (Railway deployment)
        account_id_to_find = os.getenv('TOPSTEPX_ACCOUNT_ID') or os.getenv('PROJECT_X_ACCOUNT_ID')
        if account_id_to_find:
            # Railway stringifies env vars, normalize it
            account_id_to_find = str(account_id_to_find).strip().strip('\'"').strip()
            logger.info(f"Using account ID from environment: {account_id_to_find}")
    
    selected = None
    if account_id_to_find:
        for acct in accounts:
            # Compare as integers for Railway compatibility
            try:
                if int(acct.get("id")) == int(account_id_to_find):
                    selected = acct
                    break
            except (ValueError, TypeError):
                # Fallback to string comparison
                if str(acct.get("id")) == str(account_id_to_find):
                    selected = acct
                    break
        if not selected:
            logger.error(f"Account ID {account_id_to_find} not found in available accounts")
            logger.error("Available accounts:")
            for acct in accounts:
                logger.error(f"  - {acct.get('name')} (ID: {acct.get('id')})")
            sys.exit(1)
    else:
        logger.error("No account ID specified. Set TOPSTEPX_ACCOUNT_ID environment variable or use --account-id")
        logger.error("Available accounts:")
        for acct in accounts:
            logger.error(f"  - {acct.get('name')} (ID: {acct.get('id')})")
        sys.exit(1)

    bot.selected_account = selected

    logger.info(f"Using account: {selected['name']} (ID: {selected['id']})")
    logger.info(f"Position size: {args.position_size} contracts")
    logger.info(f"Close entire position at TP1: {args.close_entire_at_tp1}")

    # Start webhook server
    server = WebhookServer(
        trading_bot=bot,
        host=args.host,
        port=args.port,
        account_id=str(selected['id']),
        position_size=args.position_size,
        close_entire_position_at_tp1=args.close_entire_at_tp1,
    )
    server.start()

    # Optional public tunnel (e.g., ngrok)
    active_tunnel = None
    public_url = None
    if args.tunnel == "ngrok":
        try:
            from pyngrok import ngrok, conf
            token = os.getenv("NGROK_AUTHTOKEN")
            if token:
                conf.get_default().auth_token = token
            # Start HTTPS tunnel to local HTTP server
            # bind_tls=True ensures an https URL suitable for TradingView
            logger.info("Starting ngrok tunnel...")
            active_tunnel = ngrok.connect(args.port, proto="http", bind_tls=True)
            public_url = active_tunnel.public_url
            logger.info(f"ngrok tunnel online: {public_url} -> http://{args.host}:{args.port}")
        except Exception as e:
            logger.error(f"Failed to start ngrok tunnel: {e}")
            public_url = None

    # Print TradingView instructions (prefer public_url when available)
    _print_tradingview_instructions(args.host, args.port, selected['name'], public_url)

    # Graceful shutdown on SIGINT/SIGTERM
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _handle_signal(signame):
        logger.info(f"Received {signame}, shutting down...")
        stop_event.set()

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, _handle_signal, s.name)
        except NotImplementedError:
            # Signal handlers may not be available on some platforms (e.g., Windows)
            pass

    try:
        logger.info("Webhook server is running. Press Ctrl+C to stop.")
        await stop_event.wait()
    finally:
        server.stop()
        # Stop tunnel if started
        try:
            if active_tunnel is not None:
                from pyngrok import ngrok
                ngrok.disconnect(active_tunnel.public_url)
                ngrok.kill()
        except Exception:
            pass


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start TradingView webhook server with TopStepX bot")
    parser.add_argument("--account-id", type=str, help="Account ID to trade on (optional)")
    parser.add_argument("--position-size", type=int, default=1, help="Contracts per position (default: 1)")
    parser.add_argument("--close-entire-at-tp1", action='store_true', help="Flatten position at TP1 instead of partial")
    parser.add_argument("--host", type=str, default=os.getenv("WEBHOOK_HOST", "0.0.0.0"), help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=int(os.getenv("WEBHOOK_PORT", "8080")), help="Bind port (default: 8080)")
    parser.add_argument("--tunnel", type=str, choices=["none", "ngrok"], default=os.getenv("WEBHOOK_TUNNEL", "none"), help="Expose server publicly via tunnel (default: none)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv or sys.argv[1:])
    try:
        asyncio.run(_bootstrap(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()



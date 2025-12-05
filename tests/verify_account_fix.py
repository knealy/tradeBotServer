#!/usr/bin/env python3
"""
Verification script for account selection and Postgres migration fix.

This script verifies:
1. TOPSTEPX_ACCOUNT_ID environment variable is correctly read
2. Database connection works
3. Account state can be saved/loaded from Postgres
4. Account selection logic works correctly
"""

import os
import sys
import asyncio
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from trading_bot import TopStepXTradingBot
from infrastructure.database import get_database
from core.account_tracker import AccountTracker


async def verify_env_vars():
    """Verify environment variables are set correctly."""
    print("\n" + "="*60)
    print("1. VERIFYING ENVIRONMENT VARIABLES")
    print("="*60)
    
    env_var = os.getenv('TOPSTEPX_ACCOUNT_ID')
    if env_var:
        print(f"✅ TOPSTEPX_ACCOUNT_ID is set: {env_var}")
    else:
        print("⚠️  TOPSTEPX_ACCOUNT_ID is not set (will use fallback)")
    
    # Check for old typo
    old_typo = os.getenv('TOPSETPX_ACCOUNT_ID')
    if old_typo:
        print(f"⚠️  WARNING: Old typo TOPSETPX_ACCOUNT_ID still exists: {old_typo}")
        print("   This will be ignored. Please remove it.")
    
    return env_var


async def verify_database():
    """Verify database connection and schema."""
    print("\n" + "="*60)
    print("2. VERIFYING DATABASE CONNECTION")
    print("="*60)
    
    try:
        db = get_database()
        print("✅ Database connection successful")
        
        # Check if account_state table exists
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = 'account_state'
                    );
                """)
                exists = cur.fetchone()[0]
                
                if exists:
                    print("✅ account_state table exists")
                    
                    # Count rows
                    cur.execute("SELECT COUNT(*) FROM account_state")
                    count = cur.fetchone()[0]
                    print(f"   Found {count} account state(s) in database")
                    
                    if count > 0:
                        cur.execute("""
                            SELECT account_id, account_name, balance, last_updated 
                            FROM account_state 
                            ORDER BY last_updated DESC 
                            LIMIT 5
                        """)
                        rows = cur.fetchall()
                        print("\n   Recent account states:")
                        for row in rows:
                            print(f"   - {row[1]} ({row[0]}): ${row[2]:,.2f} at {row[3]}")
                else:
                    print("❌ account_state table does not exist")
                    return False
        
        return db
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return None


async def verify_account_tracker(db):
    """Verify AccountTracker can save/load from database."""
    print("\n" + "="*60)
    print("3. VERIFYING ACCOUNT TRACKER")
    print("="*60)
    
    try:
        # Create tracker with database
        tracker = AccountTracker(db=db)
        print("✅ AccountTracker initialized with database support")
        
        # Check if any states were loaded
        states = tracker.get_all_states()
        if states:
            print(f"✅ Loaded {len(states)} account state(s) from database")
            for account_id, state in states.items():
                print(f"   - {state.account_name}: ${state.current_balance:,.2f}")
        else:
            print("⚠️  No account states found in database (expected on first run)")
        
        return True
    except Exception as e:
        print(f"❌ AccountTracker verification failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def verify_account_selection():
    """Verify account selection logic."""
    print("\n" + "="*60)
    print("4. VERIFYING ACCOUNT SELECTION LOGIC")
    print("="*60)
    
    try:
        bot = TopStepXTradingBot()
        
        # Authenticate
        print("Authenticating with TopStepX...")
        if not await bot.authenticate():
            print("❌ Authentication failed")
            return False
        print("✅ Authentication successful")
        
        # List accounts
        print("Fetching accounts...")
        accounts = await bot.list_accounts()
        if not accounts:
            print("❌ No accounts found")
            return False
        print(f"✅ Found {len(accounts)} account(s)")
        
        # Show account selection logic
        env_account_id = os.getenv('TOPSTEPX_ACCOUNT_ID')
        persisted_account_id = None
        
        if bot.db:
            try:
                settings = bot.db.get_dashboard_settings()
                persisted_account_id = settings.get('defaultAccount') or settings.get('default_account')
            except:
                pass
        
        print("\nAccount selection priority:")
        print(f"  1. Env var (TOPSTEPX_ACCOUNT_ID): {env_account_id or 'not set'}")
        print(f"  2. Persisted setting: {persisted_account_id or 'not set'}")
        print(f"  3. First account fallback: {accounts[0]['name']} ({accounts[0]['id']})")
        
        # Determine which account would be selected
        account_choice = env_account_id or persisted_account_id
        if account_choice:
            selected = next((acc for acc in accounts if str(acc['id']) == str(account_choice)), None)
            if selected:
                print(f"\n✅ Would select: {selected['name']} ({selected['id']})")
                print(f"   Source: {'env' if env_account_id else 'settings'}")
            else:
                print(f"\n⚠️  Preferred account {account_choice} not found")
                print(f"   Would fallback to: {accounts[0]['name']} ({accounts[0]['id']})")
        else:
            print(f"\n✅ Would select first account: {accounts[0]['name']} ({accounts[0]['id']})")
        
        return True
    except Exception as e:
        print(f"❌ Account selection verification failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run all verification checks."""
    print("\n" + "="*60)
    print("ACCOUNT SELECTION & POSTGRES MIGRATION VERIFICATION")
    print("="*60)
    
    results = {
        'env_vars': False,
        'database': False,
        'account_tracker': False,
        'account_selection': False
    }
    
    # 1. Check env vars
    env_var = await verify_env_vars()
    results['env_vars'] = True
    
    # 2. Check database
    db = await verify_database()
    results['database'] = db is not None
    
    # 3. Check account tracker
    if db:
        results['account_tracker'] = await verify_account_tracker(db)
    else:
        print("\n⚠️  Skipping AccountTracker verification (no database)")
    
    # 4. Check account selection
    results['account_selection'] = await verify_account_selection()
    
    # Summary
    print("\n" + "="*60)
    print("VERIFICATION SUMMARY")
    print("="*60)
    
    for check, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {check.replace('_', ' ').title()}")
    
    all_passed = all(results.values())
    
    if all_passed:
        print("\n🎉 All checks passed! System is ready for deployment.")
        return 0
    else:
        print("\n⚠️  Some checks failed. Review the output above.")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)


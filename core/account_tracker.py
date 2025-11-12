"""
Real-Time Account State Tracker for TopstepX Accounts

This module implements comprehensive real-time tracking of account metrics
without relying on API endpoints that may not exist. It computes all
metrics locally based on positions, orders, and live quotes.

Based on TopstepX rules and requirements from topstep_info_profile.md
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from threading import Lock

logger = logging.getLogger(__name__)


@dataclass
class AccountState:
    """Real-time account state with all tracking metrics."""
    
    # Account identification
    account_id: str
    account_name: str
    account_type: str  # 'evaluation', 'express_funded', 'live_funded', 'practice'
    
    # Balance tracking
    starting_balance: float
    current_balance: float
    highest_EOD_balance: float
    
    # PnL tracking
    realised_PnL: float
    unrealised_PnL: float
    commissions: float
    fees: float
    
    # Risk limits
    daily_loss_limit: float
    maximum_loss_limit: float
    drawdown_threshold: float
    
    # Compliance status
    is_compliant: bool
    violation_reason: Optional[str]
    
    # Timestamps
    last_update: str
    last_EOD_update: str
    
    # Statistics
    total_trades: int
    winning_trades: int
    losing_trades: int
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)
    
    @property
    def net_PnL(self) -> float:
        """Calculate net PnL: (realised + unrealised) - (commissions + fees)"""
        return (self.realised_PnL + self.unrealised_PnL) - (self.commissions + self.fees)
    
    @property
    def drawdown_from_high(self) -> float:
        """Calculate drawdown from highest EOD balance."""
        return self.highest_EOD_balance - self.current_balance
    
    @property
    def remaining_daily_loss(self) -> float:
        """Calculate remaining daily loss capacity."""
        daily_pnl = self.realised_PnL + self.unrealised_PnL - (self.commissions + self.fees)
        return self.daily_loss_limit + daily_pnl  # Returns positive if within limit
    
    @property
    def remaining_total_loss(self) -> float:
        """Calculate remaining capacity before max loss limit."""
        return self.current_balance - self.drawdown_threshold


class AccountTracker:
    """
    Real-time account state tracker that computes all metrics locally.
    
    This class maintains accurate account state by:
    1. Tracking all filled orders to compute realised PnL
    2. Monitoring open positions with live quotes for unrealised PnL
    3. Computing compliance with daily/maximum loss limits
    4. Persisting state to disk for continuity across restarts
    """
    
    def __init__(self, state_file: str = ".account_state.json"):
        """
        Initialize account tracker.
        
        Args:
            state_file: Path to file for persisting account state
        """
        self.state_file = Path(state_file)
        self.accounts: Dict[str, AccountState] = {}
        self.lock = Lock()
        self.current_account_id: Optional[str] = None  # Track current active account
        
        # Load persisted state if available
        self._load_state()
    
    def initialize_account(self, account_id: str, account_name: str, account_type: str,
                          starting_balance: float, daily_loss_limit: Optional[float] = None,
                          maximum_loss_limit: Optional[float] = None) -> AccountState:
        """
        Initialize tracking for a new account or reset existing account.
        
        Args:
            account_id: Account ID
            account_name: Account name/number
            account_type: Type of account (evaluation, express_funded, live_funded, practice)
            starting_balance: Starting balance
            daily_loss_limit: Daily loss limit (auto-detected if None)
            maximum_loss_limit: Maximum loss limit (auto-detected if None)
            
        Returns:
            Initialized AccountState
        """
        # Auto-detect limits based on account type if not provided
        if daily_loss_limit is None or maximum_loss_limit is None:
            daily_loss_limit, maximum_loss_limit = self._detect_limits(
                account_name, account_type, starting_balance
            )
        
        state = AccountState(
            account_id=account_id,
            account_name=account_name,
            account_type=account_type,
            starting_balance=starting_balance,
            current_balance=starting_balance,
            highest_EOD_balance=starting_balance,
            realised_PnL=0.0,
            unrealised_PnL=0.0,
            commissions=0.0,
            fees=0.0,
            daily_loss_limit=daily_loss_limit,
            maximum_loss_limit=maximum_loss_limit,
            drawdown_threshold=starting_balance - maximum_loss_limit,
            is_compliant=True,
            violation_reason=None,
            last_update=datetime.now(timezone.utc).isoformat(),
            last_EOD_update=datetime.now(timezone.utc).isoformat(),
            total_trades=0,
            winning_trades=0,
            losing_trades=0
        )
        
        with self.lock:
            self.accounts[account_id] = state
            self._save_state()
        
        logger.info(f"Initialized tracking for account {account_name} ({account_id})")
        logger.info(f"  Starting Balance: ${starting_balance:,.2f}")
        logger.info(f"  Daily Loss Limit: ${daily_loss_limit:,.2f}")
        logger.info(f"  Maximum Loss Limit: ${maximum_loss_limit:,.2f}")
        logger.info(f"  Drawdown Threshold: ${state.drawdown_threshold:,.2f}")
        
        return state
    
    def _detect_limits(self, account_name: str, account_type: str, 
                       starting_balance: float) -> Tuple[float, float]:
        """
        Auto-detect daily and maximum loss limits based on account details.
        
        Based on TopstepX standard rules:
        - $50K accounts: DLL=$1,000, MLL=$2,000
        - $100K accounts: DLL=$2,000, MLL=$3,000
        - $150K accounts: DLL=$3,000, MLL=$4,500
        - Practice accounts: DLL=$1,000, MLL=$2,500
        - Express accounts: DLL=$250, MLL=$500
        
        Args:
            account_name: Account name for pattern matching
            account_type: Account type
            starting_balance: Starting balance for fallback calculation
            
        Returns:
            Tuple of (daily_loss_limit, maximum_loss_limit)
        """
        account_name_upper = account_name.upper()
        
        # Check for specific account patterns
        if 'PRAC' in account_name_upper or account_type == 'practice':
            return (1000.0, 2500.0)
        elif 'EXPRESS' in account_name_upper or account_type == 'express_funded':
            return (250.0, 500.0)
        elif '150K' in account_name_upper:
            return (3000.0, 4500.0)
        elif '100K' in account_name_upper:
            return (2000.0, 3000.0)
        elif '50K' in account_name_upper:
            return (1000.0, 2000.0)
        
        # Fallback based on starting balance
        if starting_balance >= 145000:
            return (3000.0, 4500.0)
        elif starting_balance >= 95000:
            return (2000.0, 3000.0)
        elif starting_balance >= 45000:
            return (1000.0, 2000.0)
        else:
            # Conservative defaults
            return (250.0, 500.0)
    
    def update_from_fill(self, account_id: str, fill_data: Dict) -> AccountState:
        """
        Update account state based on a filled order.
        
        Args:
            account_id: Account ID
            fill_data: Fill data containing side, qty, price, commission, fees
            
        Returns:
            Updated AccountState
        """
        with self.lock:
            if account_id not in self.accounts:
                logger.error(f"Account {account_id} not initialized")
                raise ValueError(f"Account {account_id} not tracked - call initialize_account first")
            
            state = self.accounts[account_id]
            
            # Extract fill details
            side = fill_data.get('side', '').upper()
            qty = fill_data.get('qty', 0)
            price = fill_data.get('price', 0)
            commission = fill_data.get('commission', 0)
            fee = fill_data.get('fee', 0)
            pnl = fill_data.get('pnl', 0)  # Realised PnL from closing trades
            
            # Update PnL and costs
            if pnl != 0:
                state.realised_PnL += pnl
                state.total_trades += 1
                if pnl > 0:
                    state.winning_trades += 1
                else:
                    state.losing_trades += 1
            
            state.commissions += commission
            state.fees += fee
            
            # Recalculate current balance
            state.current_balance = (state.starting_balance + state.realised_PnL + 
                                    state.unrealised_PnL - state.commissions - state.fees)
            
            # Check compliance
            self._check_compliance(state)
            
            # Update timestamp
            state.last_update = datetime.now(timezone.utc).isoformat()
            
            self._save_state()
            
            logger.info(f"Updated account {account_id} from fill: PnL=${pnl:.2f}, Balance=${state.current_balance:,.2f}")
            
            return state
    
    def update_unrealised_pnl(self, account_id: str, positions: List[Dict], 
                              current_prices: Dict[str, float]) -> AccountState:
        """
        Update unrealised PnL based on current positions and live prices.
        
        Args:
            account_id: Account ID
            positions: List of open positions with entry_price, qty, symbol
            current_prices: Dict mapping symbol to current price
            
        Returns:
            Updated AccountState
        """
        with self.lock:
            if account_id not in self.accounts:
                logger.error(f"Account {account_id} not initialized")
                raise ValueError(f"Account {account_id} not tracked")
            
            state = self.accounts[account_id]
            
            # Calculate total unrealised PnL
            total_unrealised = 0.0
            for pos in positions:
                symbol = pos.get('symbol', '').upper()
                qty = pos.get('qty', 0)
                entry_price = pos.get('entry_price', 0)
                side = pos.get('side', '').upper()
                
                current_price = current_prices.get(symbol, entry_price)
                
                # Calculate PnL based on side
                if side == 'BUY' or side == 'LONG':
                    price_diff = current_price - entry_price
                else:  # SELL or SHORT
                    price_diff = entry_price - current_price
                
                # Get tick value (assume $5 per point for micros, adjust as needed)
                tick_value = self._get_tick_value(symbol)
                position_pnl = price_diff * tick_value * qty
                
                total_unrealised += position_pnl
            
            state.unrealised_PnL = total_unrealised
            
            # Recalculate current balance
            state.current_balance = (state.starting_balance + state.realised_PnL + 
                                    state.unrealised_PnL - state.commissions - state.fees)
            
            # Check compliance
            self._check_compliance(state)
            
            # Update timestamp
            state.last_update = datetime.now(timezone.utc).isoformat()
            
            self._save_state()
            
            return state
    
    def _get_tick_value(self, symbol: str) -> float:
        """
        Get tick value for a symbol.
        
        Returns dollars per point move.
        """
        symbol_upper = symbol.upper()
        
        # Micro contracts
        if symbol_upper in ['MNQ', 'MNQZ25', 'MNQ.Z25']:
            return 2.0  # $2 per point
        elif symbol_upper in ['MES', 'MESZ25', 'MES.Z25']:
            return 5.0  # $5 per point
        elif symbol_upper in ['MYM', 'MYMZ25', 'MYM.Z25']:
            return 0.5  # $0.50 per point
        elif symbol_upper in ['M2K', 'M2KZ25', 'M2K.Z25']:
            return 0.5  # $0.50 per point
        
        # Full-size contracts
        elif symbol_upper in ['NQ', 'NQZ25', 'NQ.Z25']:
            return 20.0  # $20 per point
        elif symbol_upper in ['ES', 'ESZ25', 'ES.Z25']:
            return 50.0  # $50 per point
        elif symbol_upper in ['YM', 'YMZ25', 'YM.Z25']:
            return 5.0  # $5 per point
        elif symbol_upper in ['RTY', 'RTYZ25', 'RTY.Z25']:
            return 50.0  # $50 per point
        
        # Default to $1 per point if unknown
        logger.warning(f"Unknown symbol {symbol}, using default tick value $1")
        return 1.0
    
    def _check_compliance(self, state: AccountState) -> None:
        """
        Check if account is compliant with loss limits.
        
        Updates state.is_compliant and state.violation_reason
        """
        # Check daily loss limit
        daily_pnl = state.net_PnL
        if daily_pnl <= -state.daily_loss_limit:
            state.is_compliant = False
            state.violation_reason = f"Daily loss limit breached: ${daily_pnl:.2f} <= ${-state.daily_loss_limit:.2f}"
            logger.error(f"❌ {state.violation_reason}")
            return
        
        # Check maximum loss limit (trailing drawdown)
        if state.current_balance <= state.drawdown_threshold:
            state.is_compliant = False
            state.violation_reason = f"Maximum loss limit breached: ${state.current_balance:.2f} <= ${state.drawdown_threshold:.2f}"
            logger.error(f"❌ {state.violation_reason}")
            return
        
        # If we get here, account is compliant
        state.is_compliant = True
        state.violation_reason = None
    
    def update_EOD(self, account_id: str) -> AccountState:
        """
        Perform end-of-day update.
        
        Should be called at market close (21:00 UTC for CME).
        Updates highest_EOD_balance if new high is reached.
        
        Args:
            account_id: Account ID
            
        Returns:
            Updated AccountState
        """
        with self.lock:
            if account_id not in self.accounts:
                raise ValueError(f"Account {account_id} not tracked")
            
            state = self.accounts[account_id]
            
            # Update highest EOD balance if new high
            if state.current_balance > state.highest_EOD_balance:
                old_high = state.highest_EOD_balance
                state.highest_EOD_balance = state.current_balance
                # Recalculate drawdown threshold (MLL moves up with new highs)
                state.drawdown_threshold = state.highest_EOD_balance - state.maximum_loss_limit
                logger.info(f"📈 New EOD high for {state.account_name}: ${state.highest_EOD_balance:,.2f} (was ${old_high:,.2f})")
                logger.info(f"   New drawdown threshold: ${state.drawdown_threshold:,.2f}")
            
            # Reset daily tracking
            state.last_EOD_update = datetime.now(timezone.utc).isoformat()
            
            # Note: Realised PnL is NOT reset daily - it's cumulative
            # Daily loss limit is checked against TODAY's net PnL
            
            self._save_state()
            
            logger.info(f"EOD update completed for {state.account_name}")
            
            return state
    
    def get_state(self, account_id: Optional[str] = None) -> Optional[Dict]:
        """
        Get current state for an account.
        
        Args:
            account_id: Account ID (uses current account if None)
            
        Returns:
            Dict with account state or None if not found
        """
        with self.lock:
            target_id = account_id or self.current_account_id
            if not target_id or target_id not in self.accounts:
                # Return empty state if not initialized
                return {
                    'account_id': target_id or 'unknown',
                    'account_name': 'Unknown',
                    'starting_balance': 0.0,
                    'current_balance': 0.0,
                    'realized_pnl': 0.0,
                    'unrealized_pnl': 0.0,
                    'total_pnl': 0.0,
                    'highest_eod_balance': 0.0,
                    'position_count': 0,
                    'positions': {},
                    'last_update': datetime.now(timezone.utc).isoformat(),
                    'account_type': 'unknown'
                }
            
            state = self.accounts[target_id]
            return {
                'account_id': state.account_id,
                'account_name': state.account_name,
                'starting_balance': state.starting_balance,
                'current_balance': state.current_balance,
                'realized_pnl': state.realised_PnL,
                'unrealized_pnl': state.unrealised_PnL,
                'total_pnl': state.net_PnL,
                'highest_eod_balance': state.highest_EOD_balance,
                'position_count': 0,  # TODO: Track positions
                'positions': {},  # TODO: Track positions
                'last_update': state.last_update,
                'account_type': state.account_type
            }
    
    def get_all_states(self) -> Dict[str, AccountState]:
        """Get all tracked account states."""
        with self.lock:
            return self.accounts.copy()
    
    def initialize(self, account_id: str, starting_balance: float, account_type: str) -> None:
        """
        Convenience method to initialize account tracking.
        
        Args:
            account_id: Account ID
            starting_balance: Starting balance
            account_type: Account type
        """
        account_name = f"Account-{account_id}"
        self.current_account_id = str(account_id)
        self.initialize_account(
            account_id=str(account_id),
            account_name=account_name,
            account_type=account_type,
            starting_balance=starting_balance
        )
    
    def check_compliance(self, account_id: Optional[str] = None) -> Dict:
        """
        Check compliance for current or specified account.
        
        Args:
            account_id: Account ID (uses current account if None)
            
        Returns:
            Dict with compliance status
        """
        with self.lock:
            target_id = account_id or self.current_account_id
            if not target_id or target_id not in self.accounts:
                return {
                    'is_compliant': True,
                    'dll_limit': None,
                    'dll_used': 0.0,
                    'dll_remaining': 0.0,
                    'dll_violated': False,
                    'mll_limit': None,
                    'mll_used': 0.0,
                    'mll_remaining': 0.0,
                    'mll_violated': False,
                    'trailing_loss': 0.0,
                    'violations': []
                }
            
            state = self.accounts[target_id]
            daily_pnl = state.net_PnL
            dll_violated = daily_pnl <= -state.daily_loss_limit
            
            trailing_loss = state.drawdown_from_high
            mll_violated = state.current_balance <= state.drawdown_threshold
            
            violations = []
            if dll_violated:
                violations.append(f"Daily loss limit exceeded: ${daily_pnl:.2f}")
            if mll_violated:
                violations.append(f"Maximum loss limit exceeded: ${trailing_loss:.2f}")
            
            return {
                'is_compliant': state.is_compliant,
                'dll_limit': state.daily_loss_limit,
                'dll_used': abs(min(0, daily_pnl)),
                'dll_remaining': max(0, state.daily_loss_limit + daily_pnl),
                'dll_violated': dll_violated,
                'mll_limit': state.maximum_loss_limit,
                'mll_used': trailing_loss,
                'mll_remaining': max(0, state.maximum_loss_limit - trailing_loss),
                'mll_violated': mll_violated,
                'trailing_loss': trailing_loss,
                'violations': violations
            }
    
    def update_eod_balance(self, balance: float, account_id: Optional[str] = None) -> None:
        """
        Update end-of-day balance for current or specified account.
        
        Args:
            balance: New balance
            account_id: Account ID (uses current account if None)
        """
        target_id = account_id or self.current_account_id
        if target_id:
            self.update_EOD(str(target_id))
    
    def _save_state(self) -> None:
        """Persist account state to disk."""
        try:
            state_dict = {
                account_id: state.to_dict()
                for account_id, state in self.accounts.items()
            }
            
            with open(self.state_file, 'w') as f:
                json.dump(state_dict, f, indent=2)
            
            logger.debug(f"Saved account state to {self.state_file}")
        except Exception as e:
            logger.error(f"Failed to save account state: {e}")
    
    def _load_state(self) -> None:
        """Load persisted account state from disk."""
        if not self.state_file.exists():
            logger.info("No persisted account state found")
            return
        
        try:
            with open(self.state_file, 'r') as f:
                state_dict = json.load(f)
            
            for account_id, data in state_dict.items():
                self.accounts[account_id] = AccountState(**data)
            
            logger.info(f"Loaded state for {len(self.accounts)} accounts from {self.state_file}")
        except Exception as e:
            logger.error(f"Failed to load account state: {e}")


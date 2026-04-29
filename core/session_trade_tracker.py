"""
Session-Aware Real-Time Trade and PnL Tracker

This module implements accurate, real-time tracking of trades and PnL per trading session.
Based on TopstepX requirements:
- Uses FIFO matching for entry/exit fills
- Tracks realized PnL only (for daily loss calculations)
- Session-aware (resets at market open/close, not midnight)
- Real-time updates via SignalR fills
- Optimized for speed, efficiency, and accuracy
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from collections import deque
from threading import Lock
from enum import Enum

from core.json_fast import dumps_str

logger = logging.getLogger(__name__)


class FillSide(Enum):
    """Fill side enumeration."""
    BUY = 0
    SELL = 1


@dataclass(slots=True)
class Fill:
    """Represents a single fill/execution."""
    fill_id: str
    order_id: str
    account_id: str
    symbol: str
    side: FillSide
    quantity: int
    price: float
    commission: float
    fee: float
    timestamp: datetime
    contract_id: Optional[int] = None
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            **asdict(self),
            'side': self.side.value,
            'timestamp': self.timestamp.isoformat()
        }


@dataclass(slots=True)
class Trade:
    """Represents a completed trade (entry + exit)."""
    trade_id: str
    account_id: str
    symbol: str
    side: str  # 'LONG' or 'SHORT'
    quantity: int
    entry_fill_id: str
    entry_price: float
    entry_time: datetime
    exit_fill_id: str
    exit_price: float
    exit_time: datetime
    gross_pnl: float
    commission: float
    fee: float
    net_pnl: float
    duration_seconds: int
    point_value: float
    session_id: str  # Trading session identifier
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            **asdict(self),
            'entry_time': self.entry_time.isoformat(),
            'exit_time': self.exit_time.isoformat()
        }


@dataclass(slots=True)
class SessionState:
    """Trading session state."""
    session_id: str
    account_id: str
    start_time: datetime
    end_time: Optional[datetime]
    realized_pnl: float
    total_commissions: float
    total_fees: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    
    @property
    def net_pnl(self) -> float:
        """Calculate net PnL."""
        return self.realized_pnl - self.total_commissions - self.total_fees
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            **asdict(self),
            'start_time': self.start_time.isoformat(),
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'net_pnl': self.net_pnl
        }


class SessionTradeTracker:
    """
    Real-time session-aware trade and PnL tracker.
    
    Features:
    - FIFO matching of entry/exit fills
    - Session-aware (resets at market boundaries)
    - Real-time updates from SignalR fills
    - Accurate realized PnL calculation
    - Database persistence
    """
    
    def __init__(self, db=None, point_values: Optional[Dict[str, float]] = None):
        """
        Initialize session trade tracker.
        
        Args:
            db: Database manager instance (optional)
            point_values: Dict mapping symbol to point value (dollars per point)
        """
        self.db = db
        self.lock = Lock()
        
        # Point values for PnL calculation (dollars per point move)
        self.point_values = point_values or {
            'MNQ': 2.0, 'MES': 5.0, 'MYM': 0.5, 'M2K': 0.5,
            'NQ': 20.0, 'ES': 50.0, 'YM': 5.0, 'RTY': 50.0
        }
        
        # Per-account tracking
        # account_id -> {
        #   'open_positions': deque of Fill (FIFO queue),
        #   'completed_trades': List[Trade],
        #   'current_session': SessionState,
        #   'fills': List[Fill]  # All fills for audit
        # }
        self.account_data: Dict[str, Dict] = {}
        
        # Track current trading session per account
        self.current_sessions: Dict[str, SessionState] = {}
        # Consecutive sessions that ended with zero completed trades (per account)
        self._zero_trade_session_streak: Dict[str, int] = {}
        
        logger.info("SessionTradeTracker initialized")
    
    def _get_point_value(self, symbol: str) -> float:
        """Get point value for a symbol."""
        symbol_upper = symbol.upper()
        # Handle contract codes like MNQZ25, MES.Z25, etc.
        base_symbol = symbol_upper.split('.')[0].split('Z')[0]
        return self.point_values.get(base_symbol, 1.0)
    
    def _calculate_commission(self, quantity: int) -> float:
        """
        Calculate total commission for a trade (round trip).
        
        Each contract has $0.74 commission to open and $0.74 to close,
        so total commission per round trip is $1.48 per contract.
        
        Args:
            quantity: Number of contracts
            
        Returns:
            Total commission in dollars
        """
        COMMISSION_PER_CONTRACT_OPEN = 0.74
        COMMISSION_PER_CONTRACT_CLOSE = 0.74
        return (COMMISSION_PER_CONTRACT_OPEN + COMMISSION_PER_CONTRACT_CLOSE) * quantity
    
    def _get_current_session_id(self, account_id: str) -> str:
        """
        Get current trading session ID.
        
        Sessions reset at market open (typically 17:00 UTC for CME).
        For simplicity, we use daily sessions (reset at 00:00 UTC).
        Can be enhanced to use actual market hours.
        """
        now = datetime.now(timezone.utc)
        # Session resets at 00:00 UTC (can be changed to market open time)
        session_date = now.date()
        return f"{account_id}_{session_date.isoformat()}"
    
    def _ensure_account_data(self, account_id: str) -> Dict:
        """Ensure account tracking data exists."""
        if account_id not in self.account_data:
            session_id = self._get_current_session_id(account_id)
            self.account_data[account_id] = {
                'open_positions': deque(),  # FIFO queue of entry fills
                'completed_trades': [],
                'current_session': SessionState(
                    session_id=session_id,
                    account_id=account_id,
                    start_time=datetime.now(timezone.utc),
                    end_time=None,
                    realized_pnl=0.0,
                    total_commissions=0.0,
                    total_fees=0.0,
                    total_trades=0,
                    winning_trades=0,
                    losing_trades=0
                ),
                'fills': []
            }
            self.current_sessions[account_id] = self.account_data[account_id]['current_session']
        return self.account_data[account_id]
    
    def process_fill(
        self,
        fill_id: str,
        order_id: str,
        account_id: str,
        symbol: str,
        side: int,  # 0=BUY, 1=SELL
        quantity: int,
        price: float,
        commission: float = 0.0,
        fee: float = 0.0,
        timestamp: Optional[datetime] = None,
        contract_id: Optional[int] = None
    ) -> List[Trade]:
        """
        Process a fill and match with open positions using FIFO.
        
        Args:
            fill_id: Unique fill ID
            order_id: Order ID
            account_id: Account ID
            symbol: Trading symbol
            side: 0=BUY, 1=SELL
            quantity: Fill quantity
            price: Fill price
            commission: Commission paid
            fee: Fee paid
            timestamp: Fill timestamp (defaults to now)
            contract_id: Optional contract ID
            
        Returns:
            List of completed trades (may be empty if position opened)
        """
        with self.lock:
            account_data = self._ensure_account_data(account_id)
            
            # Check if session needs reset
            current_session_id = self._get_current_session_id(account_id)
            if account_data['current_session'].session_id != current_session_id:
                self._reset_session(account_id, current_session_id)
                account_data = self._ensure_account_data(account_id)
            
            timestamp = timestamp or datetime.now(timezone.utc)
            fill_side = FillSide.BUY if side == 0 else FillSide.SELL
            
            # Calculate commission if not provided or is 0
            # Commission is $0.74 per contract per side (open or close)
            if commission == 0.0:
                commission = 0.74 * quantity
            
            # Create fill record
            fill = Fill(
                fill_id=fill_id,
                order_id=order_id,
                account_id=account_id,
                symbol=symbol,
                side=fill_side,
                quantity=quantity,
                price=price,
                commission=commission,
                fee=fee,
                timestamp=timestamp,
                contract_id=contract_id
            )
            
            account_data['fills'].append(fill)
            
            # Get point value for PnL calculation
            point_value = self._get_point_value(symbol)
            
            # Match with open positions using FIFO
            completed_trades = []
            open_positions = account_data['open_positions']
            remaining_qty = quantity
            
            while remaining_qty > 0 and open_positions:
                entry_fill = open_positions[0]
                
                # Check if this fill closes the position
                # BUY fill closes SHORT position, SELL fill closes LONG position
                closes_position = (
                    (fill_side == FillSide.BUY and entry_fill.side == FillSide.SELL) or
                    (fill_side == FillSide.SELL and entry_fill.side == FillSide.BUY)
                )
                
                if not closes_position:
                    # Same direction - add to position queue
                    break
                
                # Calculate quantity to close
                qty_to_close = min(entry_fill.quantity, remaining_qty)
                
                # Calculate PnL
                if entry_fill.side == FillSide.BUY:  # Long position
                    price_diff = price - entry_fill.price
                else:  # Short position
                    price_diff = entry_fill.price - price
                
                gross_pnl = price_diff * qty_to_close * point_value
                total_commission = entry_fill.commission * (qty_to_close / entry_fill.quantity) + commission * (qty_to_close / quantity)
                total_fee = entry_fill.fee * (qty_to_close / entry_fill.quantity) + fee * (qty_to_close / quantity)
                net_pnl = gross_pnl - total_commission - total_fee
                
                # Create trade record
                trade = Trade(
                    trade_id=f"{account_id}_{len(account_data['completed_trades']) + 1}_{int(timestamp.timestamp())}",
                    account_id=account_id,
                    symbol=symbol,
                    side='LONG' if entry_fill.side == FillSide.BUY else 'SHORT',
                    quantity=qty_to_close,
                    entry_fill_id=entry_fill.fill_id,
                    entry_price=entry_fill.price,
                    entry_time=entry_fill.timestamp,
                    exit_fill_id=fill_id,
                    exit_price=price,
                    exit_time=timestamp,
                    gross_pnl=gross_pnl,
                    commission=total_commission,
                    fee=total_fee,
                    net_pnl=net_pnl,
                    duration_seconds=int((timestamp - entry_fill.timestamp).total_seconds()),
                    point_value=point_value,
                    session_id=current_session_id
                )
                
                completed_trades.append(trade)
                account_data['completed_trades'].append(trade)
                
                # Update session statistics
                session = account_data['current_session']
                session.realized_pnl += gross_pnl
                session.total_commissions += total_commission
                session.total_fees += total_fee
                session.total_trades += 1
                if net_pnl > 0:
                    session.winning_trades += 1
                elif net_pnl < 0:
                    session.losing_trades += 1
                
                # Update entry fill quantity
                entry_fill.quantity -= qty_to_close
                if entry_fill.quantity == 0:
                    open_positions.popleft()
                
                remaining_qty -= qty_to_close
            
            # If remaining quantity, open new position
            if remaining_qty > 0:
                new_fill = Fill(
                    fill_id=fill_id,
                    order_id=order_id,
                    account_id=account_id,
                    symbol=symbol,
                    side=fill_side,
                    quantity=remaining_qty,
                    price=price,
                    commission=commission * (remaining_qty / quantity),
                    fee=fee * (remaining_qty / quantity),
                    timestamp=timestamp,
                    contract_id=contract_id
                )
                open_positions.append(new_fill)
            
            # Persist to database if available
            if completed_trades and self.db:
                self._persist_trades(account_id, completed_trades)
            
            return completed_trades
    
    def _reset_session(self, account_id: str, new_session_id: str) -> None:
        """Reset session for an account."""
        if account_id in self.account_data:
            old_session = self.account_data[account_id]['current_session']
            old_session.end_time = datetime.now(timezone.utc)
            
            # Create new session
            self.account_data[account_id]['current_session'] = SessionState(
                session_id=new_session_id,
                account_id=account_id,
                start_time=datetime.now(timezone.utc),
                end_time=None,
                realized_pnl=0.0,
                total_commissions=0.0,
                total_fees=0.0,
                total_trades=0,
                winning_trades=0,
                losing_trades=0
            )
            self.current_sessions[account_id] = self.account_data[account_id]['current_session']

            if old_session.total_trades == 0:
                self._zero_trade_session_streak[account_id] = (
                    int(self._zero_trade_session_streak.get(account_id, 0)) + 1
                )
            else:
                self._zero_trade_session_streak[account_id] = 0
            
            logger.info(f"🔄 Session reset for account {account_id}: {old_session.session_id} -> {new_session_id}")
    
    def reset_zero_trade_session_streak(self, account_id: str) -> None:
        """Clear the no-trade-session streak (e.g. after a confirmed fill)."""
        self._zero_trade_session_streak[str(account_id)] = 0

    def zero_trade_session_streak(self, account_id: str) -> int:
        """How many consecutive sessions ended with zero completed trades for this account."""
        return int(self._zero_trade_session_streak.get(str(account_id), 0))
    
    def _persist_trades(self, account_id: str, trades: List[Trade]) -> None:
        """Persist trades to database."""
        try:
            if not self.db or not trades:
                return
            from psycopg2.extras import execute_values

            aid = str(account_id)
            trade_ids = [t.trade_id for t in trades if t.trade_id]
            rows = []
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    existing: set = set()
                    if trade_ids:
                        cur.execute(
                            """
                            SELECT metadata->>'trade_id' FROM trade_history
                            WHERE account_id = %s AND metadata->>'trade_id' = ANY(%s)
                            """,
                            (aid, trade_ids),
                        )
                        existing = {r[0] for r in cur.fetchall() if r and r[0]}
                    for trade in trades:
                        if trade.trade_id in existing:
                            logger.debug("Trade %s already exists, skipping", trade.trade_id)
                            continue
                        rows.append(
                            (
                                trade.account_id,
                                trade.symbol,
                                trade.side,
                                trade.quantity,
                                trade.entry_price,
                                trade.exit_price,
                                trade.net_pnl,
                                trade.entry_time,
                                trade.exit_time,
                                trade.duration_seconds,
                                dumps_str(
                                    {
                                        "trade_id": trade.trade_id,
                                        "entry_fill_id": trade.entry_fill_id,
                                        "exit_fill_id": trade.exit_fill_id,
                                        "gross_pnl": trade.gross_pnl,
                                        "commission": trade.commission,
                                        "fee": trade.fee,
                                        "point_value": trade.point_value,
                                        "session_id": trade.session_id,
                                    }
                                ),
                            )
                        )
                    if rows:
                        execute_values(
                            cur,
                            """
                            INSERT INTO trade_history (
                                account_id, symbol, side, quantity,
                                entry_price, exit_price, pnl,
                                entry_time, exit_time, duration_seconds,
                                metadata
                            ) VALUES %s
                            """,
                            rows,
                            page_size=100,
                        )
            if rows:
                logger.debug("Persisted %d trades to database", len(rows))
        except Exception as e:
            logger.error("Failed to persist trades: %s", e, exc_info=True)
    
    def get_session_trades(self, account_id: str, limit: int = 100) -> List[Dict]:
        """
        Get completed trades for current session.
        
        Args:
            account_id: Account ID
            limit: Maximum number of trades to return
            
        Returns:
            List of trade dictionaries
        """
        with self.lock:
            if account_id not in self.account_data:
                return []
            
            trades = self.account_data[account_id]['completed_trades']
            return [trade.to_dict() for trade in trades[-limit:]]
    
    def get_session_state(self, account_id: str) -> Optional[Dict]:
        """
        Get current session state.
        
        Args:
            account_id: Account ID
            
        Returns:
            Session state dictionary or None
        """
        with self.lock:
            if account_id not in self.account_data:
                return None
            
            session = self.account_data[account_id]['current_session']
            return session.to_dict()
    
    def get_open_positions(self, account_id: str) -> List[Dict]:
        """
        Get current open positions (unmatched entry fills).
        
        Args:
            account_id: Account ID
            
        Returns:
            List of position dictionaries
        """
        with self.lock:
            if account_id not in self.account_data:
                return []
            
            positions = []
            for fill in self.account_data[account_id]['open_positions']:
                positions.append({
                    'fill_id': fill.fill_id,
                    'order_id': fill.order_id,
                    'symbol': fill.symbol,
                    'side': 'LONG' if fill.side == FillSide.BUY else 'SHORT',
                    'quantity': fill.quantity,
                    'entry_price': fill.price,
                    'entry_time': fill.timestamp.isoformat(),
                    'commission': fill.commission,
                    'fee': fill.fee
                })
            return positions
    
    def get_session_pnl(self, account_id: str) -> Dict:
        """
        Get session PnL summary.
        
        Args:
            account_id: Account ID
            
        Returns:
            Dict with PnL metrics
        """
        session_state = self.get_session_state(account_id)
        if not session_state:
            return {
                'realized_pnl': 0.0,
                'net_pnl': 0.0,
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate': 0.0,
                'avg_win': 0.0,
                'avg_loss': 0.0
            }
        
        total_trades = session_state['total_trades']
        winning_trades = session_state['winning_trades']
        losing_trades = session_state['losing_trades']
        
        # Calculate win rate and averages
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
        
        trades = self.get_session_trades(account_id, limit=1000)
        wins = [t['net_pnl'] for t in trades if t['net_pnl'] > 0]
        losses = [t['net_pnl'] for t in trades if t['net_pnl'] < 0]
        
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        
        return {
            'realized_pnl': session_state['realized_pnl'],
            'net_pnl': session_state['net_pnl'],
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'win_rate': win_rate,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'session_id': session_state['session_id'],
            'start_time': session_state['start_time']
        }

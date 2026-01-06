"""
Order Audit Trail - Tracks order metadata for audit and analysis.

Captures "who/why" information for every order:
- Strategy name
- Signal reason
- Signal score
- Config snapshot
- Execution method (rust/python)
- Entry trigger type
"""

import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from pathlib import Path

logger = logging.getLogger(__name__)


class OrderAuditTrail:
    """
    Tracks order metadata for audit and analysis.
    
    Stores order audit information including strategy, signal details,
    execution method, and configuration snapshots.
    """
    
    def __init__(self, audit_file: Optional[str] = None):
        """
        Initialize order audit trail.
        
        Args:
            audit_file: Path to JSON file for storing audit records (default: order_audit.json)
        """
        self.audit_file = Path(audit_file or "order_audit.json")
        self._audit_records: List[Dict[str, Any]] = []
        self._load_audit_records()
    
    def _load_audit_records(self) -> None:
        """Load existing audit records from file."""
        if self.audit_file.exists():
            try:
                with open(self.audit_file, 'r') as f:
                    self._audit_records = json.load(f)
                logger.debug(f"Loaded {len(self._audit_records)} audit records from {self.audit_file}")
            except Exception as e:
                logger.warning(f"Failed to load audit records: {e}")
                self._audit_records = []
        else:
            self._audit_records = []
    
    def _save_audit_records(self) -> None:
        """Save audit records to file."""
        try:
            with open(self.audit_file, 'w') as f:
                json.dump(self._audit_records, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Failed to save audit records: {e}")
    
    def record_order(
        self,
        order_id: str,
        strategy_name: Optional[str] = None,
        signal_reason: Optional[str] = None,
        signal_score: Optional[float] = None,
        config_snapshot: Optional[Dict[str, Any]] = None,
        execution_method: Optional[str] = None,
        entry_trigger: Optional[str] = None,
        **kwargs
    ) -> None:
        """
        Record order metadata in audit trail.
        
        Args:
            order_id: Order ID
            strategy_name: Strategy that placed order (or "manual" for CLI/GUI)
            signal_reason: Signal description (e.g., "2 consecutive +distance closes")
            signal_score: Confidence score (0-100)
            config_snapshot: Strategy config at time of order (JSON-serializable dict)
            execution_method: "rust" or "python" - which path was used
            entry_trigger: "market", "stop", "limit" - how entry was triggered
            **kwargs: Additional metadata fields
        """
        audit_record = {
            'order_id': str(order_id),
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'strategy_name': strategy_name or 'manual',
            'signal_reason': signal_reason,
            'signal_score': signal_score,
            'config_snapshot': config_snapshot,
            'execution_method': execution_method,
            'entry_trigger': entry_trigger,
            **kwargs
        }
        
        self._audit_records.append(audit_record)
        
        # Keep only last 10,000 records to prevent file bloat
        if len(self._audit_records) > 10000:
            self._audit_records = self._audit_records[-10000:]
        
        self._save_audit_records()
        logger.debug(f"Recorded audit trail for order {order_id}: strategy={strategy_name}, method={execution_method}")
    
    def get_order_audit(self, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Get audit record for a specific order.
        
        Args:
            order_id: Order ID
            
        Returns:
            Audit record dict or None if not found
        """
        for record in reversed(self._audit_records):  # Check most recent first
            if record.get('order_id') == str(order_id):
                return record
        return None
    
    def get_strategy_orders(self, strategy_name: str, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get all orders for a specific strategy.
        
        Args:
            strategy_name: Strategy name
            limit: Maximum number of records to return
            
        Returns:
            List of audit records
        """
        records = [
            r for r in self._audit_records
            if r.get('strategy_name') == strategy_name
        ]
        return records[-limit:]  # Most recent first
    
    def get_all_audit_records(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """
        Get all audit records (most recent first).
        
        Args:
            limit: Maximum number of records to return
            
        Returns:
            List of audit records
        """
        return self._audit_records[-limit:]


# Global audit trail instance
_audit_trail: Optional[OrderAuditTrail] = None


def get_audit_trail() -> OrderAuditTrail:
    """Get global audit trail instance."""
    global _audit_trail
    if _audit_trail is None:
        _audit_trail = OrderAuditTrail()
    return _audit_trail


def record_order_audit(order_id: str, **metadata) -> None:
    """
    Convenience function to record order audit.
    
    Args:
        order_id: Order ID
        **metadata: Order metadata (strategy_name, signal_reason, etc.)
    """
    get_audit_trail().record_order(order_id, **metadata)


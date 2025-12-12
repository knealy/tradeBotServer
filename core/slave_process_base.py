"""
Slave Process Base Class

Base class for slave processes that can be controlled by master processes.
Provides common functionality for process state management, health checks, and communication.
"""

import os
import asyncio
import logging
import signal
import sys
from typing import Optional, Dict, Any
from datetime import datetime, timezone
from abc import ABC, abstractmethod

from infrastructure.database import get_database

logger = logging.getLogger(__name__)


class SlaveProcessBase(ABC):
    """Base class for slave processes."""
    
    def __init__(
        self,
        process_id: str,
        process_type: str,
        account_id: Optional[str] = None,
        heartbeat_interval: int = 30
    ):
        """
        Initialize slave process.
        
        Args:
            process_id: Unique process identifier
            process_type: Type of process (e.g., 'strategy_executor', 'order_monitor')
            account_id: Account ID this process is associated with
            heartbeat_interval: Seconds between heartbeats
        """
        self.process_id = process_id
        self.process_type = process_type
        self.account_id = account_id
        self.heartbeat_interval = heartbeat_interval
        
        self.is_running = False
        self.db = get_database()
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        logger.info(f"🛑 Received signal {signum}, shutting down...")
        self.is_running = False
    
    async def start(self):
        """Start the slave process."""
        self.is_running = True
        
        # Save initial state
        await self.update_process_state(status='running')
        
        logger.info(f"🚀 {self.process_type} started (ID: {self.process_id})")
        
        try:
            # Start main loop and heartbeat in parallel
            await asyncio.gather(
                self.run(),
                self._heartbeat_loop()
            )
        except Exception as e:
            logger.error(f"❌ Process error: {e}")
            await self.update_process_state(status='error', metadata={'error': str(e)})
        finally:
            await self.update_process_state(status='stopped')
            logger.info(f"✅ {self.process_type} stopped")
    
    @abstractmethod
    async def run(self):
        """
        Main process loop - must be implemented by subclasses.
        
        This method should contain the main logic of the process.
        It should check self.is_running and exit when False.
        """
        pass
    
    async def update_process_state(
        self,
        status: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """Update process state in database."""
        if not self.db:
            return
        
        current_status = status or ('running' if self.is_running else 'stopped')
        
        # Merge with existing metadata
        existing_metadata = {}
        if metadata:
            existing_metadata.update(metadata)
        
        self.db.save_process_state(
            process_id=self.process_id,
            process_type=self.process_type,
            status=current_status,
            account_id=self.account_id,
            metadata=existing_metadata if existing_metadata else None
        )
    
    async def _heartbeat_loop(self):
        """Send periodic heartbeats to database."""
        while self.is_running:
            try:
                await self.update_process_state()
                await asyncio.sleep(self.heartbeat_interval)
            except Exception as e:
                logger.debug(f"Heartbeat error: {e}")
                await asyncio.sleep(self.heartbeat_interval)
    
    def stop(self):
        """Stop the process."""
        self.is_running = False
        logger.info(f"🛑 Stopping {self.process_type}...")
    
    def get_status(self) -> Dict[str, Any]:
        """Get current process status."""
        return {
            "process_id": self.process_id,
            "process_type": self.process_type,
            "status": "running" if self.is_running else "stopped",
            "account_id": self.account_id,
            "heartbeat_interval": self.heartbeat_interval
        }

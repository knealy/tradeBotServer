"""
Scheduled Tasks Module

Handles scheduled operations like strategy restarts at specific times.
"""

import asyncio
import logging
from datetime import datetime, time
from typing import Optional
import pytz

logger = logging.getLogger(__name__)


class ScheduledTaskManager:
    """
    Manages scheduled tasks like strategy restarts.
    
    Features:
    - Strategy restart at 8 AM ET on weekdays
    - Timezone-aware scheduling
    - Prevents duplicate executions
    """
    
    def __init__(self, trading_bot):
        """
        Initialize scheduled task manager.
        
        Args:
            trading_bot: Reference to TopStepXTradingBot instance
        """
        self.trading_bot = trading_bot
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_restart_date: Optional[str] = None
        self._last_telemetry_prune_date: Optional[str] = None

        # Configuration
        self.restart_time = time(8, 0)  # 8:00 AM
        self.telemetry_prune_time = time(3, 30)  # nightly DB TTL prune (Phase 2.10)
        self.timezone = pytz.timezone('US/Eastern')  # ET timezone

        logger.info("📅 Scheduled Task Manager initialized")
        logger.info(f"   Strategy restart: {self.restart_time.strftime('%H:%M')} ET (weekdays only)")
        logger.info(
            "   DB telemetry prune: %s ET (api_metrics, notifications, strategy_executions + bars)",
            self.telemetry_prune_time.strftime('%H:%M'),
        )
    
    async def start(self):
        """Start the scheduled task manager."""
        if self._running:
            logger.warning("⚠️  Scheduled task manager already running")
            return
        
        self._running = True
        self._task = asyncio.create_task(self._scheduler_loop())
        logger.info("✅ Scheduled task manager started")
    
    async def stop(self):
        """Stop the scheduled task manager."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("🛑 Scheduled task manager stopped")
    
    async def _scheduler_loop(self):
        """Main scheduler loop that checks time and executes tasks."""
        logger.info("🔄 Scheduler loop started")
        
        while self._running:
            try:
                # Get current time in ET
                now_et = datetime.now(self.timezone)
                current_time = now_et.time()
                current_date = now_et.date().isoformat()
                weekday = now_et.weekday()  # 0=Monday, 6=Sunday
                
                # Check if it's a weekday (Monday=0 to Friday=4)
                is_weekday = weekday < 5
                
                # Check if it's time to restart strategy (8:00-8:05 AM window)
                should_restart = (
                    is_weekday and
                    self.restart_time <= current_time < time(8, 5) and
                    self._last_restart_date != current_date
                )
                
                if should_restart:
                    logger.info(f"⏰ Scheduled strategy restart triggered at {current_time.strftime('%H:%M:%S')} ET")
                    await self._restart_strategy()
                    self._last_restart_date = current_date

                should_prune = (
                    self.telemetry_prune_time <= current_time < time(3, 35)
                    and self._last_telemetry_prune_date != current_date
                )
                if should_prune:
                    await self._prune_telemetry_tables()
                    self._last_telemetry_prune_date = current_date

                # Check every minute
                await asyncio.sleep(60)
                
            except asyncio.CancelledError:
                logger.info("🛑 Scheduler loop cancelled")
                break
            except Exception as e:
                logger.error(f"❌ Error in scheduler loop: {e}")
                await asyncio.sleep(60)  # Wait before retrying
    
    async def _restart_strategy(self):
        """
        Restart the overnight range strategy.
        
        This ensures the strategy is fresh and ready for market open at 9:30 AM.
        """
        try:
            if not hasattr(self.trading_bot, 'strategy_manager'):
                logger.warning("⚠️  Strategy manager not available")
                return
            
            strategy_manager = self.trading_bot.strategy_manager
            strategy_name = 'overnight_range'
            
            # Check if strategy exists
            if strategy_name not in strategy_manager.strategies:
                logger.warning(f"⚠️  Strategy '{strategy_name}' not found")
                return
            
            logger.info(f"🔄 Restarting strategy: {strategy_name}")
            
            # Stop strategy if running
            if strategy_name in strategy_manager.active_strategies:
                logger.info(f"   Stopping {strategy_name}...")
                success, message = await strategy_manager.stop_strategy(strategy_name, persist=False)
                if success:
                    logger.info(f"   ✅ {message}")
                    # Small delay to ensure cleanup
                    await asyncio.sleep(2)
                else:
                    logger.warning(f"   ⚠️  {message}")
            
            # Start strategy
            logger.info(f"   Starting {strategy_name}...")
            success, message = await strategy_manager.start_strategy(strategy_name, persist=False)
            
            if success:
                logger.info(f"   ✅ {message}")
                logger.info(f"✅ Strategy restart completed successfully")
            else:
                logger.error(f"   ❌ {message}")
                logger.error(f"❌ Strategy restart failed")
                
        except Exception as e:
            logger.error(f"❌ Error restarting strategy: {e}")
            import traceback
            traceback.print_exc()

    async def _prune_telemetry_tables(self) -> None:
        """Delete DB rows older than retention (see DatabaseManager.cleanup_old_data)."""
        try:
            bot = self.trading_bot
            db = getattr(bot, "db", None)
            if not db or not getattr(db, "pool", None):
                logger.debug("Skipping telemetry prune: no database pool on trading bot")
                return
            await asyncio.to_thread(bot.db.cleanup_old_data)
            logger.info("✅ Nightly DB telemetry prune finished")
        except Exception as e:
            logger.error("❌ Telemetry prune failed: %s", e, exc_info=True)

    def get_next_restart_time(self) -> Optional[datetime]:
        """
        Get the next scheduled restart time.
        
        Returns:
            Next restart datetime in ET, or None if not scheduled
        """
        now_et = datetime.now(self.timezone)
        current_date = now_et.date()
        weekday = now_et.weekday()
        
        # If it's a weekday and before 8 AM, next restart is today
        if weekday < 5 and now_et.time() < self.restart_time:
            next_restart = self.timezone.localize(datetime.combine(current_date, self.restart_time))
            return next_restart
        
        # Otherwise, find next weekday
        from datetime import timedelta
        days_ahead = 0
        while True:
            days_ahead += 1
            next_date = current_date + timedelta(days=days_ahead)
            next_weekday = next_date.weekday()
            
            if next_weekday < 5:  # Monday-Friday
                next_restart = self.timezone.localize(datetime.combine(next_date, self.restart_time))
                return next_restart
            
            if days_ahead > 7:  # Safety limit
                return None


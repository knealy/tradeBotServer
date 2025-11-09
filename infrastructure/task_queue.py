"""
Priority Task Queue for Background Task Optimization

Manages background tasks with priority levels, ensuring critical operations
(like order fills, risk checks) execute before non-critical ones (like logging, metrics).
"""

import asyncio
import logging
from enum import IntEnum
from typing import Callable, Any, Optional, Dict
from dataclasses import dataclass, field
from datetime import datetime
import time

logger = logging.getLogger(__name__)


class TaskPriority(IntEnum):
    """Task priority levels (lower number = higher priority)."""
    CRITICAL = 0    # Order fills, position updates, emergency stops
    HIGH = 1        # Risk checks, account balance updates
    NORMAL = 2      # Strategy execution, signal processing
    LOW = 3         # Logging, metrics collection
    BACKGROUND = 4  # Cleanup, data archival


@dataclass(order=True)
class PriorityTask:
    """A task with priority and metadata."""
    priority: int
    task_id: str = field(compare=False)
    coro: Any = field(compare=False)
    created_at: float = field(default_factory=time.time, compare=False)
    timeout: Optional[float] = field(default=None, compare=False)
    retry_count: int = field(default=0, compare=False)
    max_retries: int = field(default=3, compare=False)


class PriorityTaskQueue:
    """
    Async task queue with priority levels and resource management.
    
    Features:
    - Priority-based execution (CRITICAL tasks first)
    - Concurrency limits (prevent resource exhaustion)
    - Automatic retries with exponential backoff
    - Timeout handling
    - Task cancellation
    - Performance metrics
    """
    
    def __init__(self, max_concurrent: int = 10, max_queue_size: int = 1000):
        """
        Initialize priority task queue.
        
        Args:
            max_concurrent: Maximum concurrent tasks
            max_queue_size: Maximum queued tasks (prevents memory issues)
        """
        self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=max_queue_size)
        self.max_concurrent = max_concurrent
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self.semaphore = asyncio.Semaphore(max_concurrent)
        
        # Metrics
        self.tasks_submitted = 0
        self.tasks_completed = 0
        self.tasks_failed = 0
        self.tasks_timeout = 0
        self.tasks_cancelled = 0
        
        # Workers
        self._workers: list[asyncio.Task] = []
        self._running = False
        
        logger.info(f"✅ Priority task queue initialized (max_concurrent={max_concurrent})")
    
    async def submit(self, 
                    coro: Callable,
                    priority: TaskPriority = TaskPriority.NORMAL,
                    task_id: Optional[str] = None,
                    timeout: Optional[float] = None,
                    max_retries: int = 3) -> str:
        """
        Submit a task to the queue.
        
        Args:
            coro: Async coroutine to execute
            priority: Task priority level
            task_id: Optional unique task ID
            timeout: Task timeout in seconds
            max_retries: Maximum retry attempts
        
        Returns:
            str: Task ID
        """
        if not task_id:
            task_id = f"task_{self.tasks_submitted}_{int(time.time() * 1000)}"
        
        task = PriorityTask(
            priority=priority.value,
            task_id=task_id,
            coro=coro,
            timeout=timeout,
            max_retries=max_retries
        )
        
        try:
            await self.queue.put(task)
            self.tasks_submitted += 1
            logger.debug(f"📝 Task submitted: {task_id} (priority={priority.name}, queue_size={self.queue.qsize()})")
            return task_id
        except asyncio.QueueFull:
            logger.error(f"❌ Queue full! Cannot submit task: {task_id}")
            raise
    
    async def submit_critical(self, coro: Callable, task_id: Optional[str] = None, timeout: float = 30.0) -> str:
        """Submit a CRITICAL priority task (order fills, position updates)."""
        return await self.submit(coro, TaskPriority.CRITICAL, task_id, timeout)
    
    async def submit_high(self, coro: Callable, task_id: Optional[str] = None, timeout: float = 60.0) -> str:
        """Submit a HIGH priority task (risk checks, balance updates)."""
        return await self.submit(coro, TaskPriority.HIGH, task_id, timeout)
    
    async def submit_normal(self, coro: Callable, task_id: Optional[str] = None, timeout: float = 120.0) -> str:
        """Submit a NORMAL priority task (strategy execution)."""
        return await self.submit(coro, TaskPriority.NORMAL, task_id, timeout)
    
    async def submit_low(self, coro: Callable, task_id: Optional[str] = None, timeout: float = 300.0) -> str:
        """Submit a LOW priority task (logging, metrics)."""
        return await self.submit(coro, TaskPriority.LOW, task_id, timeout)
    
    async def submit_background(self, coro: Callable, task_id: Optional[str] = None) -> str:
        """Submit a BACKGROUND priority task (cleanup, archival)."""
        return await self.submit(coro, TaskPriority.BACKGROUND, task_id, timeout=None)
    
    async def _worker(self, worker_id: int):
        """Worker that processes tasks from the queue."""
        logger.info(f"🔧 Worker {worker_id} started")
        
        while self._running:
            try:
                # Get task from queue (blocks until available)
                task = await self.queue.get()
                
                # Acquire semaphore (limits concurrent execution)
                async with self.semaphore:
                    await self._execute_task(task)
                
                # Mark task as done
                self.queue.task_done()
                
            except asyncio.CancelledError:
                logger.info(f"🛑 Worker {worker_id} cancelled")
                break
            except Exception as e:
                logger.error(f"❌ Worker {worker_id} error: {e}")
    
    async def _execute_task(self, task: PriorityTask):
        """Execute a single task with timeout and retry logic."""
        task_id = task.task_id
        
        try:
            logger.debug(f"▶️  Executing task: {task_id} (priority={task.priority})")
            
            # Track active task
            async_task = asyncio.create_task(task.coro())
            self.active_tasks[task_id] = async_task
            
            # Execute with timeout
            if task.timeout:
                result = await asyncio.wait_for(async_task, timeout=task.timeout)
            else:
                result = await async_task
            
            # Success
            self.tasks_completed += 1
            logger.debug(f"✅ Task completed: {task_id}")
            
        except asyncio.TimeoutError:
            logger.warning(f"⏱️  Task timeout: {task_id} (timeout={task.timeout}s)")
            self.tasks_timeout += 1
            
            # Retry logic
            if task.retry_count < task.max_retries:
                task.retry_count += 1
                backoff = 2 ** task.retry_count  # Exponential backoff
                logger.info(f"🔄 Retrying task {task_id} in {backoff}s (attempt {task.retry_count}/{task.max_retries})")
                await asyncio.sleep(backoff)
                await self.queue.put(task)
            else:
                logger.error(f"❌ Task failed after {task.max_retries} retries: {task_id}")
                self.tasks_failed += 1
        
        except asyncio.CancelledError:
            logger.info(f"🚫 Task cancelled: {task_id}")
            self.tasks_cancelled += 1
        
        except Exception as e:
            logger.error(f"❌ Task error: {task_id} - {e}")
            self.tasks_failed += 1
            
            # Retry logic for transient errors
            if task.retry_count < task.max_retries:
                task.retry_count += 1
                backoff = 2 ** task.retry_count
                logger.info(f"🔄 Retrying task {task_id} in {backoff}s (attempt {task.retry_count}/{task.max_retries})")
                await asyncio.sleep(backoff)
                await self.queue.put(task)
        
        finally:
            # Remove from active tasks
            self.active_tasks.pop(task_id, None)
    
    async def start(self, num_workers: int = 5):
        """
        Start worker threads to process tasks.
        
        Args:
            num_workers: Number of worker threads
        """
        if self._running:
            logger.warning("⚠️  Task queue already running")
            return
        
        self._running = True
        logger.info(f"🚀 Starting {num_workers} workers...")
        
        # Create workers
        for i in range(num_workers):
            worker = asyncio.create_task(self._worker(i))
            self._workers.append(worker)
        
        logger.info(f"✅ Task queue started with {num_workers} workers")
    
    async def stop(self, timeout: float = 30.0):
        """
        Stop all workers gracefully.
        
        Args:
            timeout: Maximum time to wait for tasks to complete
        """
        if not self._running:
            return
        
        logger.info("🛑 Stopping task queue...")
        self._running = False
        
        # Wait for queue to empty or timeout
        try:
            await asyncio.wait_for(self.queue.join(), timeout=timeout)
            logger.info("✅ All queued tasks completed")
        except asyncio.TimeoutError:
            logger.warning(f"⏱️  Timeout waiting for tasks (remaining: {self.queue.qsize()})")
        
        # Cancel all workers
        for worker in self._workers:
            worker.cancel()
        
        # Wait for workers to finish
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        
        # Cancel any remaining active tasks
        for task_id, task in list(self.active_tasks.items()):
            logger.warning(f"🚫 Cancelling active task: {task_id}")
            task.cancel()
        
        logger.info("✅ Task queue stopped")
    
    def get_stats(self) -> Dict:
        """Get queue statistics."""
        return {
            "queue_size": self.queue.qsize(),
            "active_tasks": len(self.active_tasks),
            "max_concurrent": self.max_concurrent,
            "workers": len(self._workers),
            "tasks_submitted": self.tasks_submitted,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
            "tasks_timeout": self.tasks_timeout,
            "tasks_cancelled": self.tasks_cancelled,
            "success_rate": f"{(self.tasks_completed / self.tasks_submitted * 100) if self.tasks_submitted > 0 else 0:.1f}%"
        }
    
    def print_stats(self):
        """Print queue statistics."""
        stats = self.get_stats()
        logger.info("=" * 60)
        logger.info("📊 TASK QUEUE STATISTICS")
        logger.info("=" * 60)
        logger.info(f"  Queue Size:      {stats['queue_size']}")
        logger.info(f"  Active Tasks:    {stats['active_tasks']}")
        logger.info(f"  Max Concurrent:  {stats['max_concurrent']}")
        logger.info(f"  Workers:         {stats['workers']}")
        logger.info(f"  Submitted:       {stats['tasks_submitted']}")
        logger.info(f"  Completed:       {stats['tasks_completed']}")
        logger.info(f"  Failed:          {stats['tasks_failed']}")
        logger.info(f"  Timeout:         {stats['tasks_timeout']}")
        logger.info(f"  Cancelled:       {stats['tasks_cancelled']}")
        logger.info(f"  Success Rate:    {stats['success_rate']}")
        logger.info("=" * 60)


# Global task queue instance
_task_queue: Optional[PriorityTaskQueue] = None


def get_task_queue(max_concurrent: int = 10) -> PriorityTaskQueue:
    """Get or create global task queue instance."""
    global _task_queue
    if _task_queue is None:
        _task_queue = PriorityTaskQueue(max_concurrent=max_concurrent)
    return _task_queue


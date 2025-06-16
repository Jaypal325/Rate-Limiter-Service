
from pydantic import BaseModel, Field
from typing import Dict, Optional, List
import time
import threading
import asyncio
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class RateLimitRequest:
    request_id: str
    tenant_id: str
    client_id: str
    action_type: str
    max_requests: int
    window_duration_seconds: int
    timestamp: float
    future: asyncio.Future


class CheckAndConsumeRequest(BaseModel):
    tenant_id: str = Field(..., description="Unique identifier for the tenant.")
    client_id: str = Field(..., description="Unique identifier for the client within the tenant.")
    action_type: str = Field(..., description="Type of action being rate-limited (e.g., 'login', 'api_call').")
    max_requests: int = Field(..., gt=0, description="Maximum number of requests allowed within the window.")
    window_duration_seconds: int = Field(..., gt=0, description="Duration of the rate limit window in seconds.")


class RateLimitResponse(BaseModel):
    allowed: bool
    remaining_requests: int
    reset_time_seconds: Optional[int] = None
    status: str


class RateLimitStatus(BaseModel):
    tenant_id: str
    client_id: str
    action_type: str
    current_count: int
    max_requests: int
    window_duration_seconds: int
    recorded_timestamps: List[float]
    queue_length: int
    next_reset_time: Optional[int] = None


class TenantLoadManager:

    def __init__(self, max_global_concurrent: int = 1000, max_queue_size_per_tenant: int = 100):
        self.max_global_concurrent = max_global_concurrent
        self.max_queue_size_per_tenant = max_queue_size_per_tenant
        self.current_global_load = 0
        self.tenant_queues: Dict[str, deque] = defaultdict(deque)
        self.tenant_concurrent_count: Dict[str, int] = defaultdict(int)
        self.load_lock = threading.RLock()
        self.processing_queues = False
        self._shutdown_event = threading.Event()

    def can_process_immediately(self, tenant_id: str) -> bool:
        with self.load_lock:
            return self.current_global_load < self.max_global_concurrent

    def queue_request(self, request: RateLimitRequest) -> bool:
        with self.load_lock:
            tenant_queue = self.tenant_queues[request.tenant_id]
            if len(tenant_queue) >= self.max_queue_size_per_tenant:
                logger.warning(f"Tenant queue full for tenant_id: {request.tenant_id}. Request rejected.")
                return False

            tenant_queue.append(request)
            logger.info(f"Request {request.request_id} queued for tenant {request.tenant_id}. Queue length: {len(tenant_queue)}")
            return True

    def increment_load(self, tenant_id: str):
        with self.load_lock:
            self.current_global_load += 1
            self.tenant_concurrent_count[tenant_id] += 1
            logger.debug(f"Incremented load for tenant {tenant_id}. Global: {self.current_global_load}, Tenant: {self.tenant_concurrent_count[tenant_id]}")

    def decrement_load(self, tenant_id: str):
        with self.load_lock:
            self.current_global_load = max(0, self.current_global_load - 1)
            self.tenant_concurrent_count[tenant_id] = max(0, self.tenant_concurrent_count[tenant_id] - 1)
            logger.debug(f"Decremented load for tenant {tenant_id}. Global: {self.current_global_load}, Tenant: {self.tenant_concurrent_count[tenant_id]}")

    def get_queue_length(self, tenant_id: str) -> int:
        with self.load_lock:
            return len(self.tenant_queues[tenant_id])

    def process_queued_requests(self, rate_limiter_instance):
        if self.processing_queues:
            return

        self.processing_queues = True
        logger.info("Background queue processing started.")

        try:
            while not self._shutdown_event.is_set():
                processed_any_in_cycle = False

                with self.load_lock:
                    tenant_ids = list(self.tenant_queues.keys())

                for tenant_id in tenant_ids:
                    if self._shutdown_event.is_set():
                        break

                    request_to_process = None
                    with self.load_lock:
                        if (self.current_global_load < self.max_global_concurrent and
                                self.tenant_queues[tenant_id]):
                            request_to_process = self.tenant_queues[tenant_id].popleft()
                            self.increment_load(tenant_id)
                            logger.debug(f"Pulled request {request_to_process.request_id} from queue for tenant {tenant_id}.")

                    if request_to_process:
                        try:
                            result = rate_limiter_instance._check_and_consume_internal(
                                request_to_process.tenant_id,
                                request_to_process.client_id,
                                request_to_process.action_type,
                                request_to_process.max_requests,
                                request_to_process.window_duration_seconds
                            )

                            response = RateLimitResponse(
                                allowed=result['allowed'],
                                remaining_requests=result['remaining_requests'],
                                reset_time_seconds=result.get('reset_time_seconds'),
                                status="processed"
                            )

                            if not request_to_process.future.done():
                                request_to_process.future.set_result(response)
                                logger.info(f"Processed queued request {request_to_process.request_id} for tenant {tenant_id}.")

                        except Exception as e:
                            logger.error(f"Error processing queued request {request_to_process.request_id}: {e}", exc_info=True)
                            if not request_to_process.future.done():
                                request_to_process.future.set_exception(e)

                        finally:
                            self.decrement_load(tenant_id)
                        processed_any_in_cycle = True

                if not processed_any_in_cycle:
                    time.sleep(0.01)

        except Exception as e:
            logger.critical(f"Critical error in background queue processing thread: {e}", exc_info=True)
        finally:
            self.processing_queues = False
            logger.info("Background queue processing stopped.")

    def start_background_processing(self, rate_limiter_instance) -> threading.Thread:
        logger.info("Initiating background processing thread...")
        thread = threading.Thread(target=self.process_queued_requests, args=(rate_limiter_instance,), daemon=True)
        thread.start()
        return thread

    def shutdown(self):
        logger.info("Signaling background processor shutdown.")
        self._shutdown_event.set()


class SlidingWindowRateLimiter:

    def __init__(self, load_manager_instance: TenantLoadManager):
        self.request_logs: Dict[str, Dict[str, Dict[str, List[float]]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(list))
        )

        self.locks: Dict[str, threading.RLock] = defaultdict(threading.RLock)
        self.load_manager = load_manager_instance  # Reference to the load manager

    def _get_lock(self, tenant_id: str, client_id: str, action_type: str) -> threading.RLock:
        key = f"{tenant_id}:{client_id}:{action_type}"
        if key not in self.locks:
            self.locks[key] = threading.RLock()
        return self.locks[key]

    def _cleanup_old_requests(self, timestamps: List[float], window_start: float):
        while timestamps and timestamps[0] < window_start:
            timestamps.pop(0)

    def _check_and_consume_internal(self, tenant_id: str, client_id: str, action_type: str,
                                    max_requests: int, window_duration_seconds: int) -> Dict:

        current_time = time.time()
        window_start = current_time - window_duration_seconds

        lock = self._get_lock(tenant_id, client_id, action_type)

        with lock:
            timestamps = self.request_logs[tenant_id][client_id][action_type]

            self._cleanup_old_requests(timestamps, window_start)

            current_count = len(timestamps)

            if current_count >= max_requests:
                next_reset_time = int(timestamps[0] + window_duration_seconds) if timestamps else int(current_time)
                logger.debug(f"Rate limit exceeded for {tenant_id}:{client_id}:{action_type}. Reset at {next_reset_time}")
                return {
                    'allowed': False,
                    'remaining_requests': 0,
                    'reset_time_seconds': next_reset_time
                }

            timestamps.append(current_time)
            remaining = max_requests - (current_count + 1)

            next_reset_time = int(current_time + window_duration_seconds)
            logger.debug(f"Request allowed for {tenant_id}:{client_id}:{action_type}. Remaining: {remaining}")
            return {
                'allowed': True,
                'remaining_requests': remaining,
                'reset_time_seconds': next_reset_time
            }

    async def check_and_consume(self, tenant_id: str, client_id: str, action_type: str,
                                max_requests: int, window_duration_seconds: int) -> RateLimitResponse:

        if self.load_manager.can_process_immediately(tenant_id):
            self.load_manager.increment_load(tenant_id)
            try:
                result = self._check_and_consume_internal(
                    tenant_id, client_id, action_type, max_requests, window_duration_seconds
                )
                logger.debug(f"Request {tenant_id}:{client_id}:{action_type} processed immediately. Allowed: {result['allowed']}")
                return RateLimitResponse(
                    allowed=result['allowed'],
                    remaining_requests=result['remaining_requests'],
                    reset_time_seconds=result.get('reset_time_seconds'),
                    status="processed"
                )
            finally:
                self.load_manager.decrement_load(tenant_id)
        else:
            request_id = str(uuid.uuid4())
            future = asyncio.Future()

            queued_request = RateLimitRequest(
                request_id=request_id,
                tenant_id=tenant_id,
                client_id=client_id,
                action_type=action_type,
                max_requests=max_requests,
                window_duration_seconds=window_duration_seconds,
                timestamp=time.time(),
                future=future
            )

            if self.load_manager.queue_request(queued_request):
                try:
                    logger.info(f"Request {request_id} for tenant {tenant_id} is queued. Waiting for processing.")
                    return await asyncio.wait_for(future, timeout=30.0)
                except asyncio.TimeoutError:
                    logger.warning(f"Queued request {request_id} for tenant {tenant_id} timed out before processing.")
                    return RateLimitResponse(
                        allowed=False,
                        remaining_requests=0,
                        status="rejected"
                    )
            else:
                logger.warning(f"Request {request_id} for tenant {tenant_id} rejected due to full queue.")
                return RateLimitResponse(
                    allowed=False,
                    remaining_requests=0,
                    status="rejected"
                )

    def get_status(self, tenant_id: str, client_id: str, action_type: str) -> Optional[RateLimitStatus]:
        lock = self._get_lock(tenant_id, client_id, action_type)

        with lock:
            if (tenant_id not in self.request_logs or
                    client_id not in self.request_logs[tenant_id] or
                    action_type not in self.request_logs[tenant_id][client_id]):
                return None

            timestamps = self.request_logs[tenant_id][client_id][action_type].copy()
            queue_length = self.load_manager.get_queue_length(tenant_id)

            next_reset_time = None

            if timestamps:
                next_reset_time = int(timestamps[0] + 60)

            return RateLimitStatus(
                tenant_id=tenant_id,
                client_id=client_id,
                action_type=action_type,
                current_count=len(timestamps),
                max_requests=0,
                window_duration_seconds=0,
                recorded_timestamps=timestamps,
                queue_length=queue_length,
                next_reset_time=next_reset_time
            )

load_manager = TenantLoadManager(max_global_concurrent=100, max_queue_size_per_tenant=50)
rate_limiter = SlidingWindowRateLimiter(load_manager)

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
import time
import logging

from models import (
    CheckAndConsumeRequest,
    RateLimitResponse,
    RateLimitStatus,
    load_manager,
    rate_limiter
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/check_and_consume", response_model=RateLimitResponse, summary="Check and consume a rate limit token")
async def check_and_consume_endpoint(request: CheckAndConsumeRequest):
    try:
        if not request.tenant_id or not request.client_id or not request.action_type:
            raise HTTPException(status_code=400, detail="tenant_id, client_id, and action_type are required")

        result = await rate_limiter.check_and_consume(
            tenant_id=request.tenant_id,
            client_id=request.client_id,
            action_type=request.action_type,
            max_requests=request.max_requests,
            window_duration_seconds=request.window_duration_seconds
        )
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in check_and_consume endpoint for tenant {request.tenant_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error occurred while processing rate limit request.")


@router.get("/status/{tenant_id}/{client_id}/{action_type}", response_model=RateLimitStatus, summary="Get current rate limit status for debugging")
async def get_status_endpoint(tenant_id: str, client_id: str, action_type: str):
    try:
        status = rate_limiter.get_status(tenant_id, client_id, action_type)

        if status is None:
            raise HTTPException(status_code=404, detail=f"Rate limit status not found for {tenant_id}/{client_id}/{action_type}")

        return status

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_status endpoint for {tenant_id}/{client_id}/{action_type}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error occurred while retrieving status.")


@router.get("/health", summary="Service health check")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": time.time(),
        "global_load": load_manager.current_global_load,
        "max_global_concurrent": load_manager.max_global_concurrent
    }


@router.get("/metrics", summary="Get basic operational metrics")
async def get_metrics():
    with load_manager.load_lock:
        total_queued = sum(len(queue) for queue in load_manager.tenant_queues.values())
        tenant_loads = dict(load_manager.tenant_concurrent_count)

    return {
        "global_concurrent_requests": load_manager.current_global_load,
        "max_global_concurrent": load_manager.max_global_concurrent,
        "total_queued_requests": total_queued,
        "tenant_concurrent_counts": tenant_loads,
        "active_tenants": len([t for t, c in tenant_loads.items() if c > 0]),
        "timestamp": time.time()
    }
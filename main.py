from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import time
import logging
from contextlib import asynccontextmanager
from models import load_manager, rate_limiter
import routers


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

background_processor_thread = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global background_processor_thread

    logger.info("Starting rate limiter service...")
    background_processor_thread = load_manager.start_background_processing(rate_limiter)

    yield

    logger.info("Shutting down rate limiter service...")

    load_manager.shutdown()

    if background_processor_thread and background_processor_thread.is_alive():
        background_processor_thread.join(timeout=5.0)
    logger.info("Rate limiter service shut down complete.")


app = FastAPI(
    title="Distributed Rate Limiter Service",
    description="Multi-tenant rate limiter with sliding window log algorithm and load management",
    version="1.0.0",
    lifespan=lifespan
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):

    logger.error(f"Unhandled exception for request: {request.url}. Exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": str(exc)}
    )

app.include_router(routers.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
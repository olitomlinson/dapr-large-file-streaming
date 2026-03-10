"""
Large File Streaming Service - Tests Dapr's ability to handle large binary payloads via service invocation
"""
import logging
import os
import time
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Large File Streaming Service")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dapr sidecar HTTP endpoint (from environment or default)
DAPR_HTTP_ENDPOINT = os.getenv("DAPR_HTTP_ENDPOINT", "http://localhost:3500")


class ChunkedTransferRequest(BaseModel):
    size_mb: int = 100
    chunk_size: int = 1048576  # 1MB default


@app.get("/")
async def root():
    return {
        "service": "large-file-streaming",
        "description": "Tests Dapr service invocation with large binary payloads using HTTP chunked transfer encoding",
        "endpoints": {
            "/test-chunked-transfer": "POST - Test chunked transfer encoding with large binary payload via Dapr",
            "/health": "GET - Health check endpoint"
        },
        "dapr_endpoint": DAPR_HTTP_ENDPOINT
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/test-chunked-transfer")
async def test_chunked_transfer(params: ChunkedTransferRequest):
    """
    Tests HTTP chunked transfer encoding by sending a large binary payload to the chunk-receiver
    service via Dapr service invocation. Generates binary data in chunks without buffering the
    entire payload in memory.

    Verifies whether Dapr properly handles chunked transfer encoding for large payloads.
    """
    start_time = time.time()
    size_mb = params.size_mb
    chunk_size = params.chunk_size

    total_bytes = size_mb * 1024 * 1024
    num_chunks = total_bytes // chunk_size
    remaining = total_bytes % chunk_size

    logger.info(
        f"Starting chunked transfer test: {size_mb}MB payload, "
        f"{chunk_size} byte chunks, {num_chunks} full chunks + {remaining} bytes"
    )

    # Generator to produce binary chunks without accumulating in memory
    async def generate_chunks():
        """Generate random binary data in chunks."""
        for i in range(num_chunks):
            chunk = os.urandom(chunk_size)
            yield chunk
            if (i + 1) % 10 == 0:
                logger.info(f"Generated chunk {i + 1}/{num_chunks}")

        # Send remaining bytes if any
        if remaining > 0:
            yield os.urandom(remaining)
            logger.info(f"Generated final chunk ({remaining} bytes)")

    try:
        # Construct Dapr service invocation URL
        dapr_url = f"{DAPR_HTTP_ENDPOINT}/v1.0/invoke/chunk-receiver/method/receive-chunks"

        logger.info(f"Invoking chunk-receiver via Dapr: {dapr_url}")

        # Use httpx to stream the payload to Dapr
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                dapr_url,
                content=generate_chunks(),
                headers={
                    "Content-Type": "application/octet-stream",
                }
            )

            duration = time.time() - start_time

            logger.info(
                f"Response from chunk-receiver: status={response.status_code}, "
                f"transfer-encoding={response.headers.get('transfer-encoding', 'not set')}"
            )

            receiver_response = response.json()

            result = {
                "test": "chunked-transfer-via-dapr",
                "bytes_sent": total_bytes,
                "chunks_sent": num_chunks + (1 if remaining > 0 else 0),
                "chunk_size": chunk_size,
                "duration_seconds": round(duration, 3),
                "throughput_mbps": round((total_bytes / (1024 * 1024)) / duration, 2),
                "dapr_endpoint": dapr_url,
                "receiver_response": receiver_response,
                "success": response.status_code == 200
            }

            logger.info(f"Test complete: {result}")

            return JSONResponse(content=result, status_code=200)

    except httpx.TimeoutException as e:
        duration = time.time() - start_time
        logger.error(f"Timeout during chunked transfer: {e}")
        return JSONResponse(
            content={
                "error": "Request timeout",
                "duration_seconds": round(duration, 3),
                "bytes_sent": total_bytes,
                "test": "chunked-transfer-via-dapr"
            },
            status_code=504
        )
    except httpx.RequestError as e:
        duration = time.time() - start_time
        logger.error(f"Request error during chunked transfer: {e}")
        return JSONResponse(
            content={
                "error": f"Connection error: {str(e)}",
                "duration_seconds": round(duration, 3),
                "test": "chunked-transfer-via-dapr"
            },
            status_code=502
        )
    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"Unexpected error during chunked transfer: {e}")
        return JSONResponse(
            content={
                "error": str(e),
                "duration_seconds": round(duration, 3),
                "test": "chunked-transfer-via-dapr"
            },
            status_code=500
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)

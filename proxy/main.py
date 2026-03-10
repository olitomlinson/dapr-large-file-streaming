"""
SSE Proxy Service - Tests Dapr's ability to handle Server-Sent Events via service invocation
"""
import logging
import os
import time
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="SSE Proxy Service")

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

# Direct API endpoint (bypasses Dapr service invocation)
API_DIRECT_ENDPOINT = os.getenv("API_DIRECT_ENDPOINT", "http://localhost:5111")


class ChunkedTransferRequest(BaseModel):
    size_mb: int = 100
    chunk_size: int = 1048576  # 1MB default


@app.get("/")
async def root():
    return {
        "service": "sse-proxy",
        "description": "Proxies SSE requests to test Dapr's streaming capabilities",
        "endpoints": {
            "/semantic-search/stream": "POST - Proxy SSE via Dapr service invocation (demonstrates buffering issue)",
            "/semantic-search/stream-direct": "POST - Proxy SSE directly to API (bypasses Dapr, proves proxy works)",
            "/semantic-search/workflow/{workflow_id}": "GET - Retrieve workflow results via Dapr (used for long polling fallback)",
            "/test-chunked-transfer": "POST - Test chunked transfer encoding with large binary payload via Dapr"
        }
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/semantic-search/stream")
async def proxy_semantic_search_stream(request: Request):
    """
    Proxies SSE semantic search requests to the API service via Dapr service invocation.
    Tests whether Dapr can properly handle streaming responses through service-to-service calls.

    Uses httpx to make a streaming HTTP request to the Dapr sidecar's invoke endpoint.
    """
    try:
        # Parse the incoming request body
        body = await request.json()
        logger.info(f"Proxying SSE request with query: '{body.get('query')}'")

        # Construct Dapr service invocation URL
        # Format: http://localhost:3500/v1.0/invoke/<app-id>/method/<method-name>
        dapr_url = f"{DAPR_HTTP_ENDPOINT}/v1.0/invoke/api/method/semantic-search/stream"

        logger.info(f"Invoking API service via Dapr: {dapr_url}")

        # Store content-encoding header to forward to client
        content_encoding = None

        async def generate():
            nonlocal content_encoding
            try:
                # Use httpx to stream the response from Dapr
                # IMPORTANT: Include Accept header to signal streaming intent through the entire chain
                async with httpx.AsyncClient(timeout=300.0) as client:
                    async with client.stream(
                        "POST",
                        dapr_url,
                        json=body,
                        headers={
                            "Content-Type": "application/json",
                            "Accept": "text/event-stream"
                        }
                    ) as response:
                        # Capture content-encoding if present
                        content_encoding = response.headers.get('content-encoding')

                        logger.info(
                            f"Received response from Dapr: status={response.status_code}, "
                            f"content-type={response.headers.get('content-type')}, "
                            f"transfer-encoding={response.headers.get('transfer-encoding')}, "
                            f"content-encoding={content_encoding}"
                        )

                        # Stream chunks as they arrive with small chunk size to avoid buffering
                        chunk_count = 0
                        async for chunk in response.aiter_raw():
                            if chunk:
                                chunk_count += 1
                                logger.debug(f"Yielding chunk {chunk_count}: {len(chunk)} bytes")
                                yield chunk

                        logger.info(f"Stream complete. Total chunks: {chunk_count}")

            except httpx.TimeoutException as e:
                logger.error(f"Timeout during Dapr invocation: {e}")
                error_event = f'event: error\ndata: {{"message": "Request timeout"}}\n\n'
                yield error_event.encode('utf-8')
            except httpx.RequestError as e:
                logger.error(f"Request error during Dapr invocation: {e}")
                error_event = f'event: error\ndata: {{"message": "Connection error: {str(e)}"}}\n\n'
                yield error_event.encode('utf-8')
            except Exception as e:
                logger.error(f"Unexpected error during Dapr invocation: {e}")
                error_event = f'event: error\ndata: {{"message": "{str(e)}"}}\n\n'
                yield error_event.encode('utf-8')

        # Build response headers
        response_headers = {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # Disable buffering for nginx
        }

        # Forward Content-Encoding if present so client knows data is compressed
        if content_encoding:
            response_headers["Content-Encoding"] = content_encoding

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers=response_headers
        )

    except Exception as e:
        logger.error(f"Error in proxy endpoint: {e}")
        # Return error as SSE
        async def error_stream():
            error_event = f'event: error\ndata: {{"message": "{str(e)}"}}\n\n'
            yield error_event.encode('utf-8')

        return StreamingResponse(
            error_stream(),
            media_type="text/event-stream"
        )


@app.get("/semantic-search/workflow/{workflow_id}")
async def proxy_get_workflow(workflow_id: str):
    """
    Proxies workflow retrieval requests to the API service via Dapr service invocation.
    Used by the long polling fallback mechanism.
    """
    try:
        logger.info(f"Proxying workflow retrieval request for: {workflow_id}")

        # Construct Dapr service invocation URL
        dapr_url = f"{DAPR_HTTP_ENDPOINT}/v1.0/invoke/api/method/semantic-search/workflow/{workflow_id}"

        logger.info(f"Invoking API service via Dapr: {dapr_url}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(dapr_url)

            logger.info(
                f"Received response from Dapr: status={response.status_code}"
            )

            # Return the response with the same status code
            return JSONResponse(
                content=response.json(),
                status_code=response.status_code
            )

    except httpx.TimeoutException as e:
        logger.error(f"Timeout during Dapr invocation: {e}")
        return JSONResponse(
            content={"error": "Request timeout"},
            status_code=504
        )
    except httpx.RequestError as e:
        logger.error(f"Request error during Dapr invocation: {e}")
        return JSONResponse(
            content={"error": f"Connection error: {str(e)}"},
            status_code=502
        )
    except Exception as e:
        logger.error(f"Unexpected error during Dapr invocation: {e}")
        return JSONResponse(
            content={"error": str(e)},
            status_code=500
        )


@app.post("/semantic-search/stream-direct")
async def proxy_semantic_search_stream_direct(request: Request):
    """
    Proxies SSE semantic search requests DIRECTLY to the API service (bypasses Dapr).
    This proves that the proxy itself can handle SSE streaming correctly.

    Calls the .NET API directly on port 5111 instead of using Dapr service invocation.
    This should work perfectly and isolates the issue to Dapr's service invocation layer.
    """
    try:
        # Parse the incoming request body
        body = await request.json()
        logger.info(f"Proxying SSE request (direct) with query: '{body.get('query')}'")

        # Call API directly, bypassing Dapr
        api_url = f"{API_DIRECT_ENDPOINT}/semantic-search/stream"

        logger.info(f"Calling API directly (no Dapr): {api_url}")

        # Store content-encoding header to forward to client
        content_encoding = None

        async def generate():
            nonlocal content_encoding
            try:
                # Use httpx to stream the response directly from the API
                async with httpx.AsyncClient(timeout=300.0) as client:
                    async with client.stream(
                        "POST",
                        api_url,
                        json=body,
                        headers={
                            "Content-Type": "application/json",
                            "Accept": "text/event-stream"
                        }
                    ) as response:
                        # Capture content-encoding if present
                        content_encoding = response.headers.get('content-encoding')

                        logger.info(
                            f"Received response from API (direct): status={response.status_code}, "
                            f"content-type={response.headers.get('content-type')}, "
                            f"transfer-encoding={response.headers.get('transfer-encoding')}, "
                            f"content-encoding={content_encoding}"
                        )

                        # Stream chunks as they arrive with small chunk size to avoid buffering
                        chunk_count = 0
                        async for chunk in response.aiter_raw():
                            if chunk:
                                chunk_count += 1
                                logger.debug(f"Yielding chunk {chunk_count}: {len(chunk)} bytes")
                                yield chunk

                        logger.info(f"Stream complete (direct). Total chunks: {chunk_count}")

            except httpx.TimeoutException as e:
                logger.error(f"Timeout during direct API call: {e}")
                error_event = f'event: error\ndata: {{"message": "Request timeout"}}\n\n'
                yield error_event.encode('utf-8')
            except httpx.RequestError as e:
                logger.error(f"Request error during direct API call: {e}")
                error_event = f'event: error\ndata: {{"message": "Connection error: {str(e)}"}}\n\n'
                yield error_event.encode('utf-8')
            except Exception as e:
                logger.error(f"Unexpected error during direct API call: {e}")
                error_event = f'event: error\ndata: {{"message": "{str(e)}"}}\n\n'
                yield error_event.encode('utf-8')

        # Build response headers
        response_headers = {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # Disable buffering for nginx
        }

        # Forward Content-Encoding if present so client knows data is compressed
        if content_encoding:
            response_headers["Content-Encoding"] = content_encoding

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers=response_headers
        )

    except Exception as e:
        logger.error(f"Error in direct proxy endpoint: {e}")
        # Return error as SSE
        async def error_stream():
            error_event = f'event: error\ndata: {{"message": "{str(e)}"}}\n\n'
            yield error_event.encode('utf-8')

        return StreamingResponse(
            error_stream(),
            media_type="text/event-stream"
        )


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

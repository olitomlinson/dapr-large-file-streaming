"""
Large File Streaming Service - Tests Dapr's ability to handle large binary payloads via service invocation
"""
import logging
import os
import time
import json
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


class JsonTransferRequest(BaseModel):
    num_records: int = 10000
    records_per_chunk: int = 100


class NdjsonTransferRequest(BaseModel):
    num_records: int = 10000
    records_per_chunk: int = 100


@app.get("/")
async def root():
    return {
        "service": "large-file-streaming",
        "description": "Tests Dapr service invocation with large binary payloads using HTTP chunked transfer encoding",
        "endpoints": {
            "/test-chunked-transfer": "POST - Test chunked transfer encoding with large binary payload via Dapr",
            "/test-json-transfer": "POST - Test chunked transfer encoding with large JSON document via Dapr",
            "/test-ndjson-transfer": "POST - Test chunked transfer encoding with NDJSON (newline-delimited JSON) via Dapr",
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


@app.post("/test-json-transfer")
async def test_json_transfer(params: JsonTransferRequest):
    """
    Tests HTTP chunked transfer encoding by sending a large JSON document to the chunk-receiver
    service via Dapr service invocation. Generates JSON records in chunks without buffering the
    entire document in memory.

    The JSON document is an array of records, streamed incrementally.
    """
    start_time = time.time()
    num_records = params.num_records
    records_per_chunk = params.records_per_chunk

    logger.info(
        f"Starting JSON chunked transfer test: {num_records} records, "
        f"{records_per_chunk} records per chunk"
    )

    # Generator to produce JSON chunks without accumulating in memory
    async def generate_json_chunks():
        """Generate JSON array incrementally in chunks."""
        # Start the JSON array
        yield b'['

        for i in range(0, num_records, records_per_chunk):
            chunk_records = []
            end_idx = min(i + records_per_chunk, num_records)

            for j in range(i, end_idx):
                record = {
                    "id": j,
                    "timestamp": time.time(),
                    "data": {
                        "field1": f"value_{j}",
                        "field2": j * 2,
                        "field3": j % 2 == 0,
                        "field4": [j, j+1, j+2],
                        "nested": {
                            "key1": f"nested_value_{j}",
                            "key2": j * 3
                        }
                    },
                    "metadata": {
                        "source": "chunk-sender",
                        "batch": i // records_per_chunk,
                        "sequence": j
                    }
                }
                chunk_records.append(record)

            # Convert chunk to JSON and add commas appropriately
            chunk_json = json.dumps(chunk_records)[1:-1]  # Remove outer brackets
            if i > 0:
                chunk_json = "," + chunk_json

            yield chunk_json.encode('utf-8')

            if (i + records_per_chunk) % (records_per_chunk * 10) == 0:
                logger.info(f"Generated {i + records_per_chunk}/{num_records} records")

        # Close the JSON array
        yield b']'
        logger.info(f"Generated all {num_records} records")

    try:
        # Construct Dapr service invocation URL
        dapr_url = f"{DAPR_HTTP_ENDPOINT}/v1.0/invoke/chunk-receiver/method/receive-json"

        logger.info(f"Invoking chunk-receiver via Dapr: {dapr_url}")

        # Use httpx to stream the JSON payload to Dapr
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                dapr_url,
                content=generate_json_chunks(),
                headers={
                    "Content-Type": "application/json",
                }
            )

            duration = time.time() - start_time

            logger.info(
                f"Response from chunk-receiver: status={response.status_code}, "
                f"transfer-encoding={response.headers.get('transfer-encoding', 'not set')}"
            )

            receiver_response = response.json()

            result = {
                "test": "json-chunked-transfer-via-dapr",
                "records_sent": num_records,
                "records_per_chunk": records_per_chunk,
                "total_chunks": (num_records + records_per_chunk - 1) // records_per_chunk,
                "duration_seconds": round(duration, 3),
                "records_per_second": round(num_records / duration, 2),
                "dapr_endpoint": dapr_url,
                "receiver_response": receiver_response,
                "success": response.status_code == 200
            }

            logger.info(f"Test complete: {result}")

            return JSONResponse(content=result, status_code=200)

    except httpx.TimeoutException as e:
        duration = time.time() - start_time
        logger.error(f"Timeout during JSON chunked transfer: {e}")
        return JSONResponse(
            content={
                "error": "Request timeout",
                "duration_seconds": round(duration, 3),
                "records_sent": num_records,
                "test": "json-chunked-transfer-via-dapr"
            },
            status_code=504
        )
    except httpx.RequestError as e:
        duration = time.time() - start_time
        logger.error(f"Request error during JSON chunked transfer: {e}")
        return JSONResponse(
            content={
                "error": f"Connection error: {str(e)}",
                "duration_seconds": round(duration, 3),
                "test": "json-chunked-transfer-via-dapr"
            },
            status_code=502
        )
    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"Unexpected error during JSON chunked transfer: {e}")
        return JSONResponse(
            content={
                "error": str(e),
                "duration_seconds": round(duration, 3),
                "test": "json-chunked-transfer-via-dapr"
            },
            status_code=500
        )


@app.post("/test-ndjson-transfer")
async def test_ndjson_transfer(params: NdjsonTransferRequest):
    """
    Tests HTTP chunked transfer encoding by sending NDJSON (newline-delimited JSON) to the
    chunk-receiver service via Dapr service invocation. Each line is a complete JSON record,
    making it ideal for streaming large datasets without memory buffering.

    NDJSON format: one JSON object per line, no array brackets or commas needed.
    """
    start_time = time.time()
    num_records = params.num_records
    records_per_chunk = params.records_per_chunk

    logger.info(
        f"Starting NDJSON chunked transfer test: {num_records} records, "
        f"{records_per_chunk} records per chunk"
    )

    # Generator to produce NDJSON chunks without accumulating in memory
    async def generate_ndjson_chunks():
        """Generate NDJSON incrementally - one JSON object per line."""
        for i in range(0, num_records, records_per_chunk):
            lines = []
            end_idx = min(i + records_per_chunk, num_records)

            for j in range(i, end_idx):
                record = {
                    "id": j,
                    "timestamp": time.time(),
                    "data": {
                        "field1": f"value_{j}",
                        "field2": j * 2,
                        "field3": j % 2 == 0,
                        "field4": [j, j+1, j+2],
                        "nested": {
                            "key1": f"nested_value_{j}",
                            "key2": j * 3
                        }
                    },
                    "metadata": {
                        "source": "chunk-sender",
                        "batch": i // records_per_chunk,
                        "sequence": j
                    }
                }
                # Each record on its own line
                lines.append(json.dumps(record))

            # Join with newlines and yield
            chunk_text = "\n".join(lines) + "\n"
            yield chunk_text.encode('utf-8')

            if (i + records_per_chunk) % (records_per_chunk * 10) == 0:
                logger.info(f"Generated {i + records_per_chunk}/{num_records} records")

        logger.info(f"Generated all {num_records} records")

    try:
        # Construct Dapr service invocation URL
        dapr_url = f"{DAPR_HTTP_ENDPOINT}/v1.0/invoke/chunk-receiver/method/receive-ndjson"

        logger.info(f"Invoking chunk-receiver via Dapr: {dapr_url}")

        # Use httpx to stream the NDJSON payload to Dapr
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                dapr_url,
                content=generate_ndjson_chunks(),
                headers={
                    "Content-Type": "application/x-ndjson",
                }
            )

            duration = time.time() - start_time

            logger.info(
                f"Response from chunk-receiver: status={response.status_code}, "
                f"transfer-encoding={response.headers.get('transfer-encoding', 'not set')}"
            )

            receiver_response = response.json()

            result = {
                "test": "ndjson-chunked-transfer-via-dapr",
                "records_sent": num_records,
                "records_per_chunk": records_per_chunk,
                "total_chunks": (num_records + records_per_chunk - 1) // records_per_chunk,
                "duration_seconds": round(duration, 3),
                "records_per_second": round(num_records / duration, 2),
                "dapr_endpoint": dapr_url,
                "receiver_response": receiver_response,
                "success": response.status_code == 200
            }

            logger.info(f"Test complete: {result}")

            return JSONResponse(content=result, status_code=200)

    except httpx.TimeoutException as e:
        duration = time.time() - start_time
        logger.error(f"Timeout during NDJSON chunked transfer: {e}")
        return JSONResponse(
            content={
                "error": "Request timeout",
                "duration_seconds": round(duration, 3),
                "records_sent": num_records,
                "test": "ndjson-chunked-transfer-via-dapr"
            },
            status_code=504
        )
    except httpx.RequestError as e:
        duration = time.time() - start_time
        logger.error(f"Request error during NDJSON chunked transfer: {e}")
        return JSONResponse(
            content={
                "error": f"Connection error: {str(e)}",
                "duration_seconds": round(duration, 3),
                "test": "ndjson-chunked-transfer-via-dapr"
            },
            status_code=502
        )
    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"Unexpected error during NDJSON chunked transfer: {e}")
        return JSONResponse(
            content={
                "error": str(e),
                "duration_seconds": round(duration, 3),
                "test": "ndjson-chunked-transfer-via-dapr"
            },
            status_code=500
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)

"""
Chunk Receiver Service - Tests Dapr's ability to handle chunked transfer encoding
Receives large binary payloads and streams them directly to disk without buffering
"""
import logging
import time
import json
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import aiofiles

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Chunk Receiver Service")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {
        "service": "chunk-receiver",
        "description": "Receives chunked binary data and streams to disk",
        "endpoints": {
            "/receive-chunks": "POST - Receives binary data via chunked transfer encoding and writes to /tmp/received_chunks.bin",
            "/receive-json": "POST - Receives JSON data via chunked transfer encoding and writes to /tmp/received_json.json",
            "/receive-ndjson": "POST - Receives NDJSON data via chunked transfer encoding and writes to /tmp/received_ndjson.ndjson"
        }
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/receive-chunks")
async def receive_chunks(request: Request):
    """
    Receives binary data via chunked transfer encoding and streams directly to disk.
    Does NOT buffer the entire payload in memory.

    Returns summary with bytes received, chunk count, and duration.
    """
    start_time = time.time()
    file_path = "/tmp/received_chunks.bin"

    # Log request headers to see if Transfer-Encoding: chunked is present
    transfer_encoding = request.headers.get('transfer-encoding', 'not set')
    content_type = request.headers.get('content-type', 'not set')

    logger.info(f"Starting chunk reception - Transfer-Encoding: {transfer_encoding}, Content-Type: {content_type}")

    chunk_count = 0
    total_bytes = 0

    try:
        # Stream chunks directly to disk without buffering in memory
        async with aiofiles.open(file_path, 'wb') as f:
            async for chunk in request.stream():
                if chunk:
                    chunk_size = len(chunk)
                    await f.write(chunk)
                    chunk_count += 1
                    total_bytes += chunk_size

                    # Log every 10MB to show progress without flooding logs
                    if chunk_count == 1 or total_bytes % (10 * 1024 * 1024) < chunk_size:
                        logger.info(f"Progress: {total_bytes / (1024 * 1024):.2f} MB received ({chunk_count} chunks, last chunk: {chunk_size} bytes)")

        duration = time.time() - start_time

        logger.info(
            f"Reception complete - Total: {total_bytes / (1024 * 1024):.2f} MB, "
            f"Chunks: {chunk_count}, Duration: {duration:.2f}s, "
            f"Throughput: {(total_bytes / (1024 * 1024)) / duration:.2f} MB/s"
        )

        return JSONResponse(
            content={
                "bytes_received": total_bytes,
                "chunks_received": chunk_count,
                "duration_seconds": round(duration, 3),
                "file_path": file_path,
                "transfer_encoding": transfer_encoding,
                "throughput_mbps": round((total_bytes / (1024 * 1024)) / duration, 2)
            },
            status_code=200
        )

    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"Error receiving chunks after {duration:.2f}s: {e}")
        return JSONResponse(
            content={
                "error": str(e),
                "bytes_received": total_bytes,
                "chunks_received": chunk_count,
                "duration_seconds": round(duration, 3)
            },
            status_code=500
        )


@app.post("/receive-json")
async def receive_json(request: Request):
    """
    Receives JSON data via chunked transfer encoding and streams directly to disk.
    Does NOT buffer the entire payload in memory.

    Returns summary with bytes received, chunk count, records count, and duration.
    """
    start_time = time.time()
    file_path = "/tmp/received_json.json"

    # Log request headers to see if Transfer-Encoding: chunked is present
    transfer_encoding = request.headers.get('transfer-encoding', 'not set')
    content_type = request.headers.get('content-type', 'not set')

    logger.info(f"Starting JSON chunk reception - Transfer-Encoding: {transfer_encoding}, Content-Type: {content_type}")

    chunk_count = 0
    total_bytes = 0

    try:
        # Stream chunks directly to disk without buffering in memory
        async with aiofiles.open(file_path, 'wb') as f:
            async for chunk in request.stream():
                if chunk:
                    chunk_size = len(chunk)
                    await f.write(chunk)
                    chunk_count += 1
                    total_bytes += chunk_size

                    # Log every MB to show progress without flooding logs
                    if chunk_count == 1 or total_bytes % (1 * 1024 * 1024) < chunk_size:
                        logger.info(f"Progress: {total_bytes / (1024 * 1024):.2f} MB received ({chunk_count} chunks, last chunk: {chunk_size} bytes)")

        duration = time.time() - start_time

        # Parse the JSON to count records (read from disk to avoid memory spike)
        record_count = 0
        try:
            async with aiofiles.open(file_path, 'r') as f:
                json_content = await f.read()
                json_data = json.loads(json_content)
                if isinstance(json_data, list):
                    record_count = len(json_data)
        except Exception as parse_error:
            logger.warning(f"Could not parse JSON to count records: {parse_error}")

        logger.info(
            f"JSON reception complete - Total: {total_bytes / (1024 * 1024):.2f} MB, "
            f"Chunks: {chunk_count}, Records: {record_count}, Duration: {duration:.2f}s, "
            f"Throughput: {(total_bytes / (1024 * 1024)) / duration:.2f} MB/s"
        )

        return JSONResponse(
            content={
                "bytes_received": total_bytes,
                "chunks_received": chunk_count,
                "records_received": record_count,
                "duration_seconds": round(duration, 3),
                "file_path": file_path,
                "transfer_encoding": transfer_encoding,
                "throughput_mbps": round((total_bytes / (1024 * 1024)) / duration, 2)
            },
            status_code=200
        )

    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"Error receiving JSON chunks after {duration:.2f}s: {e}")
        return JSONResponse(
            content={
                "error": str(e),
                "bytes_received": total_bytes,
                "chunks_received": chunk_count,
                "duration_seconds": round(duration, 3)
            },
            status_code=500
        )


@app.post("/receive-ndjson")
async def receive_ndjson(request: Request):
    """
    Receives NDJSON (newline-delimited JSON) data via chunked transfer encoding and streams
    directly to disk. Does NOT buffer the entire payload in memory.

    Counts records by counting newlines for minimal memory usage.
    Returns summary with bytes received, chunk count, records count, and duration.
    """
    start_time = time.time()
    file_path = "/tmp/received_ndjson.ndjson"

    # Log request headers to see if Transfer-Encoding: chunked is present
    transfer_encoding = request.headers.get('transfer-encoding', 'not set')
    content_type = request.headers.get('content-type', 'not set')

    logger.info(f"Starting NDJSON chunk reception - Transfer-Encoding: {transfer_encoding}, Content-Type: {content_type}")

    chunk_count = 0
    total_bytes = 0
    record_count = 0

    try:
        # Stream chunks directly to disk without buffering in memory
        async with aiofiles.open(file_path, 'wb') as f:
            async for chunk in request.stream():
                if chunk:
                    chunk_size = len(chunk)
                    await f.write(chunk)
                    chunk_count += 1
                    total_bytes += chunk_size

                    # Count newlines to track records (much more memory efficient than parsing)
                    record_count += chunk.count(b'\n')

                    # Log every MB to show progress without flooding logs
                    if chunk_count == 1 or total_bytes % (1 * 1024 * 1024) < chunk_size:
                        logger.info(f"Progress: {total_bytes / (1024 * 1024):.2f} MB received ({chunk_count} chunks, {record_count} records, last chunk: {chunk_size} bytes)")

        duration = time.time() - start_time

        logger.info(
            f"NDJSON reception complete - Total: {total_bytes / (1024 * 1024):.2f} MB, "
            f"Chunks: {chunk_count}, Records: {record_count}, Duration: {duration:.2f}s, "
            f"Throughput: {(total_bytes / (1024 * 1024)) / duration:.2f} MB/s"
        )

        return JSONResponse(
            content={
                "bytes_received": total_bytes,
                "chunks_received": chunk_count,
                "records_received": record_count,
                "duration_seconds": round(duration, 3),
                "file_path": file_path,
                "transfer_encoding": transfer_encoding,
                "throughput_mbps": round((total_bytes / (1024 * 1024)) / duration, 2)
            },
            status_code=200
        )

    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"Error receiving NDJSON chunks after {duration:.2f}s: {e}")
        return JSONResponse(
            content={
                "error": str(e),
                "bytes_received": total_bytes,
                "chunks_received": chunk_count,
                "records_received": record_count,
                "duration_seconds": round(duration, 3)
            },
            status_code=500
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)

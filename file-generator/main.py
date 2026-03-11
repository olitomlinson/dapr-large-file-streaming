"""
File Generator Service - Generates large files for browser download
Streams files without buffering using chunked transfer encoding
"""
import logging
import os
import subprocess
from fastapi import FastAPI, Query
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="File Generator Service")

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
        "service": "file-generator",
        "description": "Generates large files for download using chunked transfer encoding",
        "endpoints": {
            "/generate-file": "GET - Generate and download a large file (query params: size_mb, filename)",
            "/health": "GET - Health check"
        }
    }

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/memory-stats")
async def memory_stats():
    """
    Get current memory usage for all containers in the download chain.
    Requires Docker socket access.
    """
    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format",
             "{{.Container}},{{.MemUsage}}",
             "dapr-large-file-streaming-nginx-1",
             "dapr-large-file-streaming-nginx-dapr-1",
             "dapr-large-file-streaming-file-generator-1",
             "dapr-large-file-streaming-file-generator-dapr-1"],
            capture_output=True,
            text=True,
            timeout=5
        )

        stats = {}
        lines = result.stdout.strip().split('\n')

        for line in lines:
            if not line:
                continue
            if 'nginx-1' in line and 'nginx-dapr' not in line:
                mem = line.split(',')[1].split('MiB')[0].strip()
                stats['nginx'] = float(mem)
            elif 'nginx-dapr-1' in line:
                mem = line.split(',')[1].split('MiB')[0].strip()
                stats['nginx_dapr'] = float(mem)
            elif 'file-generator-1' in line and 'file-generator-dapr' not in line:
                mem = line.split(',')[1].split('MiB')[0].strip()
                stats['file_generator'] = float(mem)
            elif 'file-generator-dapr-1' in line:
                mem = line.split(',')[1].split('MiB')[0].strip()
                stats['file_generator_dapr'] = float(mem)

        return JSONResponse(content={
            "success": True,
            "memory": stats,
            "total": sum(stats.values())
        })
    except Exception as e:
        logger.error(f"Error getting memory stats: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )

@app.get("/generate-file")
async def generate_file(
    size_mb: int = Query(default=100, ge=1, le=10000, description="File size in MB"),
    filename: str = Query(default="large-file.bin", description="Filename for download")
):
    """
    Generate a large binary file and stream it to the browser.
    Uses chunked transfer encoding without buffering.
    """
    chunk_size = 1048576  # 1MB chunks

    logger.info(f"Starting file generation: {size_mb}MB as '{filename}'")

    async def generate_chunks():
        """Generate binary data in chunks"""
        total_bytes = size_mb * 1024 * 1024
        num_chunks = total_bytes // chunk_size
        remaining = total_bytes % chunk_size

        for i in range(num_chunks):
            yield os.urandom(chunk_size)
            if (i + 1) % 100 == 0:
                logger.info(f"Generated {i + 1}/{num_chunks} chunks")

        if remaining > 0:
            yield os.urandom(remaining)

        logger.info(f"File generation complete: {size_mb}MB")

    return StreamingResponse(
        generate_chunks(),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-cache",
        }
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)

"""
File Generator Service - Generates large files for browser download
Streams files without buffering using chunked transfer encoding
"""
import logging
import os
from fastapi import FastAPI, Query
from fastapi.responses import StreamingResponse
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

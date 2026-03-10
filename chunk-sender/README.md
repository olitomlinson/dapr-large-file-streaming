# SSE Proxy Service

A simple FastAPI proxy service that tests Dapr's ability to handle Server-Sent Events (SSE) through service-to-service invocation.

## Purpose

This service demonstrates whether Dapr's service invocation feature can properly handle streaming HTTP responses (specifically Server-Sent Events). It acts as an intermediary between the client and the .NET API service.

## Architecture

```
Browser/Client
    ↓ HTTP POST /semantic-search/stream
SSE Proxy Service (Python + FastAPI)
    ↓ Dapr Service Invocation
SSE Proxy Dapr Sidecar
    ↓ Service-to-Service call
API Dapr Sidecar
    ↓ Forward request
API Service (.NET)
    ↓ SSE Stream Response
    ← (response flows back through the chain)
```

## Key Questions This Tests

1. **Can Dapr handle streaming responses?** - Does Dapr's service invocation support HTTP streaming, or does it buffer the entire response?
2. **Are SSE events properly forwarded?** - Do the SSE event markers (`event:`, `data:`) flow through correctly?
3. **Is the connection kept alive?** - Does Dapr maintain the long-lived HTTP connection required for SSE?

## Endpoints

### `POST /semantic-search/stream`
Proxies semantic search requests to the API service via Dapr service invocation.

**Request:**
```json
{
  "query": "How do I reset my password?",
  "documents": [
    "Steps to update your account password and recover access",
    "Installing the mobile application on your device",
    "Troubleshooting common login and authentication issues"
  ]
}
```

**Response:** Server-Sent Events stream with events (in order):
- `scheduled` - Workflow has been scheduled (includes workflow ID, query, document count)
- `started` - Workflow has actually started running (includes workflow ID)
- `status` - Workflow runtime status updates (e.g., "Running", "Completed")
- `result` - Final search results with similarity scores and metadata
- `done` - Stream completion signal
- `error` - Any errors that occur during processing

## Running the Service

The service is configured in docker-compose.yml and runs automatically with:

```bash
docker compose up --build
```

The proxy listens on port **8001** (vs API on port 5111).

## Testing

### Option 1: Browser UI (recommended)
Open [test-sse.html](../test-sse.html) in your browser. It's already configured to use the proxy service.

### Option 2: curl command
```bash
./test-sse-proxy.sh
```

Or manually:
```bash
curl -N -X POST http://localhost:8001/semantic-search/stream \
  -H "Content-Type: application/json" \
  -d '{
    "query": "How do I reset my password?",
    "documents": [
      "Steps to update your account password and recover access",
      "Installing the mobile application on your device"
    ]
  }'
```

### Option 3: Direct comparison
Test both endpoints to compare behavior:

**Direct API (no proxy):**
```bash
./test-sse.sh  # Uses port 5111
```

**Via Proxy:**
```bash
./test-sse-proxy.sh  # Uses port 8001
```

## Expected Behavior

If Dapr properly supports SSE streaming through service invocation, you should see:
- ✅ Real-time event streaming (not buffered)
- ✅ Individual SSE events arriving as they're generated
- ✅ All event types (started, status, result, done) forwarded correctly
- ✅ Proper SSE formatting maintained

## Potential Issues

If Dapr doesn't fully support streaming:
- ❌ Response might be buffered until complete (no real-time updates)
- ❌ SSE formatting might be corrupted
- ❌ Connection might timeout or close prematurely
- ❌ Only the final result arrives, intermediate status updates are lost

### Testing Note

When testing, ensure the `Accept: text/event-stream` header is included in requests to signal streaming intent to both the proxy and Dapr. This header has been added to all test scripts and the HTML test page.

**Confirmed Findings:**
Through systematic testing (see `./test-all-chains.sh`), we've confirmed that **Dapr's HTTP service invocation does NOT support streaming responses**.

Test results:
- ✅ Direct API calls work (`Transfer-Encoding: chunked`)
- ✅ Calls through single Dapr sidecar work (`Transfer-Encoding: chunked`)
- ❌ **Service-to-service invocation fails** (`Content-Length` set, connection closes)

When one Dapr sidecar invokes another via `/v1.0/invoke/...`, Dapr buffers the entire response and sets a `Content-Length` header, breaking SSE streaming. This is a limitation of Dapr's HTTP service invocation architecture, not a bug in the application code.

**Conclusion:** For SSE streaming in Dapr applications, use direct endpoints rather than service invocation. The direct SSE endpoint at `localhost:5111/semantic-search/stream` works perfectly.

## Dependencies

- FastAPI - Web framework
- httpx - HTTP client with streaming support
- uvicorn - ASGI server

## Implementation Details

The proxy uses httpx to make HTTP streaming requests directly to the Dapr sidecar's HTTP API:

```python
# Dapr service invocation endpoint format
dapr_url = "http://localhost:3500/v1.0/invoke/api/method/semantic-search/stream"

# Stream the response using httpx
# CRITICAL: Include Accept: text/event-stream header to signal streaming through entire chain
async with httpx.AsyncClient() as client:
    async with client.stream(
        "POST",
        dapr_url,
        json=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream"  # Essential for streaming!
        }
    ) as response:
        # Use small chunk size to minimize buffering
        async for chunk in response.aiter_bytes(chunk_size=1024):
            yield chunk
```

**Critical for Streaming:**
1. **Accept header**: The `Accept: text/event-stream` header must be included when calling Dapr's invoke endpoint. Without it, Dapr may buffer the response.
2. **Small chunk size**: Using `chunk_size=1024` in `aiter_bytes()` helps ensure chunks are yielded as soon as they arrive.
3. **Request chain**: The Accept header flows through: Client → Proxy → Proxy Dapr → API Dapr → API
4. **Chunked transfer encoding**: The API must use `Transfer-Encoding: chunked` instead of `Content-Length`. In .NET, this requires:
   - Removing the Content-Length header
   - Calling `DisableBuffering()` on the response body feature
   - Without this, ASP.NET sets Content-Length which causes "wrote more than declared Content-Length" errors

## Configuration

The service is configured via environment variables in docker-compose.yml:
- `DAPR_GRPC_ENDPOINT` - Dapr gRPC endpoint
- `DAPR_HTTP_ENDPOINT` - Dapr HTTP endpoint

The proxy uses Dapr service invocation to call the `api` service's `/semantic-search/stream` endpoint.

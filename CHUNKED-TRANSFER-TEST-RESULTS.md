# HTTP Chunked Transfer Encoding Test Results

## Summary

✅ **Dapr DOES support HTTP chunked transfer encoding for service invocation**

The test successfully demonstrated that Dapr can handle large binary payloads using chunked transfer encoding, streaming data through both sidecars without buffering the entire payload in memory.

## Key Findings

### 1. Default Size Limit: 4MB

Dapr has a **default maximum request body size of 4MB** (4,194,304 bytes). Any payload larger than this will fail with:

```json
{
  "errorCode": "ERR_DIRECT_INVOKE",
  "message": "failed to invoke, id: chunk-receiver, err: stream too large"
}
```

### 2. Configurable Limit

The limit can be increased using the `--max-body-size` flag:

```yaml
command:
  [
    "./daprd",
    # ... other flags ...
    "-max-body-size",
    "128Mi",
  ]
```

**Both sidecars** (sender and receiver) need this configuration for end-to-end large transfers.

### 3. True Streaming Confirmed

With proper configuration, Dapr **does NOT buffer** the entire payload:

- **Sender**: Generated 100MB in 100 chunks of 1MB each
- **Dapr processing**: Broke down into 406 chunks of ~255KB each
- **Receiver**: Streamed directly to disk without memory accumulation

**Chunk pattern observed:**
```
Chunk 1:   80,291 bytes (~78 KB)    - TCP slow start
Chunk 2:  226,909 bytes (~222 KB)   - ramp up
Chunks 5-395: ~261,248 bytes each (~255 KB) - steady state
```

This confirms that data flows incrementally through the system without buffering.

## Test Results

### Test 1: Default Configuration (4MB limit)

| Payload Size | Result | Bytes Received | Notes |
|--------------|--------|----------------|-------|
| 2 MB | ✅ Success | 2,097,152 | Full transfer |
| 4 MB | ✅ Success | 4,194,304 | Full transfer |
| 4.5 MB | ❌ Failed | 4,194,304 | Truncated at 4MB limit |
| 5 MB | ❌ Failed | 0 | Error: stream too large |
| 10 MB | ❌ Failed | 0 | Error: stream too large |

### Test 2: Increased Limit (128MB)

| Payload Size | Result | Bytes Received | Throughput | Chunks |
|--------------|--------|----------------|------------|--------|
| 100 MB | ✅ Success | 104,857,600 | 47.74 MB/s | 406 chunks |

**Performance:**
- Duration: 2.095 seconds
- Throughput: 47.74 MB/s (sender) / 48.76 MB/s (receiver)
- Transfer-Encoding: chunked ✓

## Architecture

```
Client (curl)
  ↓ POST /test-chunked-transfer
sse-proxy service (port 8001)
  ↓ Generates 100MB in 1MB chunks
sse-proxy-dapr sidecar (port 3502) [-max-body-size 128Mi]
  ↓ Dapr service invocation: /v1.0/invoke/chunk-receiver/method/receive-chunks
  ↓ Breaks into ~255KB chunks (HTTP/TCP buffers)
chunk-receiver-dapr sidecar (port 3503) [-max-body-size 128Mi]
  ↓ Forwards to app with Transfer-Encoding: chunked
chunk-receiver service (port 8002)
  ↓ Streams directly to disk (aiofiles)
/tmp/received_chunks.bin (100MB)
```

## Configuration

### Docker Compose

Add `-max-body-size` flag to both sender and receiver Dapr sidecars:

```yaml
sse-proxy-dapr:
  command:
    [
      "./daprd",
      "-app-id", "sse-proxy",
      # ... other flags ...
      "-max-body-size", "128Mi",
    ]

chunk-receiver-dapr:
  command:
    [
      "./daprd",
      "-app-id", "chunk-receiver",
      # ... other flags ...
      "-max-body-size", "128Mi",
    ]
```

### Self-Hosted Mode

```bash
dapr run --max-body-size 128Mi node app.js
```

### Kubernetes

```yaml
annotations:
  dapr.io/enabled: "true"
  dapr.io/app-id: "myapp"
  dapr.io/max-body-size: "128Mi"
```

## Implementation Details

### Sender (proxy/main.py)

```python
# Generate chunks without accumulating in memory
async def generate_chunks():
    for i in range(num_chunks):
        yield os.urandom(chunk_size)

# Stream to Dapr
async with httpx.AsyncClient(timeout=300.0) as client:
    response = await client.post(
        dapr_url,
        content=generate_chunks(),
        headers={"Content-Type": "application/octet-stream"}
    )
```

### Receiver (chunk-receiver/main.py)

```python
# Stream directly to disk without buffering
async with aiofiles.open('/tmp/received_chunks.bin', 'wb') as f:
    async for chunk in request.stream():
        await f.write(chunk)
        chunk_count += 1
        total_bytes += len(chunk)
```

## Recommendations

1. **For payloads < 4MB**: No configuration needed, chunked transfer works out of the box

2. **For payloads > 4MB**: Set `-max-body-size` on both sidecars to accommodate your largest expected payload

3. **For very large payloads (> 100MB)**: Consider alternatives:
   - Direct service-to-service calls (bypassing Dapr)
   - External storage (S3, Azure Blob) with reference passing
   - Chunking at application level with multiple requests

4. **Memory efficiency**: Both sender and receiver should use streaming patterns (generators, async iteration) to avoid loading entire payloads into memory

## Test Endpoints

- **Sender**: `POST http://localhost:8001/test-chunked-transfer`
  ```json
  {
    "size_mb": 100,
    "chunk_size": 1048576
  }
  ```

- **Receiver**: `POST http://localhost:8002/receive-chunks`
  - Accepts `application/octet-stream`
  - Writes to `/tmp/received_chunks.bin`

## References

- [Dapr Documentation: Increase Request Size](https://docs.dapr.io/operations/configuration/increase-request-size/)
- Test implementation: `/proxy/main.py` (sender) and `/chunk-receiver/main.py` (receiver)
- Test script: `/test-chunked-transfer.sh`

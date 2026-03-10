# Dapr Caller-Side Memory Buffering Analysis

## Critical Finding: Caller's Dapr Sidecar Buffers Entire Payload

⚠️ **Despite using chunked transfer encoding, the CALLER's Dapr sidecar buffers ~2-3x the payload size in memory.**

## Test Results

All tests performed with:
- Sidecars configured with `-max-body-size 128Mi`
- Application generates data in 1MB chunks
- Data streamed via `httpx.AsyncClient().post(content=generator())`
- Transfer-Encoding: chunked confirmed

### Memory Growth by Payload Size

| Payload Size | Caller Memory Increase | Receiver Memory Increase | Caller/Payload Ratio |
|--------------|------------------------|--------------------------|----------------------|
| 25 MB | +72.04 MiB | +17.3 MiB | **2.88x** |
| 50 MB | +133.15 MiB | +17.3 MiB | **2.66x** |
| 100 MB | +221.9 MiB | +1.47 MiB | **2.22x** |

### Detailed Test Data

#### Test 1: 25MB Payload
```
Baseline:  24.51 MiB
After:     96.55 MiB
Increase:  72.04 MiB (2.88x)
```

#### Test 2: 50MB Payload
```
Baseline:  27.15 MiB
After:     160.3 MiB
Increase:  133.15 MiB (2.66x)
```

#### Test 3: 100MB Payload
```
Baseline:  117.7 MiB
After:     339.6 MiB
Increase:  221.9 MiB (2.22x)
```

## Key Observations

### 1. Caller Buffers, Receiver Streams

- **Caller (sse-proxy-dapr)**: Memory increases by 2-3x payload size
- **Receiver (chunk-receiver-dapr)**: Memory increases by only ~17 MiB (negligible relative to payload)

This confirms:
- ✅ Receiver's sidecar properly streams without buffering
- ❌ Caller's sidecar buffers the entire payload in memory

### 2. Why 2-3x the Payload Size?

The memory increase is consistently **more than the raw payload size**, likely due to:
- Original payload buffer
- Retry/resiliency buffer
- HTTP framing overhead
- gRPC protobuf overhead (Dapr uses gRPC internally)
- Go runtime overhead

### 3. Memory Overhead Decreases with Scale

The ratio decreases as payload grows:
- 25 MB: 2.88x
- 50 MB: 2.66x
- 100 MB: 2.22x

This suggests fixed overhead becomes less significant relative to payload size, but the base buffering behavior remains.

## Why Does Dapr Buffer on the Caller Side?

### Hypothesis: Resiliency & Retry Support

Dapr's built-in resiliency policies (retries, circuit breakers, timeouts) require the ability to **replay the entire request** if the target service fails mid-transfer.

**From Dapr documentation:**
> Dapr provides resiliency capabilities through retries and circuit breakers. These policies apply to service invocation.

**The implication:**
- To support request retries, Dapr must buffer the entire request body
- This allows Dapr to resend the same request if the first attempt fails
- Without buffering, a streaming request cannot be replayed

## Architecture Flow

```
Application (sse-proxy)
  ↓ Generates 100MB in 1MB chunks (streaming)
  ↓
Dapr Caller Sidecar (sse-proxy-dapr)
  ↓ BUFFERS ENTIRE 100MB (~220MB in memory)
  ↓ Applies resiliency policies (retries, circuit breakers)
  ↓ Forwards via gRPC (in chunks)
  ↓
Dapr Receiver Sidecar (chunk-receiver-dapr)
  ↓ STREAMS without buffering (+1.5MB memory)
  ↓ Forwards with Transfer-Encoding: chunked
  ↓
Application (chunk-receiver)
  ↓ Streams directly to disk
  ✓ /tmp/received_chunks.bin (100MB)
```

## Implications

### 1. Memory Constraints

For large payloads:
- **25 MB** → ~70 MiB memory required on caller
- **50 MB** → ~133 MiB memory required on caller
- **100 MB** → ~220 MiB memory required on caller
- **500 MB** → ~1.1 GiB memory required on caller (estimated)

The caller's sidecar needs 2-3x the payload size in available memory.

### 2. Concurrent Requests

If handling multiple concurrent large uploads:
- 10 concurrent 100MB uploads = **2.2 GiB** memory on caller sidecar
- This can quickly exhaust container memory limits

### 3. Memory Pressure & OOM Kills

High-throughput scenarios with large payloads risk:
- Out-of-memory (OOM) kills on the caller's Dapr sidecar
- Cascading failures in the cluster
- Increased latency due to garbage collection pressure

## Recommendations

### For Payloads < 4MB
- ✅ Use Dapr service invocation
- ✅ Buffering overhead is acceptable
- ✅ Benefit from built-in resiliency

### For Payloads 4MB - 100MB
- ⚠️ Configure `-max-body-size` appropriately
- ⚠️ Ensure caller sidecar has 3x payload memory available
- ⚠️ Consider disabling retries for large payloads
- ⚠️ Monitor memory usage closely

### For Payloads > 100MB
Consider alternatives to Dapr service invocation:

#### Option 1: Direct Service-to-Service Calls
```python
# Bypass Dapr, call service directly
response = await client.post(
    "http://chunk-receiver:8002/receive-chunks",
    content=generate_chunks()
)
```
**Pros:** True streaming, no buffering
**Cons:** Lose Dapr resiliency, service discovery, mTLS

#### Option 2: External Storage with References
```python
# 1. Upload to S3/Blob Storage (streaming)
file_url = await upload_to_s3(stream)

# 2. Pass reference via Dapr (small payload)
await dapr_invoke("receiver", {"file_url": file_url})

# 3. Receiver downloads from storage (streaming)
await download_from_s3(file_url, local_path)
```
**Pros:** No memory buffering, decouples transfer from processing
**Cons:** Added complexity, external dependency, storage costs

#### Option 3: Application-Level Chunking
```python
# Split into multiple small requests
chunk_size = 4 * 1024 * 1024  # 4MB
for chunk_id, chunk in enumerate(chunks):
    await dapr_invoke("receiver", {
        "chunk_id": chunk_id,
        "data": chunk
    })
```
**Pros:** Works within Dapr limits, keeps resiliency
**Cons:** Complex state management, partial failure handling

#### Option 4: Disable Resiliency for Large Payloads

Configure Dapr resiliency to skip retries for specific endpoints:
```yaml
# resiliency.yaml
apiVersion: dapr.io/v1alpha1
kind: Resiliency
metadata:
  name: myresiliency
spec:
  policies:
    retries:
      largePayloadNoRetry:
        policy: constant
        maxRetries: 0  # Disable retries
  targets:
    apps:
      chunk-receiver:
        retry: largePayloadNoRetry
```

**Pros:** May reduce buffering if that's the root cause
**Cons:** Need to verify if this actually prevents buffering

## Testing Methodology

### Memory Monitoring During Transfer

```bash
# Monitor memory continuously
docker stats --no-stream dapr-multi-app-testing-sse-proxy-dapr-1

# Run transfer in separate terminal
curl -X POST http://localhost:8001/test-chunked-transfer \
  -H "Content-Type: application/json" \
  -d '{"size_mb": 100, "chunk_size": 1048576}'

# Observe memory spike during transfer
```

### Automated Memory Tracking

```bash
# Log memory every 200ms during 8-second window
for i in {1..40}; do
    docker stats --no-stream --format "{{.MemUsage}}" \
      dapr-multi-app-testing-sse-proxy-dapr-1
    sleep 0.2
done
```

## Conclusion

**Dapr DOES transmit data using chunked transfer encoding** (confirmed by receiver getting 406 chunks for 100MB payload), but **the caller's sidecar buffers the entire payload in memory** (~2-3x payload size), likely to support retry/resiliency capabilities.

This is a **fundamental architectural limitation** when using Dapr service invocation for large payload transfers. For truly memory-efficient streaming of large payloads, **direct service-to-service communication or external storage patterns are necessary**.

## Related Issues & Documentation

- [Dapr: Increase Request Size](https://docs.dapr.io/operations/configuration/increase-request-size/)
- [Dapr Resiliency Policies](https://docs.dapr.io/operations/resiliency/)
- Consider filing issue: "Service invocation buffers entire request body for resiliency"

## Test Files

- Memory analysis script: `/tmp/monitor_memory.sh`
- Test endpoint: `POST http://localhost:8001/test-chunked-transfer`
- Receiver implementation: `/chunk-receiver/main.py`

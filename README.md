# Dapr Large File Streaming - Complete Guide

Comprehensive analysis and test suite for HTTP chunked transfer encoding through Dapr service invocation with large binary payloads.

## Quick Summary

✅ **Dapr DOES support true streaming of large files**
✅ **Key requirement: No-retry resiliency policy (maxRetries: 0)**
✅ **Configuration: Single resiliency YAML file**
✅ **Results: 99%+ memory reduction vs default behavior**

## Configuration (Simple)

### 1. Create Resiliency Policy

**File:** `components/resiliency-no-retry.yaml`

```yaml
apiVersion: dapr.io/v1alpha1
kind: Resiliency
metadata:
  name: chunk-transfer-no-retry
spec:
  policies:
    retries:
      noRetries:
        policy: constant
        maxRetries: 0
        duration: 0s
  targets:
    apps:
      chunk-receiver:
        retry: noRetries
scopes:
- chunk-sender
```

### 2. Configure max-body-size (For payloads > 4MB)

**File:** `docker-compose.yml`

```yaml
chunk-sender-dapr:
  command:
    - "./daprd"
    - "-app-id"
    - "chunk-sender"
    - "-app-port"
    - "8001"
    - "-dapr-http-port"
    - "3500"
    - "-components-path"
    - "/components"
    - "-max-body-size"
    - "2Gi"  # Adjust based on your needs

chunk-receiver-dapr:
  command:
    - "./daprd"
    - "-app-id"
    - "chunk-receiver"
    - "-app-port"
    - "8002"
    - "-dapr-http-port"
    - "3500"
    - "-components-path"
    - "/components"
    - "-max-body-size"
    - "2Gi"
```

### 3. Restart Services

```bash
docker-compose up -d
```

## Test Results Summary

### Memory Usage by Configuration

| Payload | Default (Retries) | No-Retry Policy | Improvement |
|---------|-------------------|-----------------|-------------|
| 25 MB   | +72 MiB (2.88x)   | +12 MiB (0.49x) | 83% less |
| 50 MB   | +133 MiB (2.66x)  | +16 MiB (0.33x) | 88% less |
| 100 MB  | +222 MiB (2.22x)  | +17 MiB (0.17x) | 92% less |
| 1 GB    | +2,253 MiB (2.2x) | +21 MiB (2%)    | 99% less |

### Concurrent Transfer Performance

**10 simultaneous 100MB transfers (1GB total):**
- Success rate: 100% (10/10)
- Memory increase: 65 MiB (vs 2,200 MiB expected with retries)
- Throughput: 53.65 MB/s
- Memory per transfer: 6.5 MiB (excellent scalability)

### Single 1GB Transfer

- Success: 100%
- Duration: 20.33 seconds
- Throughput: 50.36 MB/s
- Memory increase: 20.86 MiB (2.04% of payload)
- Memory saved vs retries: 2,232 MiB (99.1% reduction)

## Key Findings

### 1. Default Behavior: Buffering for Retries

**Without no-retry policy:**
- Dapr buffers 2-3x the payload size on the CALLER sidecar
- Required to support request replay on failure
- 100MB payload = ~220 MiB memory consumption
- Does NOT scale for large files or concurrent uploads

### 2. Solution: No-Retry Resiliency Policy

**With maxRetries: 0:**
- True streaming enabled
- Memory overhead: constant ~15-20 MiB regardless of payload size
- 1GB payload = only 21 MiB memory increase
- Scales linearly for concurrent transfers

### 3. max-body-size Flag Requirement

**Critical:** The `-max-body-size` flag IS REQUIRED for payloads > 4MB, even with no-retry policy.

Default limit: 4MB (4,194,304 bytes)

**Without flag:** Transfers fail with "stream too large" error at 4MB limit

**With flag:** Any payload size supported (tested up to 1GB)

### 4. Chunked Transfer Encoding

**Confirmed working:**
- Application sends: 100 chunks of 1MB each
- Dapr processes: ~406 chunks of ~255KB each (HTTP/TCP buffers)
- Receiver gets: chunked encoding preserved
- True streaming: data flows incrementally without full buffering

## Quick Start

### Start Services

```bash
docker-compose up -d chunk-sender chunk-receiver chunk-sender-dapr chunk-receiver-dapr
```

### Run Tests

```bash
# Basic functionality test (10MB, 100MB, 50MB)
./test-chunked-transfer.sh

# Concurrent transfers (10 × 100MB)
./test-concurrent-transfers.sh

# Single 1GB transfer
python3 test_1gb_transfer.py

# Concurrent test (Python)
python3 test_concurrent.py
```

### Manual Test

```bash
# Test 100MB transfer
curl -X POST http://localhost:8001/test-chunked-transfer \
  -H "Content-Type: application/json" \
  -d '{"size_mb": 100, "chunk_size": 1048576}' | jq

# Verify received file
docker exec dapr-large-file-streaming-chunk-receiver-1 \
  ls -lh /tmp/received_chunks.bin

# Monitor memory during transfer
watch -n 1 'docker stats --no-stream dapr-large-file-streaming-chunk-sender-dapr-1'
```

## Architecture

```
Client
  ↓ POST /test-chunked-transfer
chunk-sender (FastAPI:8001)
  ↓ Generates random binary data
  ↓ Streams via httpx in chunks
chunk-sender-dapr (port 3500)
  ↓ WITH retries: buffers 2-3x payload
  ↓ NO retries: streams with ~17 MiB overhead
  ↓ Dapr service invocation
chunk-receiver-dapr (port 3500)
  ↓ Streams with minimal buffering
chunk-receiver (FastAPI:8002)
  ↓ Request.stream() → aiofiles
  ↓ Writes directly to disk
/tmp/received_chunks.bin
```

## Recommendations by Use Case

### High-Throughput Services (Memory Critical)

✅ Use no-retry resiliency policy
✅ Set `-max-body-size` to accommodate largest expected payload
✅ Implement application-level retries if needed
✅ Monitor memory usage
✅ Scale horizontally

**Best for:** File uploads, media streaming, large data transfers

### Critical Services (Reliability Critical)

❌ Keep default retry policy
✅ Set `-max-body-size` high enough
✅ Ensure adequate memory: 3x largest payload
✅ Set memory limits and alerts
✅ Consider async processing

**Best for:** Financial transactions, critical data sync

### Hybrid Approach (Recommended)

✅ Different policies per endpoint
✅ Small payloads (<10MB): enable retries
✅ Large payloads (>10MB): disable retries
✅ Circuit breakers on all endpoints

## Configuration Reference

### Resiliency Policy Options

```yaml
# No retries (streaming)
spec:
  policies:
    retries:
      noRetries:
        maxRetries: 0

# Limited retries (partial buffering)
spec:
  policies:
    retries:
      limitedRetry:
        policy: constant
        maxRetries: 2
        duration: 5s

# Default (full buffering)
# No resiliency policy file = default Dapr retries active
```

### max-body-size Values

| Payload Size | Recommended Flag |
|--------------|------------------|
| < 4 MB       | Not needed (default) |
| 4-100 MB     | `-max-body-size 128Mi` |
| 100-500 MB   | `-max-body-size 1Gi` |
| 500MB-2GB    | `-max-body-size 2Gi` |
| > 2GB        | `-max-body-size 4Gi` |

## Troubleshooting

### Error: "stream too large"

**Cause:** Payload exceeds `-max-body-size` limit (default 4MB)

**Solution:** Add/increase `-max-body-size` on BOTH sidecars

### High Memory Usage on Caller

**Cause:** Retry policy active (default behavior)

**Solution:** Create no-retry resiliency policy

### Transfer Fails at Exactly 4MB

**Cause:** Default size limit, missing `-max-body-size` flag

**Solution:** Set `-max-body-size` appropriate for your payloads

### File Size Mismatch

**Cause:** Transfer interrupted or truncated

**Check:**
```bash
docker exec dapr-large-file-streaming-chunk-receiver-1 \
  stat -c%s /tmp/received_chunks.bin
```

### Memory Stays High After Transfer

**Expected:** Go GC doesn't release immediately

**Verify streaming works:** Restart sidecar, test again, check memory increase

## Performance Metrics

### Throughput
- Local testing: 48-54 MB/s
- Single 1GB transfer: 50.36 MB/s
- 10 concurrent 100MB: 53.65 MB/s aggregate

### Memory Efficiency
- With retries: 2-3x payload size
- Without retries: ~15-20 MiB constant overhead
- Scales: 1GB = only 21 MiB increase

### Scalability
- Concurrent transfers: Linear memory scaling
- 10 concurrent = 6.5 MiB per transfer
- Connection pooling optimizes overhead

## Test Scripts

- `test-chunked-transfer.sh` - Basic functionality (10MB, 100MB, 50MB)
- `test-concurrent-transfers.sh` - 10 concurrent 100MB transfers
- `test_concurrent.py` - Python concurrent test
- `test_1gb_transfer.py` - Single 1GB transfer with monitoring

## Services

### chunk-sender (port 8001)
- Endpoint: `POST /test-chunked-transfer`
- Generates binary data with os.urandom()
- Streams via httpx AsyncClient
- FastAPI + httpx

### chunk-receiver (port 8002)
- Endpoint: `POST /receive-chunks`
- Receives chunks via Request.stream()
- Writes to disk with aiofiles
- Output: `/tmp/received_chunks.bin`

## References

- [Dapr Service Invocation](https://docs.dapr.io/developing-applications/building-blocks/service-invocation/)
- [Dapr Resiliency](https://docs.dapr.io/operations/resiliency/)
- [Increase Request Size](https://docs.dapr.io/operations/configuration/increase-request-size/)

## License

Test/research project for understanding Dapr's chunked transfer behavior.

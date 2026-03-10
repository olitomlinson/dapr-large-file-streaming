# Dapr HTTP Chunked Transfer Encoding - Complete Analysis

This directory contains a comprehensive test suite and analysis of Dapr's support for HTTP chunked transfer encoding with large binary payloads.

## Summary

✅ **Dapr DOES support HTTP chunked transfer encoding**
✅ **True streaming is possible with proper configuration**
⚠️ **Default retry policy causes 2-3x memory buffering**
✅ **Disabling retries eliminates buffering (88-95% reduction)**

## Quick Start

### 1. Start Services

```bash
docker-compose up -d chunk-receiver chunk-receiver-dapr sse-proxy sse-proxy-dapr
```

### 2. Test Chunked Transfer

```bash
# Test 100MB transfer
curl -X POST http://localhost:8001/test-chunked-transfer \
  -H "Content-Type: application/json" \
  -d '{"size_mb": 100, "chunk_size": 1048576}'
```

### 3. Monitor Memory

```bash
# Watch memory usage during transfer
watch -n 1 'docker stats --no-stream dapr-multi-app-testing-sse-proxy-dapr-1'
```

## Test Results

### Memory Usage by Configuration

| Payload | Default (Retries) | No-Retry Policy | Improvement |
|---------|-------------------|-----------------|-------------|
| 25 MB   | +72 MiB (2.88x)   | +12 MiB (0.49x) | 83% less |
| 50 MB   | +133 MiB (2.66x)  | +16 MiB (0.33x) | 88% less |
| 100 MB  | +222 MiB (2.22x)  | +17 MiB (0.17x) | 92% less |

### Key Findings

1. **Default 4MB Limit**
   - Dapr has a 4,194,304 byte (4MB) default limit
   - Configurable via `-max-body-size` flag
   - Both caller and receiver sidecars need the flag

2. **Chunked Encoding Works**
   - Transfer-Encoding: chunked preserved
   - Data flows in ~255KB chunks (HTTP/TCP buffer size)
   - Receiver gets 406 chunks for 100MB payload (sent as 100 chunks)

3. **Caller Buffers for Retries**
   - Default retry policy causes 2-3x payload buffering on caller
   - Needed to replay requests on failure
   - Applies only to caller, not receiver

4. **Solution: Disable Retries**
   - Resiliency policy with `maxRetries: 0` eliminates buffering
   - Memory overhead drops to ~15-20 MiB regardless of payload size
   - 88-95% memory reduction achieved

## Architecture

```
Client
  ↓ POST /test-chunked-transfer
sse-proxy (FastAPI)
  ↓ Generates chunks with os.urandom()
  ↓ Streams via httpx
sse-proxy-dapr
  ↓ WITH retries: buffers ~220 MiB for 100MB
  ↓ NO retries: streams with ~17 MiB overhead
  ↓ Dapr service invocation
chunk-receiver-dapr
  ↓ Streams (minimal buffering)
chunk-receiver (FastAPI)
  ↓ Request.stream() → aiofiles
/tmp/received_chunks.bin
```

## Configuration

### Enable Large Payloads

**File:** `docker-compose.yml`

Add to both `sse-proxy-dapr` and `chunk-receiver-dapr`:

```yaml
command:
  [
    "./daprd",
    # ... other flags ...
    "-max-body-size",
    "128Mi",
  ]
```

### Disable Retry Policy (For True Streaming)

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
- sse-proxy
```

Apply changes:
```bash
docker-compose restart sse-proxy-dapr chunk-receiver-dapr
```

## Services

### Sender: sse-proxy
- **Port:** 8001
- **Endpoint:** `POST /test-chunked-transfer`
- **Function:** Generates binary data and streams to receiver
- **Implementation:** FastAPI + httpx streaming

### Receiver: chunk-receiver
- **Port:** 8002
- **Endpoint:** `POST /receive-chunks`
- **Function:** Receives chunks and writes directly to disk
- **Implementation:** FastAPI + aiofiles
- **Output:** `/tmp/received_chunks.bin`

## Testing

### Run Test Script

```bash
./test-chunked-transfer.sh
```

Tests multiple payload sizes (10MB, 50MB, 100MB) and verifies:
- File size matches expected
- Transfer completes successfully
- Memory usage patterns

### Manual Testing

```bash
# Test specific size
curl -X POST http://localhost:8001/test-chunked-transfer \
  -H "Content-Type: application/json" \
  -d '{"size_mb": 50, "chunk_size": 524288}' | jq

# Verify received file
docker exec dapr-multi-app-testing-chunk-receiver-1 ls -lh /tmp/received_chunks.bin

# Check memory
docker stats --no-stream | grep -E '(sse-proxy-dapr|chunk-receiver-dapr)'
```

## Documentation

### Core Documents

1. **[CHUNKED-TRANSFER-TEST-RESULTS.md](CHUNKED-TRANSFER-TEST-RESULTS.md)**
   - Proves chunked encoding works
   - Documents 4MB limit and configuration
   - Performance metrics

2. **[MEMORY-BUFFERING-ANALYSIS.md](MEMORY-BUFFERING-ANALYSIS.md)**
   - Proves caller buffers 2-3x payload
   - Explains retry mechanism
   - Recommendations by payload size

3. **[RESILIENCY-POLICY-SOLUTION.md](RESILIENCY-POLICY-SOLUTION.md)**
   - How to disable buffering
   - 88-95% memory reduction
   - Complete implementation guide
   - Trade-offs analysis

4. **[CONCURRENT-TRANSFER-ANALYSIS.md](CONCURRENT-TRANSFER-ANALYSIS.md)**
   - 10 concurrent 100MB transfers tested
   - 97% memory reduction with no-retry policy
   - Production scalability analysis
   - Throughput: 53.65 MB/s for 1GB concurrent transfers

5. **[1GB-TRANSFER-ANALYSIS.md](1GB-TRANSFER-ANALYSIS.md)**
   - Single 1GB file transfer test
   - Only 20.86 MiB memory overhead (2.04% of payload)
   - 99.1% memory reduction vs retry policy
   - Proves constant memory footprint at scale

6. **[1GB-WITHOUT-MAX-BODY-SIZE-TEST.md](1GB-WITHOUT-MAX-BODY-SIZE-TEST.md)** ⭐ NEW
   - Tests 1GB transfer WITHOUT -max-body-size flag
   - Result: Flag IS REQUIRED for 1GB (contradicts smaller payload findings)
   - Failed at 3.8MB with default 4MB limit
   - Clarifies flag requirement for multi-GB transfers

### Implementation Files

- `chunk-receiver/main.py` - Receiver service
- `proxy/main.py` - Sender service (added endpoint)
- `components/resiliency-no-retry.yaml` - Resiliency config
- `test-chunked-transfer.sh` - Test script
- `test_concurrent.py` - Concurrent transfer test
- `test_1gb_transfer.py` - 1GB single transfer test

## Recommendations

### By Payload Size

| Size | Configuration | Notes |
|------|---------------|-------|
| < 4 MB | Default | No changes needed |
| 4-50 MB | `-max-body-size 128Mi` | Consider disabling retries |
| 50-100 MB | `-max-body-size 128Mi` + no-retry | Strongly recommended |
| > 100 MB | `-max-body-size 256Mi+` + no-retry | Essential for streaming |

### Production Guidance

**For Critical Services (Reliability > Memory)**
- Keep default retry policy
- Ensure adequate memory: 3x largest expected payload
- Monitor memory usage with alerts

**For High-Throughput Services (Memory > Reliability)**
- Disable retry policy for large payload endpoints
- Implement application-level retries if needed
- Scale horizontally instead of vertically

**Hybrid Approach (Recommended)**
- Different policies for different endpoints
- Small payloads: enable retries
- Large payloads: disable retries
- Use circuit breakers for all endpoints

## Troubleshooting

### Error: "stream too large"

**Cause:** Payload exceeds `-max-body-size` limit (default 4MB)

**Solution:** Add `-max-body-size` flag to BOTH sidecars:
```yaml
-max-body-size: "128Mi"
```

### High Memory Usage on Caller

**Cause:** Default retry policy buffers entire payload

**Solution:** Create resiliency policy with `maxRetries: 0`

### File Size Mismatch

**Cause:** Transfer interrupted or truncated at 4MB limit

**Check:**
```bash
# Expected: 104857600 for 100MB
docker exec chunk-receiver-1 stat -c%s /tmp/received_chunks.bin
```

### Memory Doesn't Drop After Transfer

**Expected:** Memory stays high after transfer (Go GC behavior)

**Verify buffering is fixed:** Restart sidecar and re-test:
```bash
docker restart dapr-multi-app-testing-sse-proxy-dapr-1
# Wait, then run test and check memory increase
```

## Performance

### Throughput

- **Local testing:** ~48 MB/s
- **Network overhead:** Minimal with streaming
- **Bottleneck:** Typically disk I/O on receiver

### Memory Efficiency

- **With retries:** 2-3x payload size
- **Without retries:** ~15-20 MiB fixed overhead
- **Receiver:** ~1.5 MiB overhead (true streaming)

## Future Work

- [ ] Test with Kubernetes deployments
- [ ] Benchmark with different chunk sizes
- [ ] Test concurrent large uploads
- [ ] Measure impact of circuit breakers
- [ ] Test with gRPC service invocation
- [ ] Profile Dapr internal memory usage

## References

- [Dapr Service Invocation](https://docs.dapr.io/developing-applications/building-blocks/service-invocation/)
- [Dapr Resiliency](https://docs.dapr.io/operations/resiliency/)
- [Increase Request Size](https://docs.dapr.io/operations/configuration/increase-request-size/)

## Contributing

To add new tests or scenarios:

1. Add endpoint to `proxy/main.py` or `chunk-receiver/main.py`
2. Update test script `test-chunked-transfer.sh`
3. Document findings in appropriate MD file
4. Update this README with new configuration

## License

This is a test/research project for understanding Dapr's chunked transfer behavior.

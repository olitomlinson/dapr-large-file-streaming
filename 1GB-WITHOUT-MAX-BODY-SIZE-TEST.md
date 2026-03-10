# 1GB Transfer Test: max-body-size Flag REQUIRED

## Test Objective

Test whether a 1GB transfer works WITHOUT the `-max-body-size` flag when using the no-retry resiliency policy, similar to what was claimed for smaller payloads in [MAX-BODY-SIZE-NOT-NEEDED.md](MAX-BODY-SIZE-NOT-NEEDED.md).

## Configuration Tested

| Parameter | Value |
|-----------|-------|
| Resiliency policy | `maxRetries: 0` (active) |
| Payload size | 1,024 MB (1 GB) |
| Chunk size | 1 MB |
| **max-body-size flag** | **REMOVED** (default 4MB limit) |

## Test Results

### Without max-body-size Flag (Default 4MB limit)

❌ **FAILED** - Transfer failed almost immediately

```
Status: 502 Bad Gateway
Bytes transferred: 3,817,472 (~3.8 MB)
Duration: 2.03 seconds
Error: Connection error
```

**File verification:**
- Received: 3,817,472 bytes
- Expected: 1,073,741,824 bytes
- **Mismatch:** Only 0.36% of data transferred

### With max-body-size Flag (2Gi limit)

✅ **SUCCESS** - Full 1GB transferred

```
Status: 200 OK
Bytes transferred: 1,073,741,824 (exactly 1GB)
Duration: 20.21 seconds
Throughput: 50.67 MB/s
```

**Memory usage:**
- Caller increase: 23.02 MiB (2.25% of payload)
- Receiver increase: 36.15 MiB (3.53% of payload)

## Key Finding

**The `-max-body-size` flag IS REQUIRED for 1GB+ transfers**, even with the no-retry policy active.

### Why the Discrepancy?

The previous document [MAX-BODY-SIZE-NOT-NEEDED.md](MAX-BODY-SIZE-NOT-NEEDED.md) claimed that:
- 10MB transfers worked without the flag ✓
- 100MB transfers worked without the flag ✓
- 5 concurrent 100MB transfers worked without the flag ✓

However, when retesting:
- 10MB transfer: **FAILED** without flag
- 100MB transfer: **FAILED** without flag
- 1GB transfer: **FAILED** without flag

**Possible explanations:**
1. **Configuration drift** - Components or resiliency policy may not have been loaded
2. **Dapr version difference** - Different Dapr runtime version may have different behavior
3. **Testing error in original doc** - Original tests may have had the flag set without realizing it
4. **Environment difference** - Container restart may have affected component loading

## Updated Conclusion

### For Production Use

**Always set `-max-body-size` for payloads > 4MB**, regardless of retry policy:

```yaml
command:
  - "./daprd"
  # ... other flags ...
  - "-max-body-size"
  - "2Gi"  # For 1GB+ payloads
```

### Recommended max-body-size Values

| Max Expected Payload | Recommended Flag Value | Notes |
|----------------------|------------------------|-------|
| < 4 MB | None (default) | No flag needed |
| 4 MB - 100 MB | `"-max-body-size", "128Mi"` | 1.5x buffer |
| 100 MB - 500 MB | `"-max-body-size", "1Gi"` | 2x buffer |
| 500 MB - 1 GB | `"-max-body-size", "2Gi"` | 2x buffer |
| 1 GB - 5 GB | `"-max-body-size", "10Gi"` | 2x buffer |

**Rule of thumb:** Set limit to 2x your largest expected payload.

## Memory Efficiency Still Excellent

While the flag is required, the memory efficiency with no-retry policy remains exceptional:

| Configuration | 1GB Transfer Memory | Result |
|---------------|---------------------|--------|
| WITH retries | ~2,253 MiB (2.2x payload) | Impractical |
| NO retries + 2Gi flag | 23 MiB (2.25% of payload) | **Excellent** |

**Key benefit:** The flag sets an upper bound, but with no-retry streaming, Dapr only uses ~2-3% of the payload size in memory.

## Technical Analysis

### Why the Flag is Checked

Even with streaming enabled:

1. **Content-Length validation** - Dapr checks the `Content-Length` header against `max-body-size` before streaming begins
2. **Safety mechanism** - Prevents accidentally accepting unbounded payloads
3. **Resource protection** - Guards against misconfigured clients

The flag is a **safety limit**, not a **buffering requirement**.

### What Actually Streams

With no-retry policy:
- ✅ Data is NOT buffered in memory (true streaming)
- ✅ Memory usage stays constant (~20-30 MiB)
- ❌ BUT: Content-Length must be < max-body-size

**Analogy:** It's like a door with a "max height" sign. Even though you won't fill the entire doorway, you still can't pass through if you exceed the height limit.

## Comparison: With vs Without Flag

### Test 1: Without Flag (Default 4MB Limit)

```
Dapr startup:
  level=info msg="The request body size parameter is: 4194304 bytes"

Transfer result:
  ❌ Failed at ~3.8 MB
  Connection dropped
  Only 0.36% transferred
```

### Test 2: With 2Gi Flag

```
Dapr startup:
  level=info msg="The request body size parameter is: 2147483648 bytes"

Transfer result:
  ✅ Success - 100% transferred
  20.21 seconds
  50.67 MB/s throughput
```

## Recommendations

### 1. Always Verify Flag is Set

```bash
# Check current limit
docker logs <dapr-sidecar> 2>&1 | grep "request body size"

# Should see:
# level=info msg="The request body size parameter is: 2147483648 bytes"
# (2147483648 = 2GB)
```

### 2. Set Appropriately in docker-compose.yml

```yaml
sse-proxy-dapr:
  command:
    [
      "./daprd",
      "-app-id", "sse-proxy",
      # ... other standard flags ...
      "-max-body-size", "2Gi",  # Required for 1GB
    ]

chunk-receiver-dapr:
  command:
    [
      "./daprd",
      "-app-id", "chunk-receiver",
      # ... other standard flags ...
      "-max-body-size", "2Gi",  # Required for 1GB
    ]
```

### 3. Set on BOTH Sidecars

**Critical:** Both caller and receiver sidecars need the flag:
- **Caller:** Checks outbound request size
- **Receiver:** Checks inbound request size

If either is missing, the transfer will fail.

### 4. Plan for Growth

Don't set exactly to your current max:
- Current max: 1GB
- Set flag to: 2GB+
- Allows room for growth without redeployment

## Updated Architecture

```
Client
  ↓ POST /test-chunked-transfer
sse-proxy (FastAPI)
  ↓ Streams 1GB in 1MB chunks
sse-proxy-dapr (max-body-size: 2Gi) ← FLAG REQUIRED
  ↓ Streams with ~23 MiB memory (no buffering)
  ↓ Dapr service invocation
chunk-receiver-dapr (max-body-size: 2Gi) ← FLAG REQUIRED
  ↓ Streams with ~36 MiB memory
chunk-receiver (FastAPI)
  ↓ Writes directly to disk
/tmp/received_chunks.bin (1GB)
```

## Lessons Learned

### 1. Flag is NOT Optional for Large Payloads

Despite claims in previous docs, empirical testing shows the flag is **always required** for payloads > 4MB.

### 2. No-Retry Still Provides Massive Benefits

Memory savings remain exceptional:
- 2,253 MiB → 23 MiB (99% reduction)
- True streaming confirmed
- Predictable memory footprint

### 3. Test Your Specific Configuration

Don't rely on documentation alone:
- Verify with actual payloads
- Check Dapr logs for limits
- Test failure scenarios

### 4. Document What Actually Works

Update documentation based on empirical results, not assumptions.

## Related Documents

- [1GB-TRANSFER-ANALYSIS.md](1GB-TRANSFER-ANALYSIS.md) - Successful 1GB transfer with flag
- [MAX-BODY-SIZE-NOT-NEEDED.md](MAX-BODY-SIZE-NOT-NEEDED.md) - Previous (contradictory) findings
- [RESILIENCY-POLICY-SOLUTION.md](RESILIENCY-POLICY-SOLUTION.md) - No-retry configuration
- [MEMORY-BUFFERING-ANALYSIS.md](MEMORY-BUFFERING-ANALYSIS.md) - Why memory stays low

## Final Recommendation

✅ **For 1GB+ transfers through Dapr:**

1. **Set `-max-body-size` to 2x payload** on BOTH sidecars
2. **Enable no-retry policy** (maxRetries: 0)
3. **Implement application-level retries** for reliability
4. **Monitor memory usage** to verify streaming
5. **Test with actual payloads** in your environment

**Result:** Efficient 1GB+ transfers with ~2-3% memory overhead.

## Test Evidence

**Without flag:**
- Failed at 3.8 MB
- Error log: Connection dropped
- Memory: Minimal (6 MiB) - never got far enough

**With 2Gi flag:**
- Success: 1,073,741,824 bytes
- Duration: 20.21 seconds
- Memory: 23 MiB caller, 36 MiB receiver
- Throughput: 50.67 MB/s

**Verdict:** `-max-body-size` flag is **REQUIRED** for 1GB transfers, but memory efficiency remains excellent with no-retry policy.

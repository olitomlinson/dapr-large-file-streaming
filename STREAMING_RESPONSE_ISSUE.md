# Implementation Plan: Browser-Based Large File Download via nginx/Dapr

## Quickstart - How to Run the Test

### Start Services
```bash
# Start all services (placement, scheduler, and new services)
docker-compose up -d frontend nginx file-generator

# Verify services are running
docker-compose ps
```

### Option 1: Manual Browser Test
1. Open http://localhost:8080 in your browser
2. Enter file size (default: 100 MB)
3. Enter filename (default: large-file.bin)
4. Click "Download File" button
5. Check your browser downloads folder for the file

### Option 2: Automated Memory Test
```bash
# Run automated test with 4-container memory monitoring
python3 test_browser_download.py

# Check the detailed memory log
cat /tmp/browser_download_memory_log.json | jq
```

### Direct API Test (Bypass Browser)
```bash
# Download directly via curl to test the API chain
curl "http://localhost/download/large-file?size_mb=10&filename=test.bin" \
  --output /tmp/test.bin

# Verify file size
ls -lh /tmp/test.bin
```

## Test Results & Known Issues

### ✅ What's Working (Streaming Confirmed)
- **nginx**: 6-10 MiB increase (6-10% overhead) ✓ Streaming
- **nginx-dapr**: 3-25 MiB increase (3-25% overhead) ✓ Streaming
- **file-generator**: 0.02 MiB increase (0.02% overhead) ✓ **Perfect streaming!**
- **Browser downloads**: Successfully triggers download with chunked transfer-encoding ✓

### ⚠️ Known Issue: file-generator-dapr Response Buffering
- **file-generator-dapr**: 297 MiB increase (297% overhead) ❌ **Buffering entire response**
- **Root Cause**: Dapr sidecar buffers the entire HTTP response from file-generator before forwarding to nginx-dapr
- **Impact**: Total chain memory increases by ~300 MiB for 100MB file
- **Why**: The no-retry resiliency policy prevents REQUEST buffering (caller → target) but does NOT prevent RESPONSE buffering (target → caller)

### Test Results Summary
```
Container           | Baseline | Peak    | Increase | % of Payload
--------------------|----------|---------|----------|-------------
nginx               | 9.01 MiB | 15.14   | 6.13 MiB | 6.13%
nginx-dapr          | 48.83    | 52.51   | 3.68 MiB | 3.68%
file-generator      | 30.62    | 30.64   | 0.02 MiB | 0.02% ✓✓✓
file-generator-dapr | 29.27    | 326.50  | 297.23   | 297.23% ❌
--------------------|----------|---------|----------|-------------
Total Chain         | 117.73   | 424.79  | 307.06   | 307.06%
```

### Comparison to Existing System
The existing chunk-sender → chunk-receiver pattern achieves 2-18% overhead because:
- chunk-receiver **receives** data and returns a small JSON response (no large response to buffer)
- file-generator **generates and returns** a large streaming response (gets buffered by its sidecar)

### Potential Solutions
1. **Accept current state**: file-generator itself streams perfectly (0.02%), only Dapr sidecar buffers
2. **Direct connection**: Configure nginx to proxy directly to file-generator:8003 (bypass Dapr)
3. **Investigate Dapr config**: Look for undocumented streaming flags or use gRPC instead of HTTP
4. **Hybrid approach**: Use Dapr for service discovery/auth, direct connection for data transfer

## Context

This change adds a browser-triggered large file download flow through nginx and Dapr service invocation. The current system has two Python services (chunk-sender and chunk-receiver) with proven streaming patterns achieving 2-18% memory overhead. We need to extend this to support browser downloads through an nginx reverse proxy that uses Dapr service invocation.

**Problem:** Users need to download large files through a browser, with the entire chain (browser → nginx → nginx-dapr → file-generator → file-generator-dapr) using chunked transfer-encoding without buffering.

**Requirements:**
- No container buffers the file fully in memory
- Track memory growth on all containers
- Reuse existing streaming patterns
- Prove true streaming with memory monitoring

## Architecture Overview

### As Implemented
```
┌─────────────────────────────────────────────────────────────┐
│ Browser                                                       │
│  ↓ (HTML/CSS/JS)           ↓ (API calls)                    │
├─────────────────────────────────────────────────────────────┤
│ frontend:8080              nginx:80 (API Gateway)            │
│ (static files)             ↓                                 │
│                            nginx-dapr:3500                    │
│                            (service invoke)                   │
│                            ↓                                  │
│                            file-generator-dapr:3500           │
│                            ⚠️ BUFFERS RESPONSE (~300%)        │
│                            ↓                                  │
│                            file-generator:8003                │
│                            ✓ Perfect streaming (0.02%)        │
└─────────────────────────────────────────────────────────────┘
```

**Ports:**
- Frontend UI: http://localhost:8080
- API Gateway: http://localhost (port 80)
- Direct file-generator: http://localhost:8003 (for debugging)

## Critical Files to Create/Modify

### New Files
1. `/nginx/nginx.conf` - API Gateway reverse proxy with streaming config
2. `/nginx/Dockerfile` - nginx API Gateway container
3. `/frontend/index.html` - Download UI
4. `/frontend/app.js` - Download trigger logic
5. `/frontend/style.css` - Basic styling
6. `/frontend/Dockerfile` - Frontend static web server container
7. `/frontend/.dockerignore` - Exclude Dockerfile from static files
8. `/file-generator/main.py` - Streaming file generation service
9. `/file-generator/Dockerfile` - Python container
10. `/file-generator/requirements.txt` - Dependencies
11. `/test_browser_download.py` - 4-container memory monitoring test

### Modified Files
1. `/docker-compose.yml` - Add frontend, nginx, nginx-dapr, file-generator, file-generator-dapr (5 new services)
2. `/components/resiliency-no-retry.yaml` - Add file-generator target and nginx scope

## Implementation Details

### 1. nginx Configuration

**File:** `/nginx/nginx.conf`

Key streaming settings:
- `proxy_buffering off;` - Disable response buffering
- `proxy_request_buffering off;` - Disable request buffering
- `proxy_http_version 1.1;` - Required for chunked encoding
- `chunked_transfer_encoding on;` - Preserve chunked encoding
- `proxy_set_header Connection "";` - Clear connection header

Rewrite pattern:
```nginx
location /download/ {
    proxy_pass http://127.0.0.1:3500/v1.0/invoke/file-generator/method/generate-file;
    # Query params preserved automatically
}
```

Static files served from `/usr/share/nginx/html`

**File:** `/nginx/Dockerfile`
```dockerfile
FROM nginx:alpine
COPY nginx.conf /etc/nginx/nginx.conf
COPY frontend/ /usr/share/nginx/html/
```

### 2. Frontend Implementation

**File:** `/frontend/index.html`
- Input for file size (MB)
- Input for filename
- Download button
- Status display area

**File:** `/frontend/app.js`
- Constructs URL: `/download/large-file?size_mb=100&filename=test.bin`
- Uses anchor tag download approach
- Triggers browser download mechanism

### 3. File Generator Service

**File:** `/file-generator/main.py`

Pattern: Reuse chunk-sender's generator approach
```python
async def generate_chunks():
    for i in range(num_chunks):
        yield os.urandom(chunk_size)

return StreamingResponse(
    generate_chunks(),
    media_type="application/octet-stream",
    headers={
        "Content-Disposition": f'attachment; filename="{filename}"',  # Triggers browser download
        "Cache-Control": "no-cache",
    }
)
```

**Important:** Browser download is triggered by `Content-Disposition: attachment`, NOT by chunked encoding. The chunked transfer-encoding is orthogonal - it controls whether the response is buffered or streamed. Both work together perfectly in modern browsers.

Endpoint: `GET /generate-file?size_mb=100&filename=large-file.bin`
Port: 8003
Dependencies: FastAPI 0.104.1, uvicorn 0.24.0

### 4. Docker Compose Updates

**File:** `/docker-compose.yml`

Add 5 new services:

**frontend:**
- Port: 8080:80
- Build: ./frontend
- Serves static HTML/CSS/JS

**nginx:**
- Port: 80:80
- Build: ./nginx (context: root)
- Depends on: nginx-dapr
- API Gateway only (no static files)

**nginx-dapr:**
- App ID: nginx
- App channel: nginx:80
- HTTP port: 3500 (internal)
- Exposed: 3504:3500 (for debugging)
- gRPC port: 50007:50001
- Max body size: 2Gi
- Resources path: /components
- Config: /dapr-config/config.yml

**file-generator:**
- Port: 8003:8003
- Build: ./file-generator
- Depends on: file-generator-dapr

**file-generator-dapr:**
- App ID: file-generator
- App channel: file-generator:8003
- HTTP port: 3500 (internal)
- Exposed: 3505:3500 (for debugging)
- gRPC port: 50008:50001
- Max body size: 2Gi
- Resources path: /components
- Config: /dapr-config/config.yml

All services on `network` bridge.

### 5. Resiliency Policy Update

**File:** `/components/resiliency-no-retry.yaml`

Add to `targets.apps`:
```yaml
file-generator:
  retry: noRetries
```

Add to `scopes`:
```yaml
- nginx
```

Critical: Without this, nginx-dapr will buffer 2-3x payload for retry capability.

### 6. Memory Monitoring Test

**File:** `/test_browser_download.py`

Pattern: Extend test_binary_transfer.py to monitor 4 containers:
- `dapr-large-file-streaming-nginx-dapr-1`
- `dapr-large-file-streaming-file-generator-dapr-1`
- `dapr-large-file-streaming-nginx-1` (optional)
- `dapr-large-file-streaming-file-generator-1`

Docker stats command updated to include all 4 containers.

Memory log structure:
```python
{
  'nginx': 10.5,
  'nginx_dapr': 28.3,
  'file_generator': 45.2,
  'file_generator_dapr': 32.1,
  'elapsed': 2.5,
  'timestamp': 1234567890.123
}
```

Test sizes: 10MB, 100MB, 1GB

Expected results:
- 100MB file: ~48 MiB total chain increase (48% overhead)
- 1GB file: ~30 MiB total chain increase (3% overhead)

## Implementation Sequence

1. **Create file-generator service**
   - Create directory: `mkdir -p file-generator`
   - Write main.py (reuse chunk-sender pattern)
   - Write Dockerfile (identical pattern to chunk-sender)
   - Write requirements.txt (fastapi, uvicorn)

2. **Create nginx configuration**
   - Create directory: `mkdir -p nginx`
   - Write nginx.conf with streaming settings
   - Create directory: `mkdir -p frontend`
   - Write index.html, app.js, style.css
   - Write nginx Dockerfile

3. **Update docker-compose.yml**
   - Add nginx service
   - Add nginx-dapr sidecar (ports 3504, 50007)
   - Add file-generator service
   - Add file-generator-dapr sidecar (ports 3505, 50008)

4. **Update resiliency policy**
   - Add file-generator to targets
   - Add nginx to scopes

5. **Create test script**
   - Copy test_binary_transfer.py to test_browser_download.py
   - Update container names for 4-container monitoring
   - Update memory stats parsing

6. **Build and test**
   - `docker-compose build nginx file-generator`
   - `docker-compose up -d`
   - Open browser to http://localhost
   - Click download button
   - Run `python3 test_browser_download.py`

## Verification Steps

**End-to-end flow:**
1. Browser loads http://localhost
2. Click "Download File" button
3. Browser receives chunked response
4. File downloads progressively
5. File size matches requested size
6. Memory monitoring shows <50 MiB increase for 100MB file

**Memory validation:**
- Run test with 100MB file
- Check all 4 containers stay under 50 MiB increase
- Run test with 1GB file
- Check all 4 containers stay under 50 MiB increase (proving streaming)

**Browser verification:**
- Open DevTools Network tab
- Check Response Headers for `Transfer-Encoding: chunked`
- Verify file downloads without waiting for completion
- Check downloaded file size and integrity

## Expected vs Actual Outcomes

### Original Expectations (Based on chunk-sender/receiver)
**Memory overhead per container (100MB file):**
- nginx: ~5 MiB
- nginx-dapr: ~15 MiB
- file-generator: ~10 MiB
- file-generator-dapr: ~18 MiB
- Total: ~48 MiB (48%)

### Actual Results (100MB file)
**Memory overhead per container:**
- nginx: **6 MiB (6%)** ✓ As expected
- nginx-dapr: **4-25 MiB (4-25%)** ✓ Better than expected
- file-generator: **0.02 MiB (0.02%)** ✓✓✓ Perfect streaming!
- file-generator-dapr: **297 MiB (297%)** ❌ Buffering entire response
- **Total: ~307 MiB (307%)**

### Why the Difference?
The chunk-sender/receiver pattern achieves lower memory because:
1. **Request path** (sender → receiver): Streaming works perfectly with no-retry policy
2. **Response path** (receiver → sender): Small JSON response (~1KB) - nothing to buffer

The file-generator pattern has different behavior:
1. **Request path** (nginx → file-generator): Small GET request - no issues
2. **Response path** (file-generator → nginx): Large streamed response (100MB) - **file-generator-dapr buffers it**

**Conclusion:** Dapr's no-retry policy prevents REQUEST buffering but does NOT prevent RESPONSE buffering. The sidecar buffers large responses before forwarding them.

## Reusable Patterns

From existing codebase:
- Generator pattern from chunk-sender/main.py:90-101
- FastAPI StreamingResponse pattern from chunk-sender
- Docker stats monitoring from test_binary_transfer.py:19-46
- Memory timeline analysis from test_binary_transfer.py:170-243
- Dapr sidecar configuration from docker-compose.yml:61-101
- Resiliency policy structure from components/resiliency-no-retry.yaml

## Risk Mitigation

**Risk:** nginx buffers response despite configuration
**Mitigation:** Test memory monitoring will detect buffering immediately

**Risk:** Browser shows "Unknown size" for chunked downloads (no Content-Length header)
**Mitigation:** This is expected behavior for streaming; browser still downloads correctly. Modern browsers handle chunked + Content-Disposition properly. Verify in DevTools that download works and file is saved correctly.

**Risk:** Dapr buffers despite no-retry policy
**Mitigation:** Resiliency policy must include file-generator target and nginx scope

**Risk:** File corruption during streaming
**Mitigation:** Verify file size matches requested size; use binary comparison if needed

## Troubleshooting

### High Memory Usage in file-generator-dapr

**Symptoms:**
- file-generator-dapr shows 200-300% memory increase
- Total chain memory increases significantly with payload size
- File-generator itself shows minimal memory increase (<1%)

**Root Cause:**
Dapr sidecar buffers the entire HTTP response from file-generator before forwarding to the caller. The no-retry resiliency policy only prevents REQUEST buffering, not RESPONSE buffering.

**Verification:**
```bash
# Run memory test
python3 test_browser_download.py

# Check file-generator vs file-generator-dapr
# file-generator: Should show ~0.02 MiB increase (streaming)
# file-generator-dapr: Will show ~297 MiB increase (buffering)
```

**Workarounds:**

1. **Accept the limitation**: The file-generator itself streams perfectly (0.02% overhead). Only the Dapr sidecar buffers. This is still valuable for:
   - Service discovery
   - Authentication/authorization
   - Observability/tracing
   - Other Dapr features

2. **Direct connection**: Bypass Dapr for large file downloads:
   ```nginx
   # In nginx.conf
   location /download/large-file {
       proxy_pass http://file-generator:8003/generate-file$is_args$args;
       # Skip Dapr service invocation for data-heavy operations
   }
   ```

3. **Hybrid approach**: Use Dapr for control plane, direct connections for data plane
   - Service registration/discovery via Dapr
   - Actual file transfer via direct HTTP connection
   - Best of both worlds

4. **Investigate gRPC**: Dapr's gRPC streaming might handle large responses better than HTTP
   - Requires implementing gRPC service
   - More complex but potentially better streaming support

### Services Not Loading Resiliency Policy

**Symptoms:**
- High memory usage despite no-retry policy configured
- Logs don't show "Loading Resiliency configuration" message

**Solution:**
```bash
# Restart Dapr sidecars to reload policy
docker-compose restart nginx-dapr file-generator-dapr

# Verify policy loaded
docker logs dapr-large-file-streaming-nginx-dapr-1 2>&1 | grep -i resiliency
# Should see: "Loading Resiliency configuration: chunk-transfer-no-retry"
```

### Frontend Not Serving Correct Content

**Symptoms:**
- Browser shows different content than expected
- Dockerfile or .dockerignore files visible in served content

**Solution:**
```bash
# Ensure .dockerignore exists
cat frontend/.dockerignore
# Should contain: Dockerfile, .dockerignore

# Rebuild frontend
docker-compose build frontend
docker-compose up -d frontend

# Hard refresh browser (Ctrl+Shift+R or Cmd+Shift+R)
```

## Recommendations

### For Production Use
1. **Use direct connections for large file downloads** - Bypass Dapr for data-heavy operations
2. **Keep Dapr for control operations** - Service discovery, auth, observability
3. **Monitor memory usage** - Set up alerts for unexpected memory growth
4. **Test with realistic file sizes** - Don't assume small-file behavior scales

### For Further Investigation
1. **Dapr gRPC streaming** - May provide better streaming support
2. **Dapr configuration options** - Check for undocumented streaming flags
3. **Community feedback** - Report findings to Dapr project for potential improvements
4. **Alternative patterns** - Consider pub/sub for large data transfers
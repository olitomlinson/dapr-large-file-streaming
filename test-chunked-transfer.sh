#!/bin/bash

# Test script for HTTP chunked transfer encoding via Dapr service invocation
# Tests whether Dapr can handle large binary payloads without buffering in memory

set -e

PROXY_URL="http://localhost:8001"
RECEIVER_URL="http://localhost:8002"
RECEIVER_FILE="/tmp/received_chunks.bin"

echo "=========================================="
echo "HTTP Chunked Transfer Encoding Test"
echo "=========================================="
echo ""

# Function to cleanup test file
cleanup() {
    echo "Cleaning up test file..."
    docker exec dapr-multi-app-testing-chunk-receiver-1 rm -f $RECEIVER_FILE || true
}

# Function to check file size
check_file() {
    local expected_size=$1
    echo ""
    echo "Checking received file..."

    if docker exec dapr-multi-app-testing-chunk-receiver-1 test -f $RECEIVER_FILE; then
        actual_size=$(docker exec dapr-multi-app-testing-chunk-receiver-1 stat -f%z $RECEIVER_FILE 2>/dev/null || \
                     docker exec dapr-multi-app-testing-chunk-receiver-1 stat -c%s $RECEIVER_FILE 2>/dev/null)
        echo "  File exists: $RECEIVER_FILE"
        echo "  Expected size: $expected_size bytes"
        echo "  Actual size: $actual_size bytes"

        if [ "$actual_size" -eq "$expected_size" ]; then
            echo "  ✓ File size matches!"
            return 0
        else
            echo "  ✗ File size mismatch!"
            return 1
        fi
    else
        echo "  ✗ File does not exist!"
        return 1
    fi
}

# Test 1: Small test (10MB) - Quick verification
echo "=== Test 1: 10MB Payload via Dapr ==="
echo "This tests basic functionality with a smaller payload"
echo ""

cleanup
echo "Sending request..."
response=$(curl -s -X POST "$PROXY_URL/test-chunked-transfer" \
    -H "Content-Type: application/json" \
    -d '{"size_mb": 10, "chunk_size": 1048576}' \
    -w "\nHTTP_STATUS:%{http_code}")

http_status=$(echo "$response" | grep "HTTP_STATUS" | cut -d: -f2)
body=$(echo "$response" | grep -v "HTTP_STATUS")

echo "Response:"
echo "$body" | jq '.'
echo ""
echo "HTTP Status: $http_status"

if [ "$http_status" = "200" ]; then
    echo "✓ Test 1 completed successfully"
    check_file 10485760  # 10 * 1024 * 1024
else
    echo "✗ Test 1 failed with status $http_status"
fi

echo ""
echo "Waiting 2 seconds before next test..."
sleep 2

# Test 2: Full test (100MB) - Complete chunked transfer test
echo ""
echo "=== Test 2: 100MB Payload via Dapr ==="
echo "This tests chunked transfer with the full 100MB payload"
echo ""
echo "NOTE: Monitor memory usage in another terminal with:"
echo "  docker stats --no-stream dapr-multi-app-testing-sse-proxy-dapr-1 dapr-multi-app-testing-chunk-receiver-dapr-1"
echo ""

cleanup
echo "Sending request..."
start_time=$(date +%s)

response=$(curl -s -X POST "$PROXY_URL/test-chunked-transfer" \
    -H "Content-Type: application/json" \
    -d '{"size_mb": 100, "chunk_size": 1048576}' \
    -w "\nHTTP_STATUS:%{http_code}")

end_time=$(date +%s)
duration=$((end_time - start_time))

http_status=$(echo "$response" | grep "HTTP_STATUS" | cut -d: -f2)
body=$(echo "$response" | grep -v "HTTP_STATUS")

echo "Response:"
echo "$body" | jq '.'
echo ""
echo "HTTP Status: $http_status"
echo "Total time: ${duration}s"

if [ "$http_status" = "200" ]; then
    echo "✓ Test 2 completed successfully"
    check_file 104857600  # 100 * 1024 * 1024
else
    echo "✗ Test 2 failed with status $http_status"
fi

echo ""
echo "Waiting 2 seconds before next test..."
sleep 2

# Test 3: Different chunk size (512KB chunks)
echo ""
echo "=== Test 3: 50MB with 512KB Chunks ==="
echo "This tests chunked transfer with smaller chunk size"
echo ""

cleanup
echo "Sending request..."

response=$(curl -s -X POST "$PROXY_URL/test-chunked-transfer" \
    -H "Content-Type: application/json" \
    -d '{"size_mb": 50, "chunk_size": 524288}' \
    -w "\nHTTP_STATUS:%{http_code}")

http_status=$(echo "$response" | grep "HTTP_STATUS" | cut -d: -f2)
body=$(echo "$response" | grep -v "HTTP_STATUS")

echo "Response:"
echo "$body" | jq '.'
echo ""
echo "HTTP Status: $http_status"

if [ "$http_status" = "200" ]; then
    echo "✓ Test 3 completed successfully"
    check_file 52428800  # 50 * 1024 * 1024
else
    echo "✗ Test 3 failed with status $http_status"
fi

echo ""
echo "=========================================="
echo "Test Summary"
echo "=========================================="
echo ""
echo "All tests completed. Check the logs for details:"
echo "  docker-compose logs chunk-receiver"
echo "  docker-compose logs sse-proxy"
echo ""
echo "To check memory usage during tests, run:"
echo "  docker stats --no-stream"
echo ""
echo "Key indicators of success:"
echo "  1. File sizes match expected values"
echo "  2. Logs show incremental chunk processing"
echo "  3. Memory usage remains stable (no 100MB spike)"
echo "  4. Transfer-Encoding: chunked preserved in headers"
echo ""

cleanup

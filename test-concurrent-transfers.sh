#!/bin/bash

# Concurrent Chunked Transfer Test
# Tests 10 simultaneous 100MB transfers to measure memory scaling

set -e

PROXY_URL="http://localhost:8001"
NUM_CONCURRENT=10
PAYLOAD_SIZE_MB=100

echo "=========================================="
echo "Concurrent Chunked Transfer Test"
echo "=========================================="
echo ""
echo "Configuration:"
echo "  Concurrent transfers: ${NUM_CONCURRENT}"
echo "  Payload size: ${PAYLOAD_SIZE_MB}MB each"
echo "  Total data: $((NUM_CONCURRENT * PAYLOAD_SIZE_MB))MB"
echo ""

# Function to monitor memory continuously
monitor_memory() {
    local duration=$1
    local output_file=$2
    echo "timestamp,caller_mb,receiver_mb" > "$output_file"

    end_time=$(($(date +%s) + duration))
    while [ $(date +%s) -lt $end_time ]; do
        timestamp=$(date +%s.%N)
        stats=$(docker stats --no-stream --format "{{.Container}},{{.MemUsage}}" \
            dapr-multi-app-testing-sse-proxy-dapr-1 \
            dapr-multi-app-testing-chunk-receiver-dapr-1 2>/dev/null || echo "error")

        if [ "$stats" != "error" ]; then
            caller=$(echo "$stats" | grep sse-proxy-dapr | cut -d',' -f2 | sed 's/MiB.*//' | xargs)
            receiver=$(echo "$stats" | grep chunk-receiver-dapr | cut -d',' -f2 | sed 's/MiB.*//' | xargs)

            echo "$timestamp,$caller,$receiver" >> "$output_file"
        fi

        sleep 0.5
    done
}

# Function to run single transfer
run_transfer() {
    local id=$1
    local start=$(date +%s.%N)

    response=$(curl -s -X POST "$PROXY_URL/test-chunked-transfer" \
        -H "Content-Type: application/json" \
        -d "{\"size_mb\": ${PAYLOAD_SIZE_MB}, \"chunk_size\": 1048576}" \
        -w "\nHTTP_STATUS:%{http_code}" 2>&1)

    local end=$(date +%s.%N)
    local duration=$(echo "$end - $start" | bc)

    local status=$(echo "$response" | grep "HTTP_STATUS" | cut -d: -f2)
    local body=$(echo "$response" | grep -v "HTTP_STATUS")

    if [ "$status" = "200" ]; then
        local bytes=$(echo "$body" | jq -r '.bytes_sent' 2>/dev/null || echo "0")
        local success=$(echo "$body" | jq -r '.success' 2>/dev/null || echo "false")
        echo "$id,$status,$bytes,$duration,$success"
    else
        echo "$id,$status,0,$duration,false"
    fi
}

# Cleanup
echo "Cleaning up any existing files..."
for i in $(seq 1 $NUM_CONCURRENT); do
    docker exec dapr-multi-app-testing-chunk-receiver-1 rm -f /tmp/received_chunks_${i}.bin 2>/dev/null || true
done
docker exec dapr-multi-app-testing-chunk-receiver-1 rm -f /tmp/received_chunks.bin 2>/dev/null || true

echo ""
echo "=== Baseline Memory Usage ==="
docker stats --no-stream --format "table {{.Container}}\t{{.MemUsage}}" \
    dapr-multi-app-testing-sse-proxy-dapr-1 \
    dapr-multi-app-testing-chunk-receiver-dapr-1

baseline_caller=$(docker stats --no-stream --format "{{.MemUsage}}" \
    dapr-multi-app-testing-sse-proxy-dapr-1 | cut -d'M' -f1 | cut -d'i' -f1)
baseline_receiver=$(docker stats --no-stream --format "{{.MemUsage}}" \
    dapr-multi-app-testing-chunk-receiver-dapr-1 | cut -d'M' -f1 | cut -d'i' -f1)

echo ""
echo "Starting memory monitor (30 second window)..."
monitor_memory 30 /tmp/concurrent_memory_log.csv &
MONITOR_PID=$!

sleep 2

echo ""
echo "=== Launching ${NUM_CONCURRENT} Concurrent Transfers ==="
echo ""

# Launch all transfers in parallel
pids=()
start_time=$(date +%s.%N)

for i in $(seq 1 $NUM_CONCURRENT); do
    run_transfer $i > /tmp/transfer_result_${i}.txt &
    pids+=($!)
    echo "Launched transfer $i (PID: ${pids[$((i-1))]})"
    sleep 0.1  # Small delay to stagger starts slightly
done

echo ""
echo "All transfers launched. Waiting for completion..."
echo ""

# Wait for all transfers to complete
for i in $(seq 1 $NUM_CONCURRENT); do
    wait ${pids[$((i-1))]} 2>/dev/null || true
done

end_time=$(date +%s.%N)
total_duration=$(echo "$end_time - $start_time" | bc)

# Wait for monitoring to complete
sleep 2
kill $MONITOR_PID 2>/dev/null || true
wait $MONITOR_PID 2>/dev/null || true

echo "=== Transfer Results ==="
echo ""
echo "ID,Status,Bytes,Duration,Success"
echo "-----------------------------------"

success_count=0
total_bytes=0

for i in $(seq 1 $NUM_CONCURRENT); do
    if [ -f /tmp/transfer_result_${i}.txt ]; then
        result=$(cat /tmp/transfer_result_${i}.txt)
        echo "$result"

        status=$(echo "$result" | cut -d',' -f2)
        bytes=$(echo "$result" | cut -d',' -f3)
        success=$(echo "$result" | cut -d',' -f5)

        if [ "$success" = "true" ]; then
            success_count=$((success_count + 1))
            total_bytes=$((total_bytes + bytes))
        fi
    fi
done

echo ""
echo "=== Summary ==="
echo "  Successful transfers: ${success_count}/${NUM_CONCURRENT}"
echo "  Total data transferred: $((total_bytes / 1024 / 1024)) MB"
echo "  Total duration: ${total_duration} seconds"
echo "  Average throughput: $(echo "scale=2; ($total_bytes / 1024 / 1024) / $total_duration" | bc) MB/s"
echo ""

# Analyze memory usage
echo "=== Memory Analysis ==="

max_caller=$(awk -F',' 'NR>1 {if ($2>max) max=$2} END {print max}' /tmp/concurrent_memory_log.csv)
max_receiver=$(awk -F',' 'NR>1 {if ($3>max) max=$3} END {print max}' /tmp/concurrent_memory_log.csv)

caller_increase=$(echo "$max_caller - $baseline_caller" | bc)
receiver_increase=$(echo "$max_receiver - $baseline_receiver" | bc)

echo ""
echo "Caller (sse-proxy-dapr):"
echo "  Baseline: ${baseline_caller} MiB"
echo "  Peak: ${max_caller} MiB"
echo "  Increase: ${caller_increase} MiB"
echo "  Per transfer: $(echo "scale=2; $caller_increase / $NUM_CONCURRENT" | bc) MiB"
echo ""
echo "Receiver (chunk-receiver-dapr):"
echo "  Baseline: ${baseline_receiver} MiB"
echo "  Peak: ${max_receiver} MiB"
echo "  Increase: ${receiver_increase} MiB"
echo "  Per transfer: $(echo "scale=2; $receiver_increase / $NUM_CONCURRENT" | bc) MiB"
echo ""

# Calculate expected vs actual
expected_with_retry=$((NUM_CONCURRENT * 220))
expected_no_retry=$((NUM_CONCURRENT * 17))

echo "Expected memory (based on single transfer tests):"
echo "  WITH retries: ~${expected_with_retry} MiB (${NUM_CONCURRENT} × 220 MiB)"
echo "  NO retries: ~${expected_no_retry} MiB (${NUM_CONCURRENT} × 17 MiB)"
echo ""
echo "Actual increase: ${caller_increase} MiB"
echo ""

if (( $(echo "$caller_increase < 200" | bc -l) )); then
    echo "✓ Memory increase consistent with NO-RETRY behavior"
    echo "  Streaming is working efficiently under concurrent load!"
else
    echo "⚠ Memory increase suggests buffering is occurring"
    echo "  May indicate retry policy is active or connection pooling limits"
fi

echo ""
echo "=== Final Memory Usage ==="
sleep 2
docker stats --no-stream --format "table {{.Container}}\t{{.MemUsage}}\t{{.CPUPerc}}" \
    dapr-multi-app-testing-sse-proxy-dapr-1 \
    dapr-multi-app-testing-chunk-receiver-dapr-1

echo ""
echo "Memory log saved to: /tmp/concurrent_memory_log.csv"
echo ""
echo "To visualize memory over time:"
echo "  cat /tmp/concurrent_memory_log.csv | column -t -s, | head -20"
echo ""

# Cleanup temp files
for i in $(seq 1 $NUM_CONCURRENT); do
    rm -f /tmp/transfer_result_${i}.txt
done

echo "=========================================="
echo "Test Complete"
echo "=========================================="

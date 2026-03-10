#!/usr/bin/env python3
"""
Concurrent Chunked Transfer Test
Tests 10 simultaneous 100MB transfers and monitors memory usage
"""

import asyncio
import httpx
import time
import subprocess
import json
from datetime import datetime

NUM_CONCURRENT = 10
PAYLOAD_SIZE_MB = 100
PROXY_URL = "http://localhost:8001/test-chunked-transfer"

def get_memory_stats():
    """Get current memory usage for Dapr sidecars"""
    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format",
             "{{.Container}},{{.MemUsage}}",
             "dapr-multi-app-testing-sse-proxy-dapr-1",
             "dapr-multi-app-testing-chunk-receiver-dapr-1"],
            capture_output=True,
            text=True,
            timeout=5
        )

        lines = result.stdout.strip().split('\n')
        stats = {}

        for line in lines:
            if 'sse-proxy-dapr' in line:
                mem = line.split(',')[1].split('MiB')[0].strip()
                stats['caller'] = float(mem)
            elif 'chunk-receiver-dapr' in line:
                mem = line.split(',')[1].split('MiB')[0].strip()
                stats['receiver'] = float(mem)

        return stats
    except Exception as e:
        print(f"Error getting memory stats: {e}")
        return {'caller': 0, 'receiver': 0}

async def monitor_memory(duration_seconds, results_list):
    """Monitor memory usage continuously"""
    start = time.time()
    while time.time() - start < duration_seconds:
        stats = get_memory_stats()
        stats['timestamp'] = time.time()
        results_list.append(stats)
        await asyncio.sleep(0.5)

async def run_transfer(session, transfer_id):
    """Run a single transfer"""
    start = time.time()

    try:
        response = await session.post(
            PROXY_URL,
            json={"size_mb": PAYLOAD_SIZE_MB, "chunk_size": 1048576},
            timeout=120.0
        )

        duration = time.time() - start
        result = response.json()

        return {
            "id": transfer_id,
            "status": response.status_code,
            "success": result.get("success", False),
            "bytes": result.get("bytes_sent", 0),
            "duration": duration,
            "throughput_mbps": result.get("throughput_mbps", 0)
        }
    except Exception as e:
        duration = time.time() - start
        return {
            "id": transfer_id,
            "status": 0,
            "success": False,
            "bytes": 0,
            "duration": duration,
            "error": str(e)
        }

async def run_concurrent_test():
    """Run concurrent transfers and monitor memory"""
    print("=" * 80)
    print("Concurrent Chunked Transfer Test")
    print("=" * 80)
    print()
    print(f"Configuration:")
    print(f"  Concurrent transfers: {NUM_CONCURRENT}")
    print(f"  Payload size: {PAYLOAD_SIZE_MB}MB each")
    print(f"  Total data: {NUM_CONCURRENT * PAYLOAD_SIZE_MB}MB")
    print()

    # Get baseline memory
    print("=== Baseline Memory Usage ===")
    baseline = get_memory_stats()
    print(f"  Caller: {baseline['caller']:.2f} MiB")
    print(f"  Receiver: {baseline['receiver']:.2f} MiB")
    print()

    # Start memory monitoring
    memory_log = []
    monitor_task = asyncio.create_task(monitor_memory(60, memory_log))

    await asyncio.sleep(2)

    print(f"=== Launching {NUM_CONCURRENT} Concurrent Transfers ===")
    print()

    start_time = time.time()

    # Launch all transfers concurrently
    async with httpx.AsyncClient(timeout=120.0) as session:
        tasks = [
            run_transfer(session, i+1)
            for i in range(NUM_CONCURRENT)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

    end_time = time.time()
    total_duration = end_time - start_time

    # Wait for monitoring to finish
    await asyncio.sleep(2)
    monitor_task.cancel()
    try:
        await monitor_task
    except asyncio.CancelledError:
        pass

    print()
    print("=== Transfer Results ===")
    print()
    print(f"{'ID':<4} {'Status':<8} {'Success':<8} {'MB':<8} {'Duration':<10} {'Throughput':<12}")
    print("-" * 70)

    success_count = 0
    total_bytes = 0

    for result in results:
        if isinstance(result, dict):
            success = "✓" if result["success"] else "✗"
            mb = result["bytes"] / (1024 * 1024)
            throughput = f"{result.get('throughput_mbps', 0):.2f} MB/s"

            print(f"{result['id']:<4} {result['status']:<8} {success:<8} {mb:<8.2f} {result['duration']:<10.2f} {throughput:<12}")

            if result["success"]:
                success_count += 1
                total_bytes += result["bytes"]

    print()
    print("=== Summary ===")
    print(f"  Successful transfers: {success_count}/{NUM_CONCURRENT}")
    print(f"  Total data transferred: {total_bytes / (1024 * 1024):.2f} MB")
    print(f"  Total duration: {total_duration:.2f} seconds")
    print(f"  Average throughput: {(total_bytes / (1024 * 1024)) / total_duration:.2f} MB/s")
    print()

    # Analyze memory
    if memory_log:
        max_caller = max(m['caller'] for m in memory_log)
        max_receiver = max(m['receiver'] for m in memory_log)

        caller_increase = max_caller - baseline['caller']
        receiver_increase = max_receiver - baseline['receiver']

        print("=== Memory Analysis ===")
        print()
        print("Caller (sse-proxy-dapr):")
        print(f"  Baseline: {baseline['caller']:.2f} MiB")
        print(f"  Peak: {max_caller:.2f} MiB")
        print(f"  Increase: {caller_increase:.2f} MiB")
        print(f"  Per transfer: {caller_increase / NUM_CONCURRENT:.2f} MiB")
        print()
        print("Receiver (chunk-receiver-dapr):")
        print(f"  Baseline: {baseline['receiver']:.2f} MiB")
        print(f"  Peak: {max_receiver:.2f} MiB")
        print(f"  Increase: {receiver_increase:.2f} MiB")
        print(f"  Per transfer: {receiver_increase / NUM_CONCURRENT:.2f} MiB")
        print()

        expected_with_retry = NUM_CONCURRENT * 220
        expected_no_retry = NUM_CONCURRENT * 17

        print("Expected memory (based on single transfer tests):")
        print(f"  WITH retries: ~{expected_with_retry} MiB ({NUM_CONCURRENT} × 220 MiB)")
        print(f"  NO retries: ~{expected_no_retry} MiB ({NUM_CONCURRENT} × 17 MiB)")
        print()
        print(f"Actual increase: {caller_increase:.2f} MiB")
        print()

        if caller_increase < 200:
            print("✓ Memory increase consistent with NO-RETRY behavior")
            print("  Streaming is working efficiently under concurrent load!")
        else:
            print("⚠ Memory increase suggests buffering is occurring")
            print("  May indicate retry policy is active or connection pooling limits")

    print()
    print("=== Final Memory Usage ===")
    final = get_memory_stats()
    print(f"  Caller: {final['caller']:.2f} MiB")
    print(f"  Receiver: {final['receiver']:.2f} MiB")
    print()

    # Save memory log
    with open('/tmp/concurrent_memory_log.json', 'w') as f:
        json.dump(memory_log, f, indent=2)

    print("Memory log saved to: /tmp/concurrent_memory_log.json")
    print()
    print("=" * 80)
    print("Test Complete")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(run_concurrent_test())

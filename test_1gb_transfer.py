#!/usr/bin/env python3
"""
1GB Single File Transfer Test
Tests a single 1GB transfer with detailed memory monitoring of both Dapr sidecars
"""

import asyncio
import httpx
import time
import subprocess
import json
from datetime import datetime

PAYLOAD_SIZE_MB = 1024  # 1GB
CHUNK_SIZE = 1048576  # 1MB chunks
PROXY_URL = "http://localhost:8001/test-chunked-transfer"
MONITOR_INTERVAL = 0.5  # Monitor every 0.5 seconds

def get_memory_stats():
    """Get current memory usage for both Dapr sidecars"""
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

async def monitor_memory(stop_event, results_list):
    """Monitor memory usage continuously until stop_event is set"""
    start_time = time.time()
    while not stop_event.is_set():
        stats = get_memory_stats()
        elapsed = time.time() - start_time
        stats['elapsed'] = elapsed
        stats['timestamp'] = time.time()
        results_list.append(stats)
        await asyncio.sleep(MONITOR_INTERVAL)

async def run_1gb_test():
    """Run 1GB transfer and monitor memory"""
    print("=" * 80)
    print("1GB Single File Transfer Test with Memory Monitoring")
    print("=" * 80)
    print()
    print(f"Configuration:")
    print(f"  Payload size: {PAYLOAD_SIZE_MB}MB (1GB)")
    print(f"  Chunk size: {CHUNK_SIZE} bytes (1MB)")
    print(f"  Expected chunks: {PAYLOAD_SIZE_MB}")
    print(f"  Memory monitoring interval: {MONITOR_INTERVAL}s")
    print()

    # Get baseline memory
    print("=== Baseline Memory Usage ===")
    await asyncio.sleep(2)  # Let things settle
    baseline = get_memory_stats()
    print(f"  Caller (sse-proxy-dapr): {baseline['caller']:.2f} MiB")
    print(f"  Receiver (chunk-receiver-dapr): {baseline['receiver']:.2f} MiB")
    print()

    # Cleanup any previous file
    print("Cleaning up previous test file...")
    try:
        subprocess.run(
            ["docker", "exec", "dapr-multi-app-testing-chunk-receiver-1",
             "rm", "-f", "/tmp/received_chunks.bin"],
            capture_output=True,
            timeout=5
        )
    except:
        pass

    # Start memory monitoring
    memory_log = []
    stop_event = asyncio.Event()
    monitor_task = asyncio.create_task(monitor_memory(stop_event, memory_log))

    await asyncio.sleep(1)

    print(f"=== Starting 1GB Transfer ===")
    print(f"Started at: {datetime.now().strftime('%H:%M:%S')}")
    print()

    start_time = time.time()

    try:
        # Launch transfer with extended timeout
        async with httpx.AsyncClient(timeout=600.0) as client:
            response = await client.post(
                PROXY_URL,
                json={"size_mb": PAYLOAD_SIZE_MB, "chunk_size": CHUNK_SIZE}
            )

        end_time = time.time()
        duration = end_time - start_time

        # Stop monitoring
        stop_event.set()
        await asyncio.sleep(1)
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass

        # Parse response
        result = response.json()

        print(f"Completed at: {datetime.now().strftime('%H:%M:%S')}")
        print()
        print("=== Transfer Results ===")
        print(f"  Status: {response.status_code}")
        print(f"  Success: {'✓' if result.get('success', False) else '✗'}")
        print(f"  Bytes sent: {result.get('bytes_sent', 0):,}")
        print(f"  Chunks sent: {result.get('chunks_sent', 0):,}")
        print(f"  Duration: {duration:.2f} seconds")
        print(f"  Throughput: {(result.get('bytes_sent', 0) / (1024 * 1024)) / duration:.2f} MB/s")
        print()

        # Verify file size
        print("=== File Verification ===")
        try:
            file_check = subprocess.run(
                ["docker", "exec", "dapr-multi-app-testing-chunk-receiver-1",
                 "stat", "-c%s", "/tmp/received_chunks.bin"],
                capture_output=True,
                text=True,
                timeout=5
            )
            file_size = int(file_check.stdout.strip())
            expected_size = PAYLOAD_SIZE_MB * 1024 * 1024
            print(f"  File size: {file_size:,} bytes")
            print(f"  Expected: {expected_size:,} bytes")
            print(f"  Match: {'✓' if file_size == expected_size else '✗ MISMATCH'}")
            print()
        except Exception as e:
            print(f"  Error checking file: {e}")
            print()

    except Exception as e:
        print(f"✗ Transfer failed: {e}")
        print()
        stop_event.set()
        await asyncio.sleep(1)
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
        return

    # Analyze memory
    if memory_log:
        max_caller = max(m['caller'] for m in memory_log)
        max_receiver = max(m['receiver'] for m in memory_log)

        caller_increase = max_caller - baseline['caller']
        receiver_increase = max_receiver - baseline['receiver']

        # Find when peak occurred
        peak_caller_time = next(m['elapsed'] for m in memory_log if m['caller'] == max_caller)
        peak_receiver_time = next(m['elapsed'] for m in memory_log if m['receiver'] == max_receiver)

        print("=== Memory Analysis ===")
        print()
        print("Caller (sse-proxy-dapr):")
        print(f"  Baseline: {baseline['caller']:.2f} MiB")
        print(f"  Peak: {max_caller:.2f} MiB (at {peak_caller_time:.1f}s)")
        print(f"  Increase: {caller_increase:.2f} MiB")
        print(f"  Percentage of payload: {(caller_increase / (PAYLOAD_SIZE_MB)) * 100:.2f}%")
        print()

        print("Receiver (chunk-receiver-dapr):")
        print(f"  Baseline: {baseline['receiver']:.2f} MiB")
        print(f"  Peak: {max_receiver:.2f} MiB (at {peak_receiver_time:.1f}s)")
        print(f"  Increase: {receiver_increase:.2f} MiB")
        print(f"  Percentage of payload: {(receiver_increase / (PAYLOAD_SIZE_MB)) * 100:.2f}%")
        print()

        # Calculate what would happen WITH retry policy
        expected_with_retry = PAYLOAD_SIZE_MB * 2.2  # 2.2x buffering observed

        print("Expected memory with DIFFERENT configurations:")
        print(f"  WITH retry policy (hypothetical): ~{expected_with_retry:.0f} MiB ({PAYLOAD_SIZE_MB}MB × 2.2)")
        print(f"  NO retry policy (actual): {caller_increase:.2f} MiB")
        print()

        memory_saved = expected_with_retry - caller_increase
        memory_saved_percent = (memory_saved / expected_with_retry) * 100

        print(f"Memory SAVED by no-retry policy: {memory_saved:.2f} MiB ({memory_saved_percent:.1f}%)")
        print()

        if caller_increase < 100:
            print("✓ EXCELLENT: Memory increase is minimal (<100 MiB)")
            print("  True streaming is working perfectly for 1GB payload!")
        elif caller_increase < 500:
            print("✓ GOOD: Memory increase is reasonable (<500 MiB)")
            print("  Streaming is working well with some overhead")
        else:
            print("⚠ WARNING: Memory increase is significant (>500 MiB)")
            print("  May indicate partial buffering occurring")

        print()

        # Show memory timeline (sample)
        print("=== Memory Timeline (Sample) ===")
        print()
        print(f"{'Time (s)':<10} {'Caller (MiB)':<15} {'Receiver (MiB)':<15}")
        print("-" * 40)

        # Sample every 5 seconds
        sample_interval = 5.0
        next_sample = 0
        for entry in memory_log:
            if entry['elapsed'] >= next_sample:
                print(f"{entry['elapsed']:<10.1f} {entry['caller']:<15.2f} {entry['receiver']:<15.2f}")
                next_sample += sample_interval
                if next_sample > entry['elapsed'] + sample_interval * 2:
                    next_sample = entry['elapsed'] + sample_interval

        print()

    print("=== Final Memory Usage ===")
    await asyncio.sleep(2)
    final = get_memory_stats()
    print(f"  Caller: {final['caller']:.2f} MiB")
    print(f"  Receiver: {final['receiver']:.2f} MiB")
    print()

    # Save detailed memory log
    log_file = '/tmp/1gb_memory_log.json'
    with open(log_file, 'w') as f:
        json.dump({
            'baseline': baseline,
            'final': final,
            'timeline': memory_log,
            'test_config': {
                'payload_mb': PAYLOAD_SIZE_MB,
                'chunk_size': CHUNK_SIZE,
                'duration_seconds': duration if 'duration' in locals() else 0
            }
        }, f, indent=2)

    print(f"Detailed memory log saved to: {log_file}")
    print()

    print("=" * 80)
    print("Test Complete")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(run_1gb_test())

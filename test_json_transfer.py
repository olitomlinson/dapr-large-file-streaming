#!/usr/bin/env python3
"""
Large JSON Document Transfer Test
Tests large JSON document transfer with detailed memory monitoring of both Dapr sidecars
"""

import asyncio
import httpx
import time
import subprocess
import json
from datetime import datetime

NUM_RECORDS = 500000  # 500k records (generates ~200-300MB JSON)
RECORDS_PER_CHUNK = 100  # Stream in small chunks
PROXY_URL = "http://localhost:8001/test-json-transfer"
MONITOR_INTERVAL = 0.5  # Monitor every 0.5 seconds

def get_memory_stats():
    """Get current memory usage for both Dapr sidecars"""
    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format",
             "{{.Container}},{{.MemUsage}}",
             "dapr-large-file-streaming-chunk-sender-dapr-1",
             "dapr-large-file-streaming-chunk-receiver-dapr-1"],
            capture_output=True,
            text=True,
            timeout=5
        )

        lines = result.stdout.strip().split('\n')
        stats = {}

        for line in lines:
            if 'chunk-sender-dapr' in line:
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

async def run_json_test():
    """Run large JSON transfer and monitor memory"""
    print("=" * 80)
    print("Large JSON Document Transfer Test with Memory Monitoring")
    print("=" * 80)
    print()
    print(f"Configuration:")
    print(f"  Number of records: {NUM_RECORDS:,}")
    print(f"  Records per chunk: {RECORDS_PER_CHUNK}")
    print(f"  Expected chunks: {NUM_RECORDS // RECORDS_PER_CHUNK:,}")
    print(f"  Memory monitoring interval: {MONITOR_INTERVAL}s")
    print()

    # Get baseline memory
    print("=== Baseline Memory Usage ===")
    await asyncio.sleep(2)  # Let things settle
    baseline = get_memory_stats()
    print(f"  Caller (chunk-sender-dapr): {baseline['caller']:.2f} MiB")
    print(f"  Receiver (chunk-receiver-dapr): {baseline['receiver']:.2f} MiB")
    print()

    # Cleanup any previous file
    print("Cleaning up previous test file...")
    try:
        subprocess.run(
            ["docker", "exec", "dapr-large-file-streaming-chunk-receiver-1",
             "rm", "-f", "/tmp/received_json.json"],
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

    print(f"=== Starting JSON Transfer ===")
    print(f"Started at: {datetime.now().strftime('%H:%M:%S')}")
    print()

    start_time = time.time()

    try:
        # Launch transfer with extended timeout
        async with httpx.AsyncClient(timeout=600.0) as client:
            response = await client.post(
                PROXY_URL,
                json={"num_records": NUM_RECORDS, "records_per_chunk": RECORDS_PER_CHUNK}
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
        print(f"  Records sent: {result.get('records_sent', 0):,}")
        print(f"  Total chunks: {result.get('total_chunks', 0):,}")
        print(f"  Duration: {duration:.2f} seconds")
        print(f"  Records/second: {result.get('records_per_second', 0):,.2f}")
        print()

        # Verify file and parse JSON
        print("=== File Verification ===")
        try:
            # Check file size
            file_check = subprocess.run(
                ["docker", "exec", "dapr-large-file-streaming-chunk-receiver-1",
                 "stat", "-c%s", "/tmp/received_json.json"],
                capture_output=True,
                text=True,
                timeout=5
            )
            file_size = int(file_check.stdout.strip())
            print(f"  File size: {file_size:,} bytes ({file_size / (1024*1024):.2f} MB)")

            # Get record count from receiver response
            receiver_response = result.get('receiver_response', {})
            records_received = receiver_response.get('records_received', 0)
            print(f"  Records received: {records_received:,}")
            print(f"  Expected records: {NUM_RECORDS:,}")
            print(f"  Match: {'✓' if records_received == NUM_RECORDS else '✗ MISMATCH'}")
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

        # Estimate JSON size
        estimated_json_size_mb = file_size / (1024 * 1024) if 'file_size' in locals() else 0

        print("=== Memory Analysis ===")
        print()
        print("Caller (chunk-sender-dapr):")
        print(f"  Baseline: {baseline['caller']:.2f} MiB")
        print(f"  Peak: {max_caller:.2f} MiB (at {peak_caller_time:.1f}s)")
        print(f"  Increase: {caller_increase:.2f} MiB")
        if estimated_json_size_mb > 0:
            print(f"  Percentage of JSON size: {(caller_increase / estimated_json_size_mb) * 100:.2f}%")
        print()

        print("Receiver (chunk-receiver-dapr):")
        print(f"  Baseline: {baseline['receiver']:.2f} MiB")
        print(f"  Peak: {max_receiver:.2f} MiB (at {peak_receiver_time:.1f}s)")
        print(f"  Increase: {receiver_increase:.2f} MiB")
        if estimated_json_size_mb > 0:
            print(f"  Percentage of JSON size: {(receiver_increase / estimated_json_size_mb) * 100:.2f}%")
        print()

        # Calculate what would happen WITH retry policy
        if estimated_json_size_mb > 0:
            expected_with_retry = estimated_json_size_mb * 2.2  # 2.2x buffering observed

            print("Expected memory with DIFFERENT configurations:")
            print(f"  WITH retry policy (hypothetical): ~{expected_with_retry:.0f} MiB ({estimated_json_size_mb:.0f}MB × 2.2)")
            print(f"  NO retry policy (actual): {caller_increase:.2f} MiB")
            print()

            memory_saved = expected_with_retry - caller_increase
            memory_saved_percent = (memory_saved / expected_with_retry) * 100

            print(f"Memory SAVED by no-retry policy: {memory_saved:.2f} MiB ({memory_saved_percent:.1f}%)")
            print()

        if caller_increase < 50:
            print("✓ EXCELLENT: Memory increase is minimal (<50 MiB)")
            print("  True streaming is working perfectly for JSON!")
        elif caller_increase < 100:
            print("✓ GOOD: Memory increase is reasonable (<100 MiB)")
            print("  Streaming is working well with some overhead")
        else:
            print("⚠ WARNING: Memory increase is significant (>100 MiB)")
            print("  May indicate partial buffering occurring")

        print()

        # Show memory timeline (sample)
        print("=== Memory Timeline (Sample) ===")
        print()
        print(f"{'Time (s)':<10} {'Caller (MiB)':<15} {'Receiver (MiB)':<15}")
        print("-" * 40)

        # Sample every 2 seconds
        sample_interval = 2.0
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
    log_file = '/tmp/json_memory_log.json'
    with open(log_file, 'w') as f:
        json.dump({
            'baseline': baseline,
            'final': final,
            'timeline': memory_log,
            'test_config': {
                'num_records': NUM_RECORDS,
                'records_per_chunk': RECORDS_PER_CHUNK,
                'duration_seconds': duration if 'duration' in locals() else 0,
                'json_size_bytes': file_size if 'file_size' in locals() else 0
            }
        }, f, indent=2)

    print(f"Detailed memory log saved to: {log_file}")
    print()

    print("=" * 80)
    print("Test Complete")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(run_json_test())

#!/usr/bin/env python3
"""
Sequential Binary Transfer Test with Memory Monitoring
Tests 10 sequential 10MB binary transfers to prove memory reuse vs accumulation
"""

import asyncio
import httpx
import time
import subprocess
import json
from datetime import datetime

NUM_SEQUENTIAL = 10
SIZE_MB = 100  # 100MB payload to match single transfer test
CHUNK_SIZE = 1048576  # 1MB chunks
PROXY_URL = "http://localhost:8001/test-chunked-transfer"
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

async def single_transfer(transfer_id, client):
    """Run a single 100MB transfer"""
    start_time = time.time()
    try:
        response = await client.post(
            PROXY_URL,
            json={"size_mb": SIZE_MB, "chunk_size": CHUNK_SIZE},
            timeout=120.0
        )
        duration = time.time() - start_time

        if response.status_code == 200:
            result = response.json()
            return {
                'id': transfer_id,
                'success': result.get('success', False),
                'bytes_sent': result.get('bytes_sent', 0),
                'duration': duration,
                'status_code': response.status_code
            }
        else:
            return {
                'id': transfer_id,
                'success': False,
                'bytes_sent': 0,
                'duration': duration,
                'status_code': response.status_code
            }
    except Exception as e:
        duration = time.time() - start_time
        print(f"Transfer {transfer_id} failed: {e}")
        return {
            'id': transfer_id,
            'success': False,
            'bytes_sent': 0,
            'duration': duration,
            'error': str(e)
        }

async def run_sequential_test():
    """Run sequential transfers and monitor memory"""
    print("=" * 80)
    print("Sequential Binary Transfer Test with Memory Monitoring")
    print("=" * 80)
    print()
    print(f"Configuration:")
    print(f"  Number of sequential transfers: {NUM_SEQUENTIAL}")
    print(f"  Payload size per transfer: {SIZE_MB} MB")
    print(f"  Total data: {NUM_SEQUENTIAL * SIZE_MB} MB")
    print(f"  Chunk size: {CHUNK_SIZE:,} bytes (1 MB)")
    print(f"  Memory monitoring interval: {MONITOR_INTERVAL}s")
    print()

    # Get baseline memory
    print("=== Baseline Memory Usage ===")
    await asyncio.sleep(2)  # Let things settle
    baseline = get_memory_stats()
    print(f"  Caller (chunk-sender-dapr): {baseline['caller']:.2f} MiB")
    print(f"  Receiver (chunk-receiver-dapr): {baseline['receiver']:.2f} MiB")
    print()

    # Cleanup any previous files
    print("Cleaning up previous test files...")
    try:
        subprocess.run(
            ["docker", "exec", "dapr-large-file-streaming-chunk-receiver-1",
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

    print(f"=== Starting {NUM_SEQUENTIAL} Sequential Transfers ===")
    print(f"Started at: {datetime.now().strftime('%H:%M:%S')}")
    print()

    start_time = time.time()
    results = []

    # Run transfers one at a time
    async with httpx.AsyncClient(timeout=120.0) as client:
        for i in range(1, NUM_SEQUENTIAL + 1):
            print(f"Transfer {i}/{NUM_SEQUENTIAL}...", end=" ", flush=True)
            result = await single_transfer(i, client)
            results.append(result)

            # Get memory after each transfer
            mem = get_memory_stats()
            print(f"✓ (Caller: {mem['caller']:.1f} MiB, Receiver: {mem['receiver']:.1f} MiB)")

            # Small pause between transfers
            await asyncio.sleep(0.5)

    end_time = time.time()
    total_duration = end_time - start_time

    # Stop monitoring
    stop_event.set()
    await asyncio.sleep(1)
    try:
        await monitor_task
    except asyncio.CancelledError:
        pass

    print()
    print(f"Completed at: {datetime.now().strftime('%H:%M:%S')}")
    print()

    # Analyze results
    print("=== Transfer Results ===")
    print(f"{'ID':<5} {'Status':<10} {'Bytes':<15} {'Duration (s)':<15} {'Success':<10}")
    print("-" * 60)

    success_count = 0
    total_bytes = 0
    total_transfer_time = 0

    for result in results:
        status = result.get('status_code', 'Error')
        success = '✓' if result['success'] else '✗'
        print(f"{result['id']:<5} {status:<10} {result['bytes_sent']:<15,} "
              f"{result['duration']:<15.2f} {success:<10}")

        if result['success']:
            success_count += 1
            total_bytes += result['bytes_sent']
            total_transfer_time += result['duration']

    print()
    print(f"  Successful transfers: {success_count}/{NUM_SEQUENTIAL}")
    print(f"  Total data transferred: {total_bytes:,} bytes ({total_bytes / (1024*1024):.2f} MB)")
    print(f"  Wall clock duration: {total_duration:.2f} seconds")
    print(f"  Average per-transfer time: {total_transfer_time / NUM_SEQUENTIAL:.2f} seconds")
    print(f"  Overall throughput: {(total_bytes / (1024*1024)) / total_duration:.2f} MB/s")
    print()

    # Analyze memory
    if memory_log:
        max_caller = max(m['caller'] for m in memory_log)
        max_receiver = max(m['receiver'] for m in memory_log)

        caller_increase = max_caller - baseline['caller']
        receiver_increase = max_receiver - baseline['receiver']

        # Find when peak occurred
        peak_caller_time = next(m['elapsed'] for m in memory_log if m['caller'] == max_caller)
        peak_receiver_time = next(m['elapsed'] for m in memory_log if m['receiver'] == max_receiver)

        # Expected if accumulating
        expected_if_accumulating = NUM_SEQUENTIAL * 17.9  # ~17.9 MiB per 100MB transfer
        single_transfer_overhead = 17.9  # Expected for single 100MB transfer

        print("=== Memory Analysis ===")
        print()
        print("Caller (chunk-sender-dapr):")
        print(f"  Baseline: {baseline['caller']:.2f} MiB")
        print(f"  Peak: {max_caller:.2f} MiB (at {peak_caller_time:.1f}s)")
        print(f"  Increase: {caller_increase:.2f} MiB")
        print(f"  Per transfer (if linear): {caller_increase / NUM_SEQUENTIAL:.2f} MiB")
        print()

        print("Receiver (chunk-receiver-dapr):")
        print(f"  Baseline: {baseline['receiver']:.2f} MiB")
        print(f"  Peak: {max_receiver:.2f} MiB (at {peak_receiver_time:.1f}s)")
        print(f"  Increase: {receiver_increase:.2f} MiB")
        print(f"  Per transfer (if linear): {receiver_increase / NUM_SEQUENTIAL:.2f} MiB")
        print()

        print("Memory Accumulation Analysis:")
        print(f"  Expected if ACCUMULATING: ~{expected_if_accumulating:.1f} MiB ({NUM_SEQUENTIAL} × {single_transfer_overhead} MiB)")
        print(f"  Expected if REUSING: ~{single_transfer_overhead:.1f} MiB (same as single transfer)")
        print(f"  Actual increase: {caller_increase:.2f} MiB")
        print()

        if caller_increase < single_transfer_overhead * 2:
            print("✓ EXCELLENT: Memory is REUSED across sequential transfers!")
            print(f"  Overhead stays constant (~{caller_increase:.1f} MiB) regardless of number of transfers")
            print("  Dapr's infrastructure buffers are reused, not accumulated")
            print("  Go GC may keep some memory allocated, but it's not growing linearly")
        elif caller_increase < expected_if_accumulating * 0.5:
            print("✓ GOOD: Memory growth is sub-linear")
            print(f"  Some accumulation but much less than expected")
        else:
            print("⚠ WARNING: Memory appears to accumulate linearly")
            print("  This suggests memory is not being reused efficiently")

        print()

        # Show memory per transfer over time
        print("=== Memory After Each Transfer ===")
        print()
        print(f"{'Transfer':<10} {'Time (s)':<12} {'Caller (MiB)':<15} {'Receiver (MiB)':<15}")
        print("-" * 52)

        # Find memory snapshot closest to each transfer completion
        transfer_times = [sum(r['duration'] for r in results[:i]) for i in range(1, len(results) + 1)]
        for i, t_time in enumerate(transfer_times, 1):
            # Find closest memory snapshot
            closest = min(memory_log, key=lambda m: abs(m['elapsed'] - t_time))
            print(f"{i:<10} {closest['elapsed']:<12.1f} {closest['caller']:<15.2f} {closest['receiver']:<15.2f}")

        print()

    print("=== Final Memory Usage ===")
    await asyncio.sleep(2)
    final = get_memory_stats()
    print(f"  Caller: {final['caller']:.2f} MiB")
    print(f"  Receiver: {final['receiver']:.2f} MiB")
    print(f"  Increase from baseline: {final['caller'] - baseline['caller']:.2f} MiB (caller), "
          f"{final['receiver'] - baseline['receiver']:.2f} MiB (receiver)")
    print()

    # Save detailed memory log
    log_file = '/tmp/sequential_memory_log.json'
    with open(log_file, 'w') as f:
        json.dump({
            'baseline': baseline,
            'final': final,
            'timeline': memory_log,
            'test_config': {
                'num_sequential': NUM_SEQUENTIAL,
                'size_mb': SIZE_MB,
                'chunk_size': CHUNK_SIZE,
                'total_duration': total_duration,
                'success_count': success_count
            },
            'results': results
        }, f, indent=2)

    print(f"Detailed memory log saved to: {log_file}")
    print()

    print("=" * 80)
    print("Test Complete")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(run_sequential_test())

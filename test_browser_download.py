#!/usr/bin/env python3
"""
Browser Download Test with Memory Monitoring
Tests large file streaming through nginx/Dapr chain with detailed memory monitoring
of all 4 containers: nginx, nginx-dapr, file-generator, file-generator-dapr
"""

import asyncio
import httpx
import time
import subprocess
import json
from datetime import datetime

SIZE_MB = 100  # 100 MB file payload
DOWNLOAD_URL = "http://localhost/download/large-file"
MONITOR_INTERVAL = 0.5  # Monitor every 0.5 seconds

def get_memory_stats():
    """Get current memory usage for all 4 containers in the chain"""
    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format",
             "{{.Container}},{{.MemUsage}}",
             "dapr-large-file-streaming-nginx-1",
             "dapr-large-file-streaming-nginx-dapr-1",
             "dapr-large-file-streaming-file-generator-1",
             "dapr-large-file-streaming-file-generator-dapr-1"],
            capture_output=True,
            text=True,
            timeout=5
        )

        lines = result.stdout.strip().split('\n')
        stats = {}

        for line in lines:
            if 'nginx-1' in line and 'nginx-dapr' not in line:
                mem = line.split(',')[1].split('MiB')[0].strip()
                stats['nginx'] = float(mem)
            elif 'nginx-dapr-1' in line:
                mem = line.split(',')[1].split('MiB')[0].strip()
                stats['nginx_dapr'] = float(mem)
            elif 'file-generator-1' in line and 'file-generator-dapr' not in line:
                mem = line.split(',')[1].split('MiB')[0].strip()
                stats['file_generator'] = float(mem)
            elif 'file-generator-dapr-1' in line:
                mem = line.split(',')[1].split('MiB')[0].strip()
                stats['file_generator_dapr'] = float(mem)

        return stats
    except Exception as e:
        print(f"Error getting memory stats: {e}")
        return {'nginx': 0, 'nginx_dapr': 0, 'file_generator': 0, 'file_generator_dapr': 0}

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

async def run_browser_download_test():
    """Run browser download test and monitor memory across all 4 containers"""
    print("=" * 80)
    print("Browser Download Test with Memory Monitoring")
    print("=" * 80)
    print()
    print(f"Configuration:")
    print(f"  Payload size: {SIZE_MB} MB")
    print(f"  Download URL: {DOWNLOAD_URL}")
    print(f"  Memory monitoring interval: {MONITOR_INTERVAL}s")
    print()
    print("Architecture:")
    print("  Browser → nginx:80 → nginx-dapr:3500 → file-generator-dapr:3500 → file-generator:8003")
    print()

    # Get baseline memory
    print("=== Baseline Memory Usage ===")
    await asyncio.sleep(2)  # Let things settle
    baseline = get_memory_stats()
    print(f"  nginx: {baseline['nginx']:.2f} MiB")
    print(f"  nginx-dapr: {baseline['nginx_dapr']:.2f} MiB")
    print(f"  file-generator: {baseline['file_generator']:.2f} MiB")
    print(f"  file-generator-dapr: {baseline['file_generator_dapr']:.2f} MiB")
    print(f"  Total baseline: {sum(baseline.values()):.2f} MiB")
    print()

    # Start memory monitoring
    memory_log = []
    stop_event = asyncio.Event()
    monitor_task = asyncio.create_task(monitor_memory(stop_event, memory_log))

    await asyncio.sleep(1)

    print(f"=== Starting Download ===")
    print(f"Started at: {datetime.now().strftime('%H:%M:%S')}")
    print()

    start_time = time.time()

    try:
        # Simulate browser download request
        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
            response = await client.get(
                f"{DOWNLOAD_URL}?size_mb={SIZE_MB}&filename=test-{SIZE_MB}mb.bin"
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

        print(f"Completed at: {datetime.now().strftime('%H:%M:%S')}")
        print()
        print("=== Transfer Results ===")
        print(f"  Status: {response.status_code}")
        print(f"  Success: {'✓' if response.status_code == 200 else '✗'}")
        print(f"  Content-Type: {response.headers.get('content-type', 'not set')}")
        print(f"  Content-Disposition: {response.headers.get('content-disposition', 'not set')}")
        print(f"  Transfer-Encoding: {response.headers.get('transfer-encoding', 'not set')}")
        print(f"  Bytes received: {len(response.content):,}")
        print(f"  Expected bytes: {SIZE_MB * 1024 * 1024:,}")
        print(f"  Match: {'✓' if len(response.content) == SIZE_MB * 1024 * 1024 else '✗ MISMATCH'}")
        print(f"  Duration: {duration:.2f} seconds")
        print(f"  Throughput: {(len(response.content) / (1024 * 1024)) / duration:.2f} MB/s")
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
        max_nginx = max(m['nginx'] for m in memory_log)
        max_nginx_dapr = max(m['nginx_dapr'] for m in memory_log)
        max_file_gen = max(m['file_generator'] for m in memory_log)
        max_file_gen_dapr = max(m['file_generator_dapr'] for m in memory_log)

        nginx_increase = max_nginx - baseline['nginx']
        nginx_dapr_increase = max_nginx_dapr - baseline['nginx_dapr']
        file_gen_increase = max_file_gen - baseline['file_generator']
        file_gen_dapr_increase = max_file_gen_dapr - baseline['file_generator_dapr']

        total_increase = nginx_increase + nginx_dapr_increase + file_gen_increase + file_gen_dapr_increase

        # Find when peak occurred
        peak_nginx_time = next(m['elapsed'] for m in memory_log if m['nginx'] == max_nginx)
        peak_nginx_dapr_time = next(m['elapsed'] for m in memory_log if m['nginx_dapr'] == max_nginx_dapr)
        peak_file_gen_time = next(m['elapsed'] for m in memory_log if m['file_generator'] == max_file_gen)
        peak_file_gen_dapr_time = next(m['elapsed'] for m in memory_log if m['file_generator_dapr'] == max_file_gen_dapr)

        # Calculate payload size
        payload_size_mb = SIZE_MB

        print("=== Memory Analysis ===")
        print()
        print("nginx:")
        print(f"  Baseline: {baseline['nginx']:.2f} MiB")
        print(f"  Peak: {max_nginx:.2f} MiB (at {peak_nginx_time:.1f}s)")
        print(f"  Increase: {nginx_increase:.2f} MiB")
        print(f"  Percentage of payload: {(nginx_increase / payload_size_mb) * 100:.2f}%")
        print()

        print("nginx-dapr:")
        print(f"  Baseline: {baseline['nginx_dapr']:.2f} MiB")
        print(f"  Peak: {max_nginx_dapr:.2f} MiB (at {peak_nginx_dapr_time:.1f}s)")
        print(f"  Increase: {nginx_dapr_increase:.2f} MiB")
        print(f"  Percentage of payload: {(nginx_dapr_increase / payload_size_mb) * 100:.2f}%")
        print()

        print("file-generator:")
        print(f"  Baseline: {baseline['file_generator']:.2f} MiB")
        print(f"  Peak: {max_file_gen:.2f} MiB (at {peak_file_gen_time:.1f}s)")
        print(f"  Increase: {file_gen_increase:.2f} MiB")
        print(f"  Percentage of payload: {(file_gen_increase / payload_size_mb) * 100:.2f}%")
        print()

        print("file-generator-dapr:")
        print(f"  Baseline: {baseline['file_generator_dapr']:.2f} MiB")
        print(f"  Peak: {max_file_gen_dapr:.2f} MiB (at {peak_file_gen_dapr_time:.1f}s)")
        print(f"  Increase: {file_gen_dapr_increase:.2f} MiB")
        print(f"  Percentage of payload: {(file_gen_dapr_increase / payload_size_mb) * 100:.2f}%")
        print()

        print("Total Chain:")
        print(f"  Total memory increase: {total_increase:.2f} MiB")
        print(f"  Percentage of payload: {(total_increase / payload_size_mb) * 100:.2f}%")
        print()

        if total_increase < 50:
            print("✓ EXCELLENT: Total chain memory increase is low (<50 MiB)")
            print("  Streaming is working perfectly across the entire chain!")
        elif total_increase < 100:
            print("✓ GOOD: Total chain memory increase is acceptable (<100 MiB)")
            print("  Streaming is working well")
        else:
            print("⚠ WARNING: Total chain memory increase is high (>100 MiB)")
            print("  May indicate partial buffering in one or more containers")

        print()

        # Show memory timeline (sample)
        print("=== Memory Timeline (Sample) ===")
        print()
        print(f"{'Time (s)':<10} {'nginx':<10} {'nginx-dapr':<12} {'file-gen':<12} {'file-gen-dapr':<12} {'Total':<10}")
        print("-" * 76)

        # Sample every 1 second
        sample_interval = 1.0
        next_sample = 0
        for entry in memory_log:
            if entry['elapsed'] >= next_sample:
                total = entry['nginx'] + entry['nginx_dapr'] + entry['file_generator'] + entry['file_generator_dapr']
                print(f"{entry['elapsed']:<10.1f} {entry['nginx']:<10.2f} {entry['nginx_dapr']:<12.2f} "
                      f"{entry['file_generator']:<12.2f} {entry['file_generator_dapr']:<12.2f} {total:<10.2f}")
                next_sample += sample_interval
                if next_sample > entry['elapsed'] + sample_interval * 2:
                    next_sample = entry['elapsed'] + sample_interval

        print()

    print("=== Final Memory Usage ===")
    await asyncio.sleep(2)
    final = get_memory_stats()
    print(f"  nginx: {final['nginx']:.2f} MiB")
    print(f"  nginx-dapr: {final['nginx_dapr']:.2f} MiB")
    print(f"  file-generator: {final['file_generator']:.2f} MiB")
    print(f"  file-generator-dapr: {final['file_generator_dapr']:.2f} MiB")
    print(f"  Total: {sum(final.values()):.2f} MiB")
    print()

    # Save detailed memory log
    log_file = '/tmp/browser_download_memory_log.json'
    with open(log_file, 'w') as f:
        json.dump({
            'baseline': baseline,
            'final': final,
            'timeline': memory_log,
            'test_config': {
                'size_mb': SIZE_MB,
                'duration_seconds': duration if 'duration' in locals() else 0,
                'bytes_received': len(response.content) if 'response' in locals() else 0
            }
        }, f, indent=2)

    print(f"Detailed memory log saved to: {log_file}")
    print()

    print("=" * 80)
    print("Test Complete")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(run_browser_download_test())

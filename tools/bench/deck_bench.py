#!/usr/bin/env python3
"""Steam Deck benchmark driver for Uncharted: The Nathan Drake Collection.

Drives repeatable benchmarks over SSH from the laptop to the Steam Deck.
Standard library only.

Usage:
    tools/bench/deck_bench.py push <AppImage> <label>
    tools/bench/deck_bench.py run <label> <scene> [--mode 30|60|uncapped] [--seconds N] [--repeat K]
    tools/bench/deck_bench.py compare <labelA> <labelB> <scene> [--mode 30|60|uncapped]
"""

import argparse
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

DECK_HOST = os.environ.get("DECK_HOST", "deck@192.168.20.254")
DECK_BASE = "~/unch-bench"
RESULTS_ROOT = Path("/var/home/vishwas/src/bench-results")
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def ssh(cmd, check=True):
    full_cmd = ["ssh", "-o", "BatchMode=yes", DECK_HOST, cmd]
    return subprocess.run(full_cmd, capture_output=True, text=True, check=check)


def scp_to(local_path, remote_path):
    full_cmd = ["scp", "-o", "BatchMode=yes", str(local_path), f"{DECK_HOST}:{remote_path}"]
    return subprocess.run(full_cmd, check=True)


def scp_from(remote_path, local_path):
    full_cmd = ["scp", "-o", "BatchMode=yes", "-r", f"{DECK_HOST}:{remote_path}", str(local_path)]
    return subprocess.run(full_cmd, check=True)


def cmd_push(args):
    appimage = Path(args.appimage).resolve()
    if not appimage.is_file():
        print(f"Error: AppImage not found: {appimage}", file=sys.stderr)
        sys.exit(1)
    label = args.label
    print(f"Pushing {appimage.name} to Deck as label '{label}'...")

    ssh(f"mkdir -p {DECK_BASE}/builds")
    target_appimage = f"{DECK_BASE}/builds/{label}.AppImage"
    scp_to(appimage, target_appimage)

    extract_cmd = (
        f"cd {DECK_BASE}/builds && "
        f"rm -rf {label} squashfs-root && "
        f"chmod +x {label}.AppImage && "
        f"./{label}.AppImage --appimage-extract >/dev/null && "
        f"mv squashfs-root {label} && "
        f"rm -f {label}.AppImage && "
        f"echo 'Extraction complete:' && ls -la {label}/bin/"
    )
    res = ssh(extract_cmd)
    print(res.stdout)
    print(f"Successfully pushed and extracted {label} on Deck.")


def parse_window_log(window_log_text, gpu_busy_text):
    stats_lines = []
    pacing_lines = []
    stalls = []

    for line in window_log_text.splitlines():
        line = line.strip()
        if line.startswith("Frame stats:"):
            stats_lines.append(line)
        elif line.startswith("Frame pacing:"):
            pacing_lines.append(line)
        elif line.startswith("Stall:"):
            stalls.append(line)

    fps_list = []
    worst_frame_list = []
    compiles_list = []
    compile_ms_list = []
    syncs_list = []
    write_faults_list = []
    us_draw_list = []
    draws_frame_list = []
    idle_pct_list = []
    blocked_recorder_list = []
    blocked_host_copies_list = []
    blocked_copy_threads_list = []
    blocked_ticks_list = []

    # Frame stats regex
    re_stats = re.compile(
        r"Frame stats:\s+([0-9.]+)\s+FPS,\s+worst frame\s+([0-9.]+)\s+ms.*?;"
        r"\s+([0-9]+)\s+shader/pipeline compiles,\s+([0-9.]+)\s+ms;"
        r"\s+([0-9]+)\s+recorder syncs;"
        r"\s+([0-9.]+)\s+write faults/s.*?;"
        r".*?GPU thread\s+([0-9.]+)\s+us/draw,"
        r"\s+([0-9.]+)\s+draws/frame,"
        r"\s+idle\s+([0-9.]+)%;"
        r"\s+blocked:\s+recorder\s+([0-9.]+)%,\s+host copies\s+([0-9.]+)%.*?,\s+copy threads\s+([0-9.]+)%,\s+GPU ticks\s+([0-9.]+)%"
    )

    for line in stats_lines:
        m = re_stats.search(line)
        if m:
            fps_list.append(float(m.group(1)))
            worst_frame_list.append(float(m.group(2)))
            compiles_list.append(int(m.group(3)))
            compile_ms_list.append(float(m.group(4)))
            syncs_list.append(int(m.group(5)))
            write_faults_list.append(float(m.group(6)))
            us_draw_list.append(float(m.group(7)))
            draws_frame_list.append(float(m.group(8)))
            idle_pct_list.append(float(m.group(9)))
            blocked_recorder_list.append(float(m.group(10)))
            blocked_host_copies_list.append(float(m.group(11)))
            blocked_copy_threads_list.append(float(m.group(12)))
            blocked_ticks_list.append(float(m.group(13)))

    median_ms_list = []
    stddev_ms_list = []
    p99_ms_list = []
    re_pacing = re.compile(r"Frame pacing:\s+median\s+([0-9.]+)\s+ms,\s+stddev\s+([0-9.]+)\s+ms,\s+p99\s+([0-9.]+)\s+ms")

    for line in pacing_lines:
        m = re_pacing.search(line)
        if m:
            median_ms_list.append(float(m.group(1)))
            stddev_ms_list.append(float(m.group(2)))
            p99_ms_list.append(float(m.group(3)))

    gpu_busy_list = []
    for line in gpu_busy_text.splitlines():
        line = line.strip()
        if line:
            try:
                gpu_busy_list.append(float(line))
            except ValueError:
                pass

    avg_fps = statistics.mean(fps_list) if fps_list else 0.0
    worst_frame = max(worst_frame_list) if worst_frame_list else 0.0
    p99_frame_ms = statistics.mean(p99_ms_list) if p99_ms_list else (worst_frame if worst_frame else 33.33)
    low_1pct_fps = (1000.0 / p99_frame_ms) if p99_frame_ms > 0 else 0.0
    median_frame_ms = statistics.mean(median_ms_list) if median_ms_list else 33.33
    frame_stddev = statistics.mean(stddev_ms_list) if stddev_ms_list else 0.0

    avg_us_draw = statistics.mean(us_draw_list) if us_draw_list else 0.0
    avg_draws_frame = statistics.mean(draws_frame_list) if draws_frame_list else 0.0
    avg_idle = statistics.mean(idle_pct_list) if idle_pct_list else 0.0
    avg_host_copies_blocked = statistics.mean(blocked_host_copies_list) if blocked_host_copies_list else 0.0
    avg_recorder_blocked = statistics.mean(blocked_recorder_list) if blocked_recorder_list else 0.0
    avg_gpu_busy = statistics.mean(gpu_busy_list) if gpu_busy_list else 0.0

    return {
        "avg_fps": avg_fps,
        "low_1pct_fps": low_1pct_fps,
        "median_frame_ms": median_frame_ms,
        "p99_frame_ms": p99_frame_ms,
        "frame_stddev_ms": frame_stddev,
        "worst_frame_ms": worst_frame,
        "gpu_thread_us_draw": avg_us_draw,
        "draws_per_frame": avg_draws_frame,
        "gpu_thread_idle_pct": avg_idle,
        "gpu_thread_host_copies_wait_pct": avg_host_copies_blocked,
        "gpu_thread_recorder_wait_pct": avg_recorder_blocked,
        "gpu_busy_pct": avg_gpu_busy,
        "total_compiles": sum(compiles_list),
        "total_compile_ms": sum(compile_ms_list),
        "total_recorder_syncs": sum(syncs_list),
        "avg_write_faults_sec": statistics.mean(write_faults_list) if write_faults_list else 0.0,
        "samples_count": len(fps_list),
        "stalls": stalls,
    }


def cmd_run(args):
    label = args.label
    scene = args.scene
    mode = args.mode
    seconds = args.seconds
    repeats = args.repeat

    if mode == "30":
        nav_hz, nav_present = "30", "Fifo"
    elif mode == "60":
        nav_hz, nav_present = "60", "Fifo"
    elif mode == "uncapped":
        nav_hz, nav_present = "240", "Immediate"
    else:
        print(f"Unknown mode: {mode}", file=sys.stderr)
        sys.exit(1)

    print(f"=== Starting benchmark: {label} scene={scene} mode={mode} ({nav_hz} Hz, {nav_present}) repeats={repeats} ===")

    # Ensure bench_deck.py and seeds are on Deck
    deck_script = REPO_ROOT / "bench_deck.py"
    if not deck_script.is_file():
        deck_script = Path("/var/home/vishwas/src/bench_deck.py")
    scp_to(deck_script, f"{DECK_BASE}/bench_deck.py")

    results_dir = RESULTS_ROOT / label / scene / mode
    results_dir.mkdir(parents=True, exist_ok=True)

    # Warm-up run first if warm-cache does not exist
    warm_cache_dir = f"{DECK_BASE}/warm-cache/{label}"
    check_warm = ssh(f"test -d {warm_cache_dir}/cache && echo exists || echo missing").stdout.strip()
    if check_warm != "exists":
        print(f"Running warm-up run for {label} to populate shader cache...")
        warmup_work = f"{DECK_BASE}/warmup-{label}"
        prep_cmd = (
            f"rm -rf {warmup_work} && mkdir -p {warmup_work}/user && "
            f"cp -a {DECK_BASE}/seed/* {warmup_work}/user/"
        )
        ssh(prep_cmd)
        run_cmd = f"python3 {DECK_BASE}/bench_deck.py {DECK_BASE}/builds/{label} {warmup_work} 30 Fifo 15"
        res = ssh(run_cmd, check=False)
        if res.returncode != 0:
            print(f"Warning: Warmup run returned code {res.returncode}: {res.stderr}\n{res.stdout}")
        # Copy cache
        ssh(f"mkdir -p {warm_cache_dir} && cp -a {warmup_work}/user/cache {warm_cache_dir}/")
        print(f"Warm cache created for {label}.")

    run_summaries = []

    for r in range(1, repeats + 1):
        print(f"\n--- Run {r}/{repeats} for {label} ({mode}) ---")
        run_work = f"{DECK_BASE}/work-{label}-{scene}-{mode}-r{r}"
        prep_cmd = (
            f"rm -rf {run_work} && mkdir -p {run_work}/user && "
            f"cp -a {DECK_BASE}/seed/* {run_work}/user/ && "
            f"cp -a {warm_cache_dir}/cache {run_work}/user/"
        )
        ssh(prep_cmd)

        run_cmd = f"python3 {DECK_BASE}/bench_deck.py {DECK_BASE}/builds/{label} {run_work} {nav_hz} {nav_present} {seconds}"
        t_start = time.time()
        res = ssh(run_cmd, check=False)
        t_elapsed = time.time() - t_start
        print(res.stdout)

        if res.returncode != 0:
            print(f"ERROR: Run {r} failed (exit {res.returncode})! Run aborted.", file=sys.stderr)
            sys.exit(1)

        # Collect results to laptop
        local_run_dir = results_dir / f"run_{r}"
        local_run_dir.mkdir(parents=True, exist_ok=True)
        scp_from(f"{run_work}/window.log", local_run_dir / "window.log")
        scp_from(f"{run_work}/game.log", local_run_dir / "game.log")
        scp_from(f"{run_work}/gpu_busy.txt", local_run_dir / "gpu_busy.txt")
        scp_from(f"{run_work}/shots", local_run_dir / "shots")

        # Parse results
        win_log = (local_run_dir / "window.log").read_text(errors="replace")
        gpu_txt = (local_run_dir / "gpu_busy.txt").read_text(errors="replace")
        summary = parse_window_log(win_log, gpu_txt)
        summary["run_index"] = r
        summary["label"] = label
        summary["scene"] = scene
        summary["mode"] = mode
        summary["wall_time_s"] = t_elapsed

        with open(local_run_dir / "summary.json", "w") as f:
            json.dump(summary, f, indent=2)

        run_summaries.append(summary)

        print(
            f"Run {r} Result: {summary['avg_fps']:.1f} FPS (1% low: {summary['low_1pct_fps']:.1f} FPS), "
            f"p99={summary['p99_frame_ms']:.1f}ms, GPU thread={summary['gpu_thread_us_draw']:.2f} us/draw, "
            f"idle={summary['gpu_thread_idle_pct']:.1f}%, host copies wait={summary['gpu_thread_host_copies_wait_pct']:.1f}%, "
            f"GPU busy={summary['gpu_busy_pct']:.1f}%"
        )

    # Save mode summary
    with open(results_dir / "all_runs.json", "w") as f:
        json.dump(run_summaries, f, indent=2)

    return run_summaries


def cmd_compare(args):
    label_a = args.labelA
    label_b = args.labelB
    scene = args.scene
    mode = args.mode

    dir_a = RESULTS_ROOT / label_a / scene / mode
    dir_b = RESULTS_ROOT / label_b / scene / mode

    if not (dir_a / "all_runs.json").is_file() or not (dir_b / "all_runs.json").is_file():
        print(f"Error: Missing results for comparison at {dir_a} or {dir_b}", file=sys.stderr)
        sys.exit(1)

    runs_a = json.loads((dir_a / "all_runs.json").read_text())
    runs_b = json.loads((dir_b / "all_runs.json").read_text())

    metrics = [
        ("avg_fps", "Avg FPS", "{:.2f}", True),
        ("low_1pct_fps", "1% Low FPS", "{:.2f}", True),
        ("median_frame_ms", "Median Frame (ms)", "{:.2f}", False),
        ("p99_frame_ms", "P99 Frame (ms)", "{:.2f}", False),
        ("worst_frame_ms", "Worst Frame (ms)", "{:.1f}", False),
        ("gpu_thread_us_draw", "GPU Thread (us/draw)", "{:.2f}", False),
        ("gpu_thread_idle_pct", "GPU Thread Idle (%)", "{:.1f}", True),
        ("gpu_thread_host_copies_wait_pct", "Host Copies Wait (%)", "{:.1f}", False),
        ("gpu_busy_pct", "GPU Busy (%)", "{:.1f}", False),
    ]

    print(f"\n### A/B Comparison: {label_a} vs {label_b} ({scene}, {mode})\n")
    print(f"| Metric | {label_a} | {label_b} | Delta % |")
    print("|---|---|---|---|")

    for key, name, fmt, higher_is_better in metrics:
        vals_a = [r[key] for r in runs_a]
        vals_b = [r[key] for r in runs_b]
        mean_a = statistics.mean(vals_a)
        std_a = statistics.stdev(vals_a) if len(vals_a) > 1 else 0.0
        mean_b = statistics.mean(vals_b)
        std_b = statistics.stdev(vals_b) if len(vals_b) > 1 else 0.0

        if mean_a != 0:
            delta = ((mean_b - mean_a) / mean_a) * 100.0
            delta_str = f"{delta:+.1f}%"
        else:
            delta_str = "N/A"

        str_a = f"{fmt.format(mean_a)} ± {std_a:.2f}"
        str_b = f"{fmt.format(mean_b)} ± {std_b:.2f}"
        print(f"| {name} | {str_a} | {str_b} | {delta_str} |")


def main():
    parser = argparse.ArgumentParser(description="Steam Deck Benchmark Driver")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_push = sub.add_parser("push")
    p_push.add_argument("appimage", help="Path to local AppImage")
    p_push.add_argument("label", help="Build label (e.g. integrate-b6cf83d)")
    p_push.set_defaults(func=cmd_push)

    p_run = sub.add_parser("run")
    p_run.add_argument("label", help="Build label")
    p_run.add_argument("scene", help="Scene name (e.g. u1-continue)")
    p_run.add_argument("--mode", choices=["30", "60", "uncapped"], default="30", help="Vsync/vblank mode")
    p_run.add_argument("--seconds", type=int, default=60, help="Measurement window seconds")
    p_run.add_argument("--repeat", type=int, default=3, help="Number of repeat runs")
    p_run.set_defaults(func=cmd_run)

    p_comp = sub.add_parser("compare")
    p_comp.add_argument("labelA", help="Baseline label")
    p_comp.add_argument("labelB", help="Test label")
    p_comp.add_argument("scene", help="Scene name")
    p_comp.add_argument("--mode", choices=["30", "60", "uncapped"], default="30", help="Vsync/vblank mode")
    p_comp.set_defaults(func=cmd_compare)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

# Steam Deck Benchmark Tools (`tools/bench`)

Automated, repeatable benchmark harness for the Steam Deck, driving the Uncharted: The Nathan Drake Collection Linux port over SSH from a laptop.

## Architecture & Requirements

- Driven over SSH from laptop using standard Python 3 (stdlib only).
- Game runs on the Deck under a `systemd` user unit (`unch-nav.service`) so that background processes persist and clean up reliably regardless of SSH logind sessions.
- Input injection uses `BB_PAD_FILE` to deterministically navigate menus, load saves, and perform camera loops.
- Frame capture and validation use `BB_FRAME_DUMP_TRIGGER` and `BB_DUMP_DIR` to record screenshots at navigation steps and at measurement window boundaries (`measure_start.png`, `measure_end.png`).
- Gameplay proof: every run requires visual proof (screenshot thumbnail within expected brightness/features) plus log verification before measurement begins.
- Warm shader cache: each build executes a warm-up run first; compiled shader artifacts in `user/cache/` are preserved and seeded into subsequent runs to prevent first-run compilation hitches from skewing metrics.

## CLI Usage

Run from the laptop:

### 1. Push an AppImage
Transfers and extracts an AppImage to `~/unch-bench/builds/<label>/` on the Deck:
```sh
tools/bench/deck_bench.py push /path/to/Uncharted-x86_64.AppImage integrate-b6cf83d
```

### 2. Run Benchmarks
Runs scripted benchmark on the Deck, collects logs, frame stats, and screenshots back to `/var/home/vishwas/src/bench-results/<label>/<scene>/<mode>/`:
```sh
# Deck mode 30 Hz (3 repeats, 60s measurement window)
tools/bench/deck_bench.py run baseline-sp87 u1-continue --mode 30 --repeat 3 --seconds 60

# Deck mode 60 Hz
tools/bench/deck_bench.py run baseline-sp87 u1-continue --mode 60 --repeat 3 --seconds 60

# Uncapped throughput (NAV_HZ=240, NAV_PRESENT=Immediate)
tools/bench/deck_bench.py run baseline-sp87 u1-continue --mode uncapped --repeat 3 --seconds 60
```

### 3. A/B Compare Builds
Compares two build labels and outputs markdown summary table with mean ± stdev and % delta:
```sh
tools/bench/deck_bench.py compare baseline-sp87 integrate-b6cf83d u1-continue --mode 30
tools/bench/deck_bench.py compare baseline-sp87 integrate-b6cf83d u1-continue --mode uncapped
```

## Deck Filesystem Organization
All benchmark files on the Deck reside strictly under `~/unch-bench/`:
- `~/unch-bench/builds/<label>/`: extracted AppImage build trees.
- `~/unch-bench/seed/`: user's port-format saves (mounted read-only into scratch user dirs).
- `~/unch-bench/warm-cache/<label>/`: precompiled shader/pipeline caches for the build.
- `~/unch-bench/work-<label>-<scene>-<mode>-r<N>/`: isolated scratch run directories (`user/`, `shots/`, `dump/`, `game.log`, `window.log`).
- `~/unch-bench/deckctl.py` and `~/unch-bench/bench_deck.py`: Deck-side unit control and sequence driver.

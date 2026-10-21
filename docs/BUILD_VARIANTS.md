# Build Variants and Optimization Guide

This document describes build options and the Profile-Guided Optimization (PGO) workflow for optimizing the Uncharted native port for the Steam Deck (AMD Zen 2).

---

## Build Options

All options are controlled via environment variables passed to `build.sh` or CMake:

| Variable | Values | Default | Description |
|---|---|---|---|
| `BB_MARCH` | `znver2`, `x86-64-v3`, etc. | *(unset, generic)* | Sets `-march=` and `-mtune=` across bbgpu, bb-probe, and bundled static libraries. |
| `BB_OPT` | `-O2`, `-O3` | `-O2` | Optimization level for `bb-probe` and HLE runtime code. |
| `BB_PGO` | `off`, `generate`, `use` | `use` if `pgo/*.gcda` exists, else `off` | GCC Profile-Guided Optimization mode. |
| `BB_LTO` | `ON`, `OFF` | `ON` | Link-Time Optimization (interprocedural optimization). |
| `BB_JOBS` | integer (e.g. `6`) | auto | Parallel ninja build jobs. |

### Example Build Commands
```sh
# Generic build (portable across x86-64 Linux PCs)
bash build.sh

# Steam Deck Zen 2 optimized build
BB_MARCH=znver2 bash build.sh

# Steam Deck Zen 2 with PGO instrumentation
BB_MARCH=znver2 BB_PGO=generate bash build.sh

# Steam Deck Zen 2 using committed PGO profile
BB_MARCH=znver2 BB_PGO=use bash build.sh
```

---

## PGO Training on the Steam Deck

GCC's instrumentation embeds the build-time profile directory (`<repo>/pgo`, or `/src/pgo` inside build containers) into binaries. When running on the Steam Deck, use GCC's runtime environment variables:

- `GCOV_PREFIX`: Target directory where `.gcda` counter files should be written (e.g. `/home/deck/unch-bench/pgo/<label>`).
- `GCOV_PREFIX_STRIP`: Number of leading directory components to strip from the compiled path. For container builds targeting `/src/pgo`, set `GCOV_PREFIX_STRIP=2` to strip `/src` and `/pgo`.

### Step-by-Step Workflow

1. **Build Instrumented AppImage**:
   ```sh
   BB_MARCH=znver2 BB_PGO=generate bash build.sh
   LINUX_BUILD=dev bash packaging/linux/package.sh
   # Produces dist/Uncharted-x86_64-znver2-pgo-generate.AppImage
   ```

2. **Run Benchmark on the Steam Deck**:
   Transfer the AppImage to the Deck, then launch with profile destination:
   ```sh
   export GCOV_PREFIX=/home/deck/unch-bench/pgo/run1
   export GCOV_PREFIX_STRIP=2
   ./Uncharted-x86_64-znver2-pgo-generate.AppImage
   ```
   Counters will be generated under `/home/deck/unch-bench/pgo/run1/`.

3. **Collect Profiles into Worktree**:
   From the development workstation, run:
   ```sh
   tools/pgo/collect.sh run1
   ```
   This synchronizes `.gcda` files from `deck@192.168.20.254:~/unch-bench/pgo/run1/` into `pgo/`.

4. **Build Optimized Shipped Variant**:
   ```sh
   BB_MARCH=znver2 BB_PGO=use bash build.sh
   ```

---

## Path-Independent Profiles

GCC $\ge$ 12 provides `-fprofile-prefix-path=<root>`. This build system automatically passes the project root to `-fprofile-prefix-path` during both `generate` and `use` stages.

This strips the absolute workspace path from mangled `.gcda` object filenames, ensuring profiles trained inside containers (`/src`), on the Deck, or in local directories can be consumed identically by GitHub Actions CI running at different checkout paths (e.g. `/__w/...`).

---

## Packaging & Artifacts

`packaging/linux/package.sh` automatically tags the package variant:
- Standard AppImage: `dist/Uncharted-x86_64.AppImage` (always created for compatibility and releases)
- Variant-tagged AppImage: `dist/Uncharted-x86_64-${variant}.AppImage` (e.g. `-znver2`, `-znver2-pgo`)
- `LINUX_BUILD` inside the package includes the variant identifier.

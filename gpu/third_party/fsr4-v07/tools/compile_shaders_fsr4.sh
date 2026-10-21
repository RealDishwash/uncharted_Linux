#!/usr/bin/env bash
# Compile the source-bearing FSR4 v07 INT8 model sources to Vulkan SPIR-V.
#
# Usage: compile_shaders_fsr4.sh <sdk_root> [preset] [output_dir]
#
# Environment:
#   DXC_BIN       native dxc or Windows dxc.exe (default: dxc)
#   WINE_BIN      Wine runner for dxc.exe (default: wine)
#   FSR4_DEBUG    1 for -O0 -Zi (default: optimized -O3)
#   FSR4_VALIDATE 0 to skip spirv-val and ABI reflection (default: 1)
#   FSR4_SPIRV_COMPAT 1 to replace SPIR-V dot intrinsics for old DXC builds
#
# The Q2RTX temporal contract for this fixed permutation is:
#   INT8/DOT4, non-WMMA, low-resolution motion vectors, forward linear view Z,
#   explicit exposure, linear color and jitter-free motion vectors.

set -euo pipefail

usage() {
    echo "Usage: $0 <fsr4_sdk_root> [preset] [output_dir]" >&2
    exit 2
}

die() {
    echo "Error: $*" >&2
    exit 1
}

[ "$#" -ge 1 ] || usage

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SDK="${1%/}"
PRESET="${2:-performance}"
OUT="${3:-./spv}"

case "$PRESET" in
    all) ;;
    native|quality|balanced|performance|drs|ultraperf) ;;
    *) die "unsupported INT8 preset '$PRESET'" ;;
esac

if [ ! -d "$SDK/Kits" ]; then
    candidate="$SDK"
    for _unused in 1 2 3 4 5; do
        candidate="$(dirname -- "$candidate")"
        if [ -d "$candidate/Kits" ]; then
            echo "Note: SDK root auto-detected as $candidate"
            SDK="${candidate%/}"
            break
        fi
    done
fi

# Build every fixed-ratio user preset in separate invocations so each run
# retains its own model-qualified SPIR-V, initializer and pass-0 weight blob.
# DRS deliberately remains opt-in: it needs a dedicated render-scale policy
# rather than merely another static model bundle.
if [ "$PRESET" = "all" ]; then
    for model_preset in native quality balanced performance ultraperf; do
        "$0" "$SDK" "$model_preset" "$OUT"
    done
    exit 0
fi

FSR4_ROOT="$SDK/Kits/FidelityFX/upscalers/fsr4"
API_ROOT="$SDK/Kits/FidelityFX/api/internal"
MODEL="fsr4_model_v07_i8_${PRESET}"
SHADER_DIR="$FSR4_ROOT/internal/shaders/$MODEL"
INITIALIZERS="$SHADER_DIR/initializers.bin"
PRE_SHADER="$SHADER_DIR/pre.hlsl"
WRAPPER="$SCRIPT_DIR/fsr4_spirv_wrapper.hlsl"
VALIDATOR="$SCRIPT_DIR/validate_fsr4_spirv.py"
PREPARE_SOURCES="$SCRIPT_DIR/prepare_fsr4_spirv_sources.py"

[ -d "$SHADER_DIR" ] || die "FSR4 INT8 shader directory not found: $SHADER_DIR"
[ -f "$INITIALIZERS" ] || die "model initializer not found: $INITIALIZERS"
[ "$(wc -c < "$INITIALIZERS")" -eq 89216 ] || die "initializer must be exactly 89216 bytes"
[ -f "$PREPARE_SOURCES" ] || die "SPIR-V source preparation helper not found: $PREPARE_SOURCES"
mkdir -p "$OUT"
OUT="$(CDPATH= cd -- "$OUT" && pwd)"

INCLUDE_OVERLAY="$(mktemp -d "${TMPDIR:-/tmp}/q2rtx-fsr4-includes.XXXXXX")"
cleanup() {
    find "$INCLUDE_OVERLAY" -depth -delete 2>/dev/null || true
}
trap cleanup EXIT
python3 "$PREPARE_SOURCES" "$FSR4_ROOT" "$INCLUDE_OVERLAY"

DXC_BIN="${DXC_BIN:-dxc}"
if [[ "$DXC_BIN" == */* ]]; then
    [ -f "$DXC_BIN" ] || die "DXC not found: $DXC_BIN"
    DXC_BIN="$(CDPATH= cd -- "$(dirname -- "$DXC_BIN")" && pwd)/$(basename -- "$DXC_BIN")"
else
    DXC_BIN="$(command -v "$DXC_BIN")" || die "DXC not found (set DXC_BIN)"
fi

WINDOWS_DXC=0
case "${DXC_BIN,,}" in
    *.exe) WINDOWS_DXC=1 ;;
esac

if [ "$WINDOWS_DXC" -eq 1 ]; then
    WINE_BIN="${WINE_BIN:-wine}"
    if [[ "$WINE_BIN" == */* ]]; then
        [ -x "$WINE_BIN" ] || die "Wine runner not executable: $WINE_BIN"
    else
        WINE_BIN="$(command -v "$WINE_BIN")" || die "Windows DXC requires Wine (set WINE_BIN)"
    fi
    command -v winepath >/dev/null 2>&1 || die "Windows DXC requires winepath"
    dxc_path() { WINEDEBUG=-all winepath -w "$1"; }
    DXC_CMD=(env WINEDEBUG=-all "$WINE_BIN" "$DXC_BIN")
else
    dxc_path() { printf '%s\n' "$1"; }
    DXC_CMD=("$DXC_BIN")
fi

DXC_VERSION="$("${DXC_CMD[@]}" --version 2>&1 | head -1)"
echo "Using DXC: $DXC_VERSION"
echo "FSR4 model: $MODEL"
echo "Q2 permutation: depth_inverted=0 low_res_mv=1 explicit model exposure; SPD auto exposure is runtime-selectable; colorspace=linear jittered_mv=0"

FSR4_SPIRV_COMPAT="${FSR4_SPIRV_COMPAT:-0}"
case "$FSR4_SPIRV_COMPAT" in
    0) ;;
    1) [ -f "$WRAPPER" ] || die "SPIR-V compatibility wrapper not found: $WRAPPER" ;;
    *) die "FSR4_SPIRV_COMPAT must be 0 or 1" ;;
esac

# SDK 1.1.4 carries DXC 1.7.2212. Its SPIR-V backend lacks dot2add and
# dot4add_i8packed, crashes compiling optimized pass 3 even with replacements,
# and cannot lower templates used by later model passes at -O0. Wine invocation
# and path conversion are supported, but that compiler cannot build this model.
if [[ "$DXC_VERSION" == *"1.7.2212"* ]] && [ "${FSR4_ALLOW_UNSUPPORTED_DXC:-0}" != "1" ]; then
    die "DXC 1.7.2212 cannot compile the FSR4 INT8 model; use the patched DirectXShaderCompiler build (or set FSR4_ALLOW_UNSUPPORTED_DXC=1 only for diagnostics)"
fi

if [ "${FSR4_DEBUG:-0}" = "1" ]; then
    echo "Debug shader build: -O0 -Zi"
    OPT_FLAGS=(-O0 -Zi)
else
    OPT_FLAGS=(-O3)
fi

BASE_FLAGS=(
    -spirv -T cs_6_4 -enable-16bit-types -HV 2021
    "${OPT_FLAGS[@]}"
    -fspv-target-env=vulkan1.3
    '-D_Static_assert(cond,msg)='
    -DFFX_HLSL=1
    -DFFX_GPU=1
    -DFFX_HLSL_SM=64
    -DFSR4_ENABLE_DOT4=1
    -DWMMA_ENABLED=0
    -DFFX_MLSR_DEPTH_INVERTED=0
    -DFFX_MLSR_LOW_RES_MV=1
    -DFFX_MLSR_AUTOEXPOSURE_ENABLED=0
    -DFFX_MLSR_COLORSPACE=0
    -DFFX_MLSR_JITTERED_MOTION_VECTORS=0
)

INCLUDE_FLAGS=(
    -I "$(dxc_path "$INCLUDE_OVERLAY")"
    -I "$(dxc_path "$SCRIPT_DIR")"
    -I "$(dxc_path "$FSR4_ROOT/dx12")"
    -I "$(dxc_path "$API_ROOT/dx12")"
    -I "$(dxc_path "$FSR4_ROOT/include/gpu/fsr4")"
    -I "$(dxc_path "$FSR4_ROOT/include/gpu")"
    -I "$(dxc_path "$FSR4_ROOT/internal/shaders")"
    -I "$(dxc_path "$SHADER_DIR")"
    -I "$(dxc_path "$API_ROOT/gpu")"
)

# t0/t1 -> 0/1; u0/u1 -> 2/3. ByteAddressBuffer resources reflect as
# STORAGE_BUFFER in Vulkan, irrespective of SRV/UAV origin.
MODEL_SHIFT_FLAGS=(-fvk-t-shift 0 0 -fvk-u-shift 2 0)

# Texture SRVs are separate SAMPLED_IMAGE descriptors. Keep s0 unique at 35;
# DXC does not merge a Texture2D and SamplerState that share binding zero.
FULL_SHIFT_FLAGS=(
    -fvk-t-shift 0 0
    -fvk-s-shift 35 0
    -fvk-u-shift 21 0
    -fvk-b-shift 43 0
)

# DXC cannot infer the intended R16/R8 storage formats from the typed HLSL
# resources. Use float resource types and StorageImageWriteWithoutFormat, as
# the original Vulkan conversion did. The runtime must enable that feature and
# bind the SDK-declared image formats.
DATA_FLAGS=(-DDATA_TYPE=float -DDATA_TYPE2=float2 -DDATA_TYPE3=float3 -DDATA_TYPE_VECTOR=float4)

compile_shader() {
    local source_name="$1"
    local source_path="$2"
    local entry="$3"
    local output="$4"
    shift 4
    if [ "$FSR4_SPIRV_COMPAT" = "1" ]; then
        "${DXC_CMD[@]}" "${BASE_FLAGS[@]}" "$@" "${INCLUDE_FLAGS[@]}" \
            "-DQ2RTX_FSR4_SHADER_SOURCE=\"$source_name\"" \
            -E "$entry" "$(dxc_path "$WRAPPER")" -Fo "$(dxc_path "$output")"
    else
        "${DXC_CMD[@]}" "${BASE_FLAGS[@]}" "$@" "${INCLUDE_FLAGS[@]}" \
            -E "$entry" "$(dxc_path "$source_path")" -Fo "$(dxc_path "$output")"
    fi
}

compile_model_resolution() {
    local resolution="$1"
    local resolution_id="$2"
    echo "Compiling ${resolution} model passes..."
    for pass in $(seq 1 12); do
        local output="$OUT/${MODEL}_${resolution}_pass${pass}.spv"
        echo "  pass $pass -> $(basename -- "$output")"
        compile_shader "passes_${resolution}.hlsl" \
            "$SHADER_DIR/passes_${resolution}.hlsl" \
            "fsr4_model_v07_i8_pass${pass}" "$output" \
            "${MODEL_SHIFT_FLAGS[@]}" \
            "-DFFX_MLSR_RESOLUTION=${resolution_id}" \
            "-DMLSR_PASS_${pass}=1"
    done

    echo "Compiling ${resolution} pre-pass..."
    compile_shader pre.hlsl "$SHADER_DIR/pre.hlsl" main \
        "$OUT/${MODEL}_${resolution}_pre.spv" \
        "${FULL_SHIFT_FLAGS[@]}" "${DATA_FLAGS[@]}" \
        "-DFFX_MLSR_RESOLUTION=${resolution_id}"

    echo "Compiling ${resolution} post-pass..."
    compile_shader post.hlsl "$SHADER_DIR/post.hlsl" main \
        "$OUT/${MODEL}_${resolution}_post.spv" \
        "${FULL_SHIFT_FLAGS[@]}" "${DATA_FLAGS[@]}" \
        "-DFFX_MLSR_RESOLUTION=${resolution_id}"
}

compile_model_resolution 1080 0
compile_model_resolution 2160 1
compile_model_resolution 4320 2

echo "Compiling RCAS..."
compile_shader rcas.hlsl "$FSR4_ROOT/internal/shaders/rcas.hlsl" main "$OUT/rcas.spv" \
    "${FULL_SHIFT_FLAGS[@]}" "${DATA_FLAGS[@]}"

echo "Compiling SPD auto-exposure..."
compile_shader spd_auto_exposure.hlsl \
    "$FSR4_ROOT/internal/shaders/spd_auto_exposure.hlsl" main \
    "$OUT/spd_auto_exposure.spv" \
    "${FULL_SHIFT_FLAGS[@]}" "${DATA_FLAGS[@]}"

echo "Extracting model assets..."
install -m 0644 "$INITIALIZERS" "$OUT/${MODEL}_initializers.bin"

# Extract the exact 256 uint32 pre-pass weights from the selected model. The
# SDK HLSL array is the source of truth; packing must be little-endian to match
# Vulkan's uint constant-buffer representation on supported hosts.
python3 - "$PRE_SHADER" "$OUT/${MODEL}_pre_weights.bin" <<'PY'
import re
import struct
import sys
from pathlib import Path

source = Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(
    r"embedded_encoder1_DownscaleStridedConv2x2_downscale_conv_weight_dwords\[256\]\s*=\s*\{(.*?)\};",
    source,
    re.DOTALL,
)
if match is None:
    raise SystemExit("could not find the 256-word pre-pass weight array")
words = [int(value, 16) for value in re.findall(r"0x[0-9a-fA-F]+", match.group(1))]
if len(words) != 256:
    raise SystemExit(f"expected 256 pre-pass weights, found {len(words)}")
Path(sys.argv[2]).write_bytes(struct.pack("<256I", *words))
PY

if [ "${FSR4_VALIDATE:-1}" != "0" ]; then
    command -v spirv-val >/dev/null 2>&1 || die "spirv-val is required (or set FSR4_VALIDATE=0)"
    command -v spirv-dis >/dev/null 2>&1 || die "spirv-dis is required (or set FSR4_VALIDATE=0)"
    echo "Validating all SPIR-V modules..."
    for shader in "$OUT"/${MODEL}_*.spv "$OUT"/rcas.spv "$OUT"/spd_auto_exposure.spv; do
        spirv-val --target-env vulkan1.3 "$shader"
    done
    python3 "$VALIDATOR" "$OUT" --preset "$PRESET" \
        --compiler "$DXC_VERSION" --write-manifest "$OUT/${MODEL}_shader_manifest.json"
fi

echo "Creating stable shader aliases..."
(
    cd "$OUT"
    # Runtime uses the canonical manifest names above, which are portable to
    # Windows checkouts without symlink privileges.  Preserve these symlinks
    # only as compatibility aliases for older local tooling.
    cp -f -- "${MODEL}_2160_pre.spv" "${MODEL}_pre.spv"
    cp -f -- "${MODEL}_2160_post.spv" "${MODEL}_post.spv"
    ln -sfn "${MODEL}_pre.spv" fsr4_pre.spv
    ln -sfn "${MODEL}_post.spv" fsr4_post.spv
    ln -sfn rcas.spv fsr4_rcas.spv
    ln -sfn spd_auto_exposure.spv fsr4_spd.spv
    for resolution in 1080 2160 4320; do
        ln -sfn "${MODEL}_${resolution}_pre.spv" "fsr4_${resolution}_pre.spv"
        ln -sfn "${MODEL}_${resolution}_post.spv" "fsr4_${resolution}_post.spv"
        for pass in $(seq 1 12); do
            ln -sfn "${MODEL}_${resolution}_pass${pass}.spv" \
                "fsr4_${resolution}_pass${pass}.spv"
        done
    done

    # Keep the original Performance names as a migration aid for existing
    # source trees.  The runtime selects the model-qualified files above, so
    # no preset can accidentally consume another preset's weights.
    if [ "$PRESET" = "performance" ]; then
        cp -f -- "${MODEL}_initializers.bin" fsr4_initializers.bin
        cp -f -- "${MODEL}_pre_weights.bin" fsr4_pre_weights.bin
        cp -f -- "${MODEL}_shader_manifest.json" fsr4_shader_manifest.json
    fi
)

echo "Done: $OUT"
echo "  Model assets: ${MODEL}_initializers.bin (89216 bytes), ${MODEL}_pre_weights.bin (1024 bytes)"
echo "  Model ABI: storage buffers at 0..3; initializer is binding 1 in passes 6..9"
echo "  Resolution ABI: fsr4_{1080,2160,4320}_{pre,post}.spv must match the selected model-pass tag"
echo "  Full ABI: sampled images 0..20; storage resources 21..33; weights 34; sampler 35; constants 43"
echo "  Required shader features: float16, int8, integer DOT4, Int16, storage-image extended formats/write-without-format, compute derivative group linear"

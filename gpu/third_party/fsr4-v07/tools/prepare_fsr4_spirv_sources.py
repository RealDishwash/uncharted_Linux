#!/usr/bin/env python3
"""Create a temporary FSR4 include overlay with explicit Vulkan image formats.

The SDK's HLSL relies on DXIL's typed-UAV format inference.  Vulkan stores the
format in SPIR-V instead, and DXC otherwise emits either Unknown or Rgba32f for
the float aliases used by the INT8 model.  The overlay leaves the SDK tree
untouched while adding the formats from the provider's resource descriptions.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


IMAGE_FORMATS = {
    "rw_upsampled_color": "rgba16f",
    "rw_history_color": "rgba16f",
    "rw_mlsr_output_color": "rgba16f",
    "rw_reprojected_color": "rgba16f",
    "rw_recurrent_0": "rgba8",
    "rw_recurrent_1": "rgba8",
    "rw_sr_mv_dilated": "rg16f",
    "rw_auto_exposure_texture": "r32f",
    "rw_output_color_for_rcas": "rgba16f",
    "rw_rcas_output": "rgba16f",
    "rw_debug_visualization": "r32f",
}

# DXIL permits a float3 typed UAV declaration to target an RGBA resource and
# implicitly leaves/fills the fourth channel.  Vulkan's SPIR-V environment is
# stricter: OpImageWrite to an Rgba* image must carry at least four components.
# Keep the provider's RGBA formats, but widen only those write declarations in
# the temporary overlay.  Read declarations remain float3, matching shader use.
RGBA_WRITE_TYPES = {
    "rw_upsampled_color": "float4",
    "rw_history_color": "float4",
    "rw_mlsr_output_color": "float4",
    "rw_reprojected_color": "DATA_TYPE_VECTOR",
    "rw_output_color_for_rcas": "DATA_TYPE_VECTOR",
    "rw_rcas_output": "float4",
}


def patch_resource_header(source: str) -> str:
    for name, image_format in IMAGE_FORMATS.items():
        replacement_type = RGBA_WRITE_TYPES.get(name)
        pattern = re.compile(
            rf"^(\s*)RWTexture2D<(?P<type>[^\n]+?)>(?P<tail>\s+{re.escape(name)}\s*:[^\n]+)$",
            re.MULTILINE,
        )
        def replace(match: re.Match[str]) -> str:
            resource_type = replacement_type or match.group("type")
            return (
                f'{match.group(1)}[[vk::image_format("{image_format}")]] '
                f'RWTexture2D<{resource_type}>{match.group("tail")}'
            )

        source, replacements = pattern.subn(replace, source)
        if replacements != 1:
            raise ValueError(
                f"expected one declaration for {name}, found {replacements}; "
                "the SDK resource header changed"
            )
    return source


def replace_once(source: str, old: str, new: str, filename: str) -> str:
    replacements = source.count(old)
    if replacements != 1:
        raise ValueError(
            f"expected one '{old}' in {filename}, found {replacements}; "
            "the SDK shader source changed"
        )
    return source.replace(old, new)


def patch_shader_source(filename: str, source: str) -> str:
    if filename == "pre_common.hlsli":
        return replace_once(
            source,
            "rw_reprojected_color[dtid.xy] = DATA_TYPE3(history);",
            "rw_reprojected_color[dtid.xy] = DATA_TYPE_VECTOR(history, 0.0f);",
            filename,
        )
    if filename == "post_common.hlsli":
        source = replace_once(
            source,
            "rw_history_color[tid] = model_color;",
            "rw_history_color[tid] = float4(model_color, 0.0f);",
            filename,
        )
        return replace_once(
            source,
            "rw_mlsr_output_color[tid] = final_color;",
            "rw_mlsr_output_color[tid] = float4(final_color, 0.0f);",
            filename,
        )
    if filename == "mlsr_optimized_includes.hlsli":
        return replace_once(
            source,
            "rw_mlsr_output_color[iPxPos] = color;",
            "rw_mlsr_output_color[iPxPos] = float4(color, 0.0f);",
            filename,
        )
    return source


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fsr4_root", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()

    gpu_dir = args.fsr4_root / "include" / "gpu" / "fsr4"
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    (output / "fsr4").mkdir(exist_ok=True)

    for filename in (
        "pre_common.hlsli",
        "post_common.hlsli",
        "mlsr_optimized_includes.hlsli",
    ):
        source = (gpu_dir / filename).read_text(encoding="utf-8")
        source = patch_shader_source(filename, source)
        (output / filename).write_text(source, encoding="utf-8")

    resource_header = (gpu_dir / "ffx_fsr4_upscale_resources.h").read_text(
        encoding="utf-8"
    )
    resource_header = patch_resource_header(resource_header)
    (output / "ffx_fsr4_upscale_resources.h").write_text(
        resource_header, encoding="utf-8"
    )
    # RCAS includes the same file through the include/gpu root ("fsr4/...").
    (output / "fsr4" / "ffx_fsr4_upscale_resources.h").write_text(
        resource_header, encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

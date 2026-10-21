#!/usr/bin/env python3
"""Validate the Vulkan descriptor ABI of Q2RTX's FSR4 shader set.

Only Python's standard library and spirv-dis are required.  This intentionally
checks the SPIR-V actually emitted by DXC instead of trusting source registers
or compiler command lines.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


MODEL_NAME = "fsr4_model_v07_i8_{preset}"
RESOLUTIONS = (1080, 2160, 4320)


@dataclass(frozen=True)
class Resource:
    name: str
    descriptor_set: int
    binding: int
    descriptor_type: str
    image_format: str | None = None


@dataclass(frozen=True)
class Module:
    path: Path
    entry_point: str
    local_size: tuple[int, int, int]
    capabilities: frozenset[str]
    extensions: frozenset[str]
    resources: dict[str, Resource]


def fail(message: str) -> None:
    raise ValueError(message)


def disassemble(path: Path) -> str:
    result = subprocess.run(
        ["spirv-dis", str(path), "-o", "-"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        fail(f"{path.name}: spirv-dis failed: {result.stderr.strip()}")
    return result.stdout


def parse_module(path: Path) -> Module:
    text = disassemble(path)
    names: dict[str, str] = {}
    descriptor_sets: dict[str, int] = {}
    bindings: dict[str, int] = {}
    variable_types: dict[str, str] = {}
    pointer_types: dict[str, tuple[str, str]] = {}
    image_types: dict[str, tuple[int, str]] = {}
    vector_types: dict[str, int] = {}
    result_types: dict[str, str] = {}
    image_writes: list[tuple[str, str]] = []
    struct_types: set[str] = set()
    capabilities: set[str] = set()
    extensions: set[str] = set()
    entry_point = ""
    local_size = (0, 0, 0)

    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        if fields[0] == "OpCapability" and len(fields) >= 2:
            capabilities.add(fields[1])
        elif fields[0] == "OpExtension" and len(fields) >= 2:
            extensions.add(fields[1].strip('"'))
        elif fields[0] == "OpEntryPoint" and len(fields) >= 4:
            entry_point = fields[3].strip('"')
        elif fields[0] == "OpExecutionMode" and "LocalSize" in fields:
            pos = fields.index("LocalSize")
            local_size = tuple(map(int, fields[pos + 1 : pos + 4]))
        elif fields[0] == "OpName" and len(fields) >= 3:
            names[fields[1]] = fields[2].strip('"')
        elif fields[0] == "OpDecorate" and len(fields) >= 4 and fields[2] == "Binding":
            bindings[fields[1]] = int(fields[3])
        elif fields[0] == "OpDecorate" and len(fields) >= 4 and fields[2] == "DescriptorSet":
            descriptor_sets[fields[1]] = int(fields[3])
        elif len(fields) >= 5 and fields[1] == "=" and fields[2] == "OpVariable":
            variable_types[fields[0]] = fields[3]
        elif len(fields) >= 5 and fields[1] == "=" and fields[2] == "OpTypePointer":
            pointer_types[fields[0]] = (fields[3], fields[4])
        elif len(fields) >= 10 and fields[1] == "=" and fields[2] == "OpTypeImage":
            image_types[fields[0]] = (int(fields[8]), fields[9])
        elif len(fields) >= 5 and fields[1] == "=" and fields[2] == "OpTypeVector":
            vector_types[fields[0]] = int(fields[4])
        elif len(fields) >= 3 and fields[1] == "=" and fields[2] == "OpTypeStruct":
            struct_types.add(fields[0])
        if len(fields) >= 4 and fields[1] == "=" and not fields[2].startswith("OpType"):
            # For ordinary result-producing instructions, operand zero is the
            # result type.  This is enough to trace the image and texel values
            # consumed by OpImageWrite below.
            result_types[fields[0]] = fields[3]
        if fields[0] == "OpImageWrite" and len(fields) >= 4:
            image_writes.append((fields[1], fields[3]))

    format_components = {
        "Rgba16f": 4,
        "Rgba8": 4,
        "R32ui": 1,
        "R32f": 1,
    }
    for image_value, texel_value in image_writes:
        image_type = result_types.get(image_value)
        texel_type = result_types.get(texel_value)
        if image_type not in image_types or texel_type is None:
            fail(
                f"{path.name}: cannot resolve OpImageWrite operand types "
                f"({image_value}, {texel_value})"
            )
        image_format = image_types[image_type][1]
        required_components = format_components.get(image_format)
        if required_components is None or image_format == "Unknown":
            continue
        actual_components = vector_types.get(texel_type, 1)
        if actual_components < required_components:
            fail(
                f"{path.name}: OpImageWrite to {image_format} carries "
                f"{actual_components} texel components; expected at least "
                f"{required_components}"
            )

    resources: dict[str, Resource] = {}
    for variable_id, binding in bindings.items():
        if variable_id not in names or variable_id not in variable_types:
            continue
        storage_class, pointee = pointer_types.get(variable_types[variable_id], ("", ""))
        if pointee in image_types:
            sampled, image_format = image_types[pointee]
            descriptor_type = "storage_image" if sampled == 2 else "sampled_image"
        elif pointee in struct_types:
            if storage_class == "Uniform":
                descriptor_type = "uniform_buffer"
            elif storage_class == "StorageBuffer":
                descriptor_type = "storage_buffer"
            else:
                descriptor_type = storage_class.lower()
            image_format = None
        else:
            # Samplers are the only non-image UniformConstant in this shader set.
            descriptor_type = "sampler" if storage_class == "UniformConstant" else storage_class.lower()
            image_format = None
        name = names[variable_id]
        descriptor_set = descriptor_sets.get(variable_id)
        if descriptor_set is None:
            fail(f"{path.name}: {name} has a binding but no DescriptorSet decoration")
        resources[name] = Resource(
            name, descriptor_set, binding, descriptor_type, image_format
        )

    if not entry_point or local_size == (0, 0, 0):
        fail(f"{path.name}: missing compute entry point or LocalSize")

    occupied: dict[tuple[int, int], str] = {}
    for resource in resources.values():
        slot = (resource.descriptor_set, resource.binding)
        previous = occupied.get(slot)
        if previous is not None:
            fail(
                f"{path.name}: descriptor collision at set {slot[0]} binding "
                f"{slot[1]} between {previous} and {resource.name}"
            )
        occupied[slot] = resource.name
        if resource.descriptor_set != 0:
            fail(
                f"{path.name}: {resource.name} uses descriptor set "
                f"{resource.descriptor_set}; Q2RTX requires set 0"
            )
    return Module(path, entry_point, local_size, frozenset(capabilities), frozenset(extensions), resources)


def expect_resource(
    module: Module,
    name: str,
    binding: int,
    descriptor_type: str,
    image_format: str | None = None,
) -> None:
    resource = module.resources.get(name)
    if resource is None:
        fail(f"{module.path.name}: missing resource {name}")
    if (resource.descriptor_set, resource.binding, resource.descriptor_type) != (
        0,
        binding,
        descriptor_type,
    ):
        fail(
            f"{module.path.name}: {name} is set {resource.descriptor_set} "
            f"binding {resource.binding} {resource.descriptor_type}; expected "
            f"set 0 binding {binding} {descriptor_type}"
        )
    if image_format is not None and resource.image_format != image_format:
        fail(
            f"{module.path.name}: {name} has image format "
            f"{resource.image_format}; expected {image_format}"
        )


def expect_common_int8(module: Module) -> None:
    required_caps = {"Shader", "Float16", "Int8"}
    missing = required_caps - module.capabilities
    if missing:
        fail(f"{module.path.name}: missing capabilities {sorted(missing)}")


def validate(directory: Path, preset: str) -> list[Module]:
    model = MODEL_NAME.format(preset=preset)
    paths = [
        directory / f"{model}_{resolution}_pass{number}.spv"
        for resolution in RESOLUTIONS
        for number in range(1, 13)
    ]
    paths += [
        directory / f"{model}_{resolution}_{stage}.spv"
        for resolution in RESOLUTIONS
        for stage in ("pre", "post")
    ]
    paths += [directory / "rcas.spv", directory / "spd_auto_exposure.spv"]
    missing = [path.name for path in paths if not path.is_file()]
    if missing:
        fail(f"missing shader artifacts: {', '.join(missing)}")

    assets = {
        f"{model}_initializers.bin": 89216,
        f"{model}_pre_weights.bin": 1024,
    }
    for filename, expected_size in assets.items():
        path = directory / filename
        if not path.is_file():
            fail(f"missing model asset: {filename}")
        if path.stat().st_size != expected_size:
            fail(
                f"{filename}: size is {path.stat().st_size}; expected "
                f"{expected_size} bytes"
            )

    modules = [parse_module(path) for path in paths]
    model_modules = modules[:36]
    resolution_modules = modules[36:42]
    rcas, spd = modules[42:]

    for index, module in enumerate(model_modules):
        pass_number = index % 12 + 1
        expected_entry = f"fsr4_model_v07_i8_pass{pass_number}"
        expected_local = (8, 8, 1) if pass_number == 9 else (64, 1, 1)
        if module.entry_point != expected_entry or module.local_size != expected_local:
            fail(f"{module.path.name}: entry/local size is {module.entry_point} {module.local_size}; expected {expected_entry} {expected_local}")
        expect_common_int8(module)
        required_dot_caps = {"DotProduct", "DotProductInput4x8BitPacked"}
        if not required_dot_caps <= module.capabilities or "SPV_KHR_integer_dot_product" not in module.extensions:
            fail(f"{module.path.name}: INT8/DOT4 shader lacks SPV_KHR_integer_dot_product")
        expect_resource(module, "ScratchBuffer", 3, "storage_buffer")
        if pass_number in (6, 7, 8, 9):
            expect_resource(module, "InitializerBuffer", 1, "storage_buffer")

    for resolution_index, resolution in enumerate(RESOLUTIONS):
        pre, post = resolution_modules[resolution_index * 2 : resolution_index * 2 + 2]

        if pre.entry_point != "main" or pre.local_size != (256, 1, 1):
            fail(f"{pre.path.name}: unexpected pre-pass entry/local size")
        expect_common_int8(pre)
        for name, binding, kind, image_format in (
            ("g_history_sampler", 35, "sampler", None),
            ("r_history_color", 0, "sampled_image", None),
            ("r_velocity", 1, "sampled_image", None),
            ("r_depth", 2, "sampled_image", None),
            ("r_input_color", 3, "sampled_image", None),
            ("r_recurrent_0", 4, "sampled_image", None),
            ("r_input_exposure", 6, "sampled_image", None),
            ("rw_reprojected_color", 24, "storage_image", "Rgba16f"),
            ("ScratchBuffer", 32, "storage_buffer", None),
            ("cbPass_Weights", 34, "uniform_buffer", None),
            ("MLSR_Optimized_Constants", 43, "uniform_buffer", None),
        ):
            expect_resource(pre, name, binding, kind, image_format)

        if post.entry_point != "main" or post.local_size != (8, 8, 1):
            fail(f"{post.path.name}: unexpected post-pass entry/local size")
        expect_common_int8(post)
        required_dot_caps = {"DotProduct", "DotProductInput4x8BitPacked"}
        if (
            not required_dot_caps <= post.capabilities
            or "SPV_KHR_integer_dot_product" not in post.extensions
        ):
            fail(f"{post.path.name}: post-pass is not the INT8/DOT4 variant")
        for name, binding, kind, image_format in (
            ("r_input_color", 3, "sampled_image", None),
            ("r_input_exposure", 6, "sampled_image", None),
            ("r_reprojected_color", 9, "sampled_image", None),
            ("rw_history_color", 22, "storage_image", "Rgba16f"),
            ("rw_mlsr_output_color", 23, "storage_image", "Rgba16f"),
            ("rw_recurrent_0", 27, "storage_image", "Rgba8"),
            ("ScratchBuffer", 32, "storage_buffer", None),
            ("MLSR_Optimized_Constants", 43, "uniform_buffer", None),
        ):
            expect_resource(post, name, binding, kind, image_format)

    if rcas.local_size != (64, 1, 1):
        fail(f"{rcas.path.name}: unexpected RCAS LocalSize {rcas.local_size}")
    for name, binding, kind, image_format in (
        ("r_input_exposure", 6, "sampled_image", None),
        ("r_rcas_input", 18, "sampled_image", None),
        ("rw_rcas_output", 32, "storage_image", "Rgba16f"),
        ("cbRCAS", 43, "uniform_buffer", None),
    ):
        expect_resource(rcas, name, binding, kind, image_format)

    if spd.local_size != (256, 1, 1):
        fail(f"{spd.path.name}: unexpected SPD LocalSize {spd.local_size}")
    for name, binding, kind, image_format in (
        ("r_input_color", 0, "sampled_image", None),
        ("rw_spd_global_atomic", 21, "storage_image", "R32ui"),
        ("rw_autoexp_mip_5", 22, "storage_image", "R32f"),
        ("rw_auto_exposure_texture", 23, "storage_image", "R32f"),
        ("AutoExposureSPDConstants", 43, "uniform_buffer", None),
    ):
        expect_resource(spd, name, binding, kind, image_format)

    return modules


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_manifest(
    path: Path, directory: Path, preset: str, compiler: str, modules: list[Module]
) -> None:
    artifacts: dict[str, object] = {}
    for module in modules:
        artifacts[module.path.name] = {
            "size": module.path.stat().st_size,
            "sha256": file_digest(module.path),
            "entry_point": module.entry_point,
            "local_size": list(module.local_size),
            "capabilities": sorted(module.capabilities),
            "extensions": sorted(module.extensions),
            "resources": [
                {
                    "name": resource.name,
                    "set": resource.descriptor_set,
                    "binding": resource.binding,
                    "type": resource.descriptor_type,
                    **(
                        {"image_format": resource.image_format}
                        if resource.image_format is not None
                        else {}
                    ),
                }
                for resource in sorted(
                    module.resources.values(),
                    key=lambda item: (item.descriptor_set, item.binding, item.name),
                )
            ],
        }
    model = MODEL_NAME.format(preset=preset)
    for filename in (f"{model}_initializers.bin", f"{model}_pre_weights.bin"):
        asset = directory / filename
        artifacts[filename] = {
            "size": asset.stat().st_size,
            "sha256": file_digest(asset),
        }

    manifest = {
        "schema_version": 1,
        "model": MODEL_NAME.format(preset=preset),
        "preset": preset,
        "compiler": compiler,
        "permutation": {
            "FSR4_ENABLE_DOT4": 1,
            "WMMA_ENABLED": 0,
            "FFX_MLSR_DEPTH_INVERTED": 0,
            "FFX_MLSR_LOW_RES_MV": 1,
            "FFX_MLSR_AUTOEXPOSURE_ENABLED": 0,
            "FFX_MLSR_COLORSPACE": 0,
            "FFX_MLSR_JITTERED_MOTION_VECTORS": 0,
        },
        "sampler_binding": 35,
        "artifacts": artifacts,
    }
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("shader_dir", type=Path)
    parser.add_argument("--preset", default="performance")
    parser.add_argument("--compiler", default="unknown")
    parser.add_argument("--write-manifest", type=Path)
    args = parser.parse_args()
    if shutil.which("spirv-dis") is None:
        print("error: spirv-dis is required", file=sys.stderr)
        return 2
    try:
        modules = validate(args.shader_dir, args.preset)
        if args.write_manifest is not None:
            write_manifest(
                args.write_manifest,
                args.shader_dir,
                args.preset,
                args.compiler,
                modules,
            )
    except ValueError as error:
        print(f"FSR4 SPIR-V ABI validation failed: {error}", file=sys.stderr)
        return 1
    print(f"FSR4 SPIR-V ABI validation passed: {len(modules)} modules ({args.preset})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

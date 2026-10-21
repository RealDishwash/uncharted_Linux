"""Availability of the FSR 4 (v07) and FSR 4.1.1 models required by a selected output/preset."""
import os
from pathlib import Path
from bbport_i18n import tr


def fsr411_problem(directory: Path, output: str, preset: int) -> str | None:
    w, h = map(int, output.split("x"))
    model = ("t2160" if w > 1920 or h > 1080 else "t1080") + ("_m1" if preset == 4 else "_m0")
    passes = ["spd", "prepass", "pass0_post"]
    for i in range(1, 13):
        passes.extend([f"pass{i}", f"pass{i}_post"])
    passes.extend(["postpass", "rcas"])
    for name in passes:
        path = directory / model / (name + ".spv")
        if not path.is_file() or path.stat().st_size < 20 or path.stat().st_size % 4:
            return tr("Нет или повреждён файл {}").format(f"{model}/{name}.spv")
    path = directory / model / "initializer.bin"
    if not path.is_file() or path.stat().st_size != 131072:
        return tr("Нет или повреждён файл {}").format(f"{model}/initializer.bin")
    return None




def fsr4_problem(directory: Path, output: str = "1920x1080", preset: int = 0) -> str | None:
    w, h = map(int, str(output).lower().split("x"))
    if w > 3840 or h > 2160:
        tier = "4320"
    elif w > 1920 or h > 1080:
        tier = "2160"
    else:
        tier = "1080"

    # Match vk_fsr4.cpp's ModelPreset: all remaining supported UI presets use Ultra Performance.
    model = {0: "native", 1: "quality", 2: "balanced", 3: "performance"}.get(preset, "ultraperf")

    passes = [
        "rcas.spv",
        "spd_auto_exposure.spv",
        f"fsr4_model_v07_i8_{model}_{tier}_pre.spv",
        f"fsr4_model_v07_i8_{model}_{tier}_post.spv",
    ]
    for i in range(1, 13):
        passes.append(f"fsr4_model_v07_i8_{model}_{tier}_pass{i}.spv")

    for name in passes:
        optimized = directory / "opt" / name
        path = optimized if os.environ.get("BB_FSR4_OPT") != "0" and optimized.is_file() else directory / name
        if not path.is_file() or path.stat().st_size < 20 or path.stat().st_size % 4:
            return tr("Нет или повреждён файл {}").format(name)

    init_name = f"fsr4_model_v07_i8_{model}_initializers.bin"
    init_path = directory / init_name
    if not init_path.is_file() or init_path.stat().st_size != 89216:
        return tr("Нет или повреждён файл {}").format(init_name)

    weights_name = f"fsr4_model_v07_i8_{model}_pre_weights.bin"
    weights_path = directory / weights_name
    if not weights_path.is_file() or weights_path.stat().st_size != 1024:
        return tr("Нет или повреждён файл {}").format(weights_name)

    return None

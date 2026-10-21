#!/usr/bin/env python3
"""Build and stage the open FSR 4 v07 INT8 assets; never fetch shader binaries."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'gpu/third_party/fsr4-v07'
ASSETS = ROOT / 'fsr4_shaders'
MODELS = ('native', 'quality', 'balanced', 'performance', 'ultraperf', 'drs')
TIERS = (1080, 2160, 4320)
DXC_VERSION = '1.9.2609'
DXC_ARCHIVE = 'linux_dxc_2026_09_28.x86_x64.tar.gz'
DXC_URL = f'https://github.com/microsoft/DirectXShaderCompiler/releases/download/v{DXC_VERSION}/{DXC_ARCHIVE}'
DXC_SHA256 = '96faadc7f5c282d2ffda49804beb4c3ee38127bc252b723234e3c5cdf7aa39a1'


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def expected_assets():
    names = {'rcas.spv', 'spd_auto_exposure.spv'}
    for model in MODELS:
        prefix = f'fsr4_model_v07_i8_{model}'
        names.update(f'{prefix}_{suffix}' for suffix in
                     ('initializers.bin', 'pre_weights.bin', 'shader_manifest.json'))
        for tier in TIERS:
            names.update(f'{prefix}_{tier}_{suffix}.spv' for suffix in
                         ('pre', 'post', *(f'pass{i}' for i in range(1, 13))))
    return names


def source_digest():
    digest = hashlib.sha256()
    inputs = (p for p in VENDOR.rglob('*') if p.is_file() and
              (p.suffix in ('.h', '.hlsl', '.hlsli', '.bin', '.py', '.sh') or p.name == 'SOURCE.json'))
    for path in sorted(inputs):
        digest.update(path.relative_to(ROOT).as_posix().encode() + b'\0')
        digest.update(bytes.fromhex(sha256(path)))
    digest.update(Path(__file__).read_bytes())
    digest.update(DXC_SHA256.encode())
    return digest.hexdigest()


def verify_assets(directory):
    directory = Path(directory)
    manifest = json.loads((directory / 'build.json').read_text())
    if manifest.get('format') != 1 or set(manifest['files']) != expected_assets():
        raise ValueError('FSR 4 asset inventory does not match the v07 provider')
    if manifest['source_sha256'] != source_digest():
        raise ValueError('FSR 4 shaders are stale: run bash build.sh')
    for name, record in manifest['files'].items():
        path = directory / name
        if path.is_symlink() or path.stat().st_size != record['size'] or sha256(path) != record['sha256']:
            raise ValueError(f'FSR 4 asset checksum mismatch: {name}')
    provenance = json.loads((VENDOR / 'SOURCE.json').read_text())
    for model, pins in provenance['weights'].items():
        for suffix, pin in (('initializers.bin', 'initializers_sha256'),
                            ('pre_weights.bin', 'pre_weights_sha256')):
            name = f'fsr4_model_v07_i8_{model}_{suffix}'
            if manifest['files'][name]['sha256'] != pins[pin]:
                raise ValueError(f'FSR 4 model weights differ from the pinned source: {name}')
    return manifest


def compiler():
    """Install the hash-pinned build tool in out/, not in any runtime package."""
    cache = ROOT / 'out/toolchains'
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / DXC_ARCHIVE
    if not archive.exists():
        temporary = archive.with_suffix('.part')
        print(f'FSR 4: downloading build-time DXC {DXC_VERSION}', flush=True)
        with urllib.request.urlopen(DXC_URL) as response, temporary.open('wb') as output:
            shutil.copyfileobj(response, output)
        if sha256(temporary) != DXC_SHA256:
            temporary.unlink()
            raise ValueError('DXC release archive checksum mismatch')
        temporary.replace(archive)
    if sha256(archive) != DXC_SHA256:
        raise ValueError(f'DXC release archive checksum mismatch: {archive}')
    tool = cache / f'dxc-{DXC_VERSION}'
    # Re-extract verified tool bytes: a cached executable must not silently replace the pin.
    tool.mkdir(exist_ok=True)
    with tarfile.open(archive) as bundle:
        bundle.extractall(tool, filter='data')
    return tool / 'bin/dxc'


def build(jobs):
    digest = source_digest()
    try:
        verify_assets(ASSETS)
        print('FSR 4 v07: source/asset checksums current')
        return
    except (OSError, ValueError, KeyError):
        pass
    for model, pins in json.loads((VENDOR / 'SOURCE.json').read_text())['weights'].items():
        initializer = VENDOR / f'sdk/Kits/FidelityFX/upscalers/fsr4/internal/shaders/fsr4_model_v07_i8_{model}/initializers.bin'
        if sha256(initializer) != pins['initializers_sha256']:
            raise ValueError(f'Vendored initializer checksum mismatch: {model}')
    dxc = compiler()
    for required in ('spirv-val', 'spirv-dis'):
        if not shutil.which(required):
            raise ValueError(f'FSR 4 source build requires {required}')
    logs = ROOT / 'out/fsr4-build'
    logs.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='fsr4-', dir=logs) as work:
        work = Path(work)

        def compile_model(model):
            output = work / model
            env = os.environ.copy()
            # Use native DXC intrinsics. The legacy macro wrapper can evaluate arguments twice.
            env.update(DXC_BIN=str(dxc), FSR4_SPIRV_COMPAT='0', FSR4_VALIDATE='1', FSR4_DEBUG='0')
            command = ['bash', str(VENDOR / 'tools/compile_shaders_fsr4.sh'),
                       str(VENDOR / 'sdk'), model, str(output)]
            with (logs / f'{model}.log').open('w') as log:
                result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            if result.returncode:
                raise RuntimeError(f'FSR 4 {model} source build failed; see {logs / (model + ".log")}')
            print(f'FSR 4 v07: compiled and validated {model} (1080/2160/4320)', flush=True)
            return output

        with ThreadPoolExecutor(max_workers=jobs) as pool:
            outputs = list(pool.map(compile_model, MODELS))
        stage = work / 'assets'
        stage.mkdir()
        for output in outputs:
            for name in expected_assets():
                source = output / name
                if not source.exists():
                    continue
                target = stage / name
                if target.exists() and sha256(target) != sha256(source):
                    raise ValueError(f'FSR 4 shared shader differs between models: {name}')
                shutil.copyfile(source, target)
        manifest = {'format': 1, 'source_sha256': digest,
                    'compiler': {'version': DXC_VERSION, 'archive_sha256': DXC_SHA256},
                    'provenance': json.loads((VENDOR / 'SOURCE.json').read_text()),
                    'files': {name: {'size': (stage / name).stat().st_size, 'sha256': sha256(stage / name)}
                              for name in sorted(expected_assets())}}
        (stage / 'build.json').write_text(json.dumps(manifest, indent=2) + '\n')
        verify_assets(stage)
        # Only this generated directory is replaced: stale downloaded/optimized binaries cannot
        # shadow the source-built set (and are never copied into a public package).
        if ASSETS.exists():
            shutil.rmtree(ASSETS)
        shutil.move(str(stage), ASSETS)
    print(f'FSR 4 v07: {len(expected_assets())} verified runtime assets in {ASSETS}')


def stage_assets(source, destination):
    """Stage only the verified provider inventory, never optional/local DLL or opt trees."""
    manifest = verify_assets(source)
    destination = Path(destination)
    assets = destination / 'fsr4_shaders'
    licenses = destination / 'licenses'
    assets.mkdir(parents=True, exist_ok=True)
    licenses.mkdir(parents=True, exist_ok=True)
    for name in manifest['files']:
        shutil.copyfile(Path(source) / name, assets / name)
    shutil.copyfile(Path(source) / 'build.json', assets / 'build.json')
    for source_path, name in (
            (VENDOR / 'sdk/docs/license.md', 'FSR4-AMD-MIT.txt'),
            (VENDOR / 'Q2RTX-LICENSE.txt', 'FSR4-Q2RTX-GPL.txt'),
            (VENDOR / 'SOURCE.json', 'FSR4-SOURCE.json'),
            (VENDOR / 'NOTICE.txt', 'FSR4-NOTICE.txt'),
            (ROOT / 'gpu/third_party/fsr-vulkan/LICENSE.txt', 'FSR-Vulkan-MIT.txt'),
            (ROOT / 'gpu/third_party/fsr-vulkan/NOTICE.md', 'FSR-Vulkan-NOTICE.txt')):
        shutil.copyfile(source_path, licenses / name)
    print(f'FSR 4 v07: staged {len(manifest["files"])} source-built assets and license notices')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, help='stage an already built/verified asset set into a package')
    args = parser.parse_args()
    if args.stage:
        stage_assets(ASSETS, args.stage)
    else:
        build(max(1, min(6, int(os.environ.get('BB_JOBS', '6')))))


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Drive one scripted game instance on the Steam Deck (stdlib only: SteamOS has no Pillow).

deckctl.py start BUILD_DIR WORK_DIR [EXE]   start run.py in the background (Deck mode env)
deckctl.py press TOKEN [SECONDS]            hold a pad token (e.g. cross, "ly=0") then release
deckctl.py hold TOKENS... | release
deckctl.py shot WORK_DIR NAME               next presented frame -> WORK_DIR/shots/NAME.png
deckctl.py stop                             stop the game
"""
import os
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

GAME = '/run/media/deck/SD512/Uncharted/CUSA02343-v1.00'
STATE = Path.home() / 'unch-bench' / 'nav-state'


def work():
    return Path(STATE.read_text().strip())


def write_png(path, width, height, rgba):
    rows = bytearray()
    stride = width * 4
    for y in range(height):
        rows.append(0)
        row = rgba[y * stride:(y + 1) * stride]
        rows += bytes(b for i, b in enumerate(row) if i % 4 != 3)
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
    png += chunk(b'IDAT', zlib.compress(bytes(rows), 6)) + chunk(b'IEND', b'')
    path.write_bytes(png)


def pad(text):
    (work() / 'pad.txt').write_text(text)


def main():
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == 'start':
        build, wdir = Path(args[0]), Path(args[1])
        exe = args[2] if len(args) > 2 else 'u1'
        for sub in ('dump', 'shots'):
            (wdir / sub).mkdir(parents=True, exist_ok=True)
        STATE.write_text(str(wdir))
        (wdir / 'pad.txt').write_text('')
        env = dict(os.environ, XDG_RUNTIME_DIR='/run/user/1000', WAYLAND_DISPLAY='wayland-0', DISPLAY=':0',
                   BB_GAME_DIR=GAME, BB_USER_DIR=str(wdir / 'user'), BB_DATA_DIR=str(build), BB_EXE=exe,
                   BB_WINDOW_SIZE='1280x800', BB_RENDER_720P='1', BB_FULLSCREEN='1', BB_FRAME_STATS='1',
                   BB_PRESENT_MODE=os.environ.get('NAV_PRESENT', 'Fifo'),
                   BB_VBLANK_HZ=os.environ.get('NAV_HZ', '30'),
                   BB_PAD_FILE=str(wdir / 'pad.txt'), BB_FRAME_DUMP_TRIGGER=str(wdir / 'dump.trigger'),
                   BB_DUMP_DIR=str(wdir / 'dump'))
        # SSH sessions on SteamOS are logind scopes that are torn down (with every process in them)
        # when the connection closes, start_new_session or not: the game runs as a user unit instead.
        subprocess.run(['systemctl', '--user', 'stop', 'unch-nav.service'], stderr=subprocess.DEVNULL)
        subprocess.run(['systemctl', '--user', 'reset-failed', 'unch-nav.service'], stderr=subprocess.DEVNULL)
        run = ['systemd-run', '--user', '--unit=unch-nav', '--collect', f'--working-directory={build}',
               f'--property=StandardOutput=truncate:{wdir / "game.log"}', '--property=StandardError=inherit']
        run += [f'--setenv={k}={v}' for k, v in env.items() if k.startswith(('BB_', 'XDG_', 'WAYLAND', 'DISPLAY'))]
        subprocess.run(run + [sys.executable, 'run.py'], check=True)
        print('started unch-nav.service')
    elif cmd == 'press':
        pad(args[0].replace(',', ' '))
        time.sleep(float(args[1]) if len(args) > 1 else 0.2)
        pad('')
    elif cmd == 'hold':
        pad(' '.join(args).replace(',', ' '))
    elif cmd == 'release':
        pad('')
    elif cmd == 'shot':
        wdir, name = work(), args[0]
        dump = wdir / 'dump'
        for old in dump.glob('*.raw'):
            old.unlink()
        (wdir / 'dump.trigger').write_bytes(b'')
        for _ in range(100):
            time.sleep(0.05)
            new = list(dump.glob('output_*.raw'))
            if new:
                raw = new[0]
                w, h = map(int, raw.stem.rsplit('_', 1)[1].split('x'))
                for _ in range(60):
                    if raw.stat().st_size >= w * h * 4:
                        break
                    time.sleep(0.05)
                write_png(wdir / 'shots' / f'{name}.png', w, h, raw.read_bytes()[:w * h * 4])
                for r in dump.glob('*.raw'):
                    r.unlink()
                print('shot', wdir / 'shots' / f'{name}.png')
                return
        print('shot failed: no frame presented in 5 s')
        sys.exit(1)
    elif cmd == 'stop':
        subprocess.run(['systemctl', '--user', 'stop', 'unch-nav.service'])
        time.sleep(1)
        subprocess.run(['pkill', '-x', 'bb-probe'])
        print('stopped')


if __name__ == '__main__':
    main()

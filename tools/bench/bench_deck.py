#!/usr/bin/env python3
import sys, os, time, subprocess, json, struct, zlib
from pathlib import Path

GAME = "/run/media/deck/SD512/Uncharted/CUSA02343-v1.00"

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

def get_shot(wdir, name):
    dump = wdir / "dump"
    for old in dump.glob("*.raw"): old.unlink()
    (wdir / "dump.trigger").write_bytes(b"")
    for _ in range(80):
        time.sleep(0.05)
        new = list(dump.glob("output_*.raw"))
        if new:
            raw = new[0]
            w, h = map(int, raw.stem.rsplit("_", 1)[1].split("x"))
            for _ in range(30):
                if raw.stat().st_size >= w * h * 4: break
                time.sleep(0.05)
            data = raw.read_bytes()
            if len(data) < w * h * 4: continue
            for r in dump.glob("*.raw"): r.unlink()
            png_path = wdir / "shots" / f"{name}.png"
            write_png(png_path, w, h, data[:w * h * 4])
            # Fast mean brightness (sample every 40th pixel)
            step = 40 * 4
            samples = [data[i] * 0.299 + data[i+1] * 0.587 + data[i+2] * 0.114 for i in range(0, w * h * 4, step)]
            mean_b = sum(samples) / len(samples) if samples else 0.0
            print(f"Shot {name}: mean_brightness={mean_b:.1f}", flush=True)
            return mean_b, png_path
    return None, None

def pad(wdir, text):
    (wdir / "pad.txt").write_text(text)

def press(wdir, token, duration=0.3):
    pad(wdir, token)
    time.sleep(duration)
    pad(wdir, "")

def sample_gpu_busy():
    for card in ('/sys/class/drm/card0/device/gpu_busy_percent', '/sys/class/drm/card1/device/gpu_busy_percent'):
        try:
            return float(Path(card).read_text().strip())
        except Exception:
            pass
    return None

def main():
    if len(sys.argv) < 6:
        print("Usage: bench_deck.py <build_dir> <work_dir> <nav_hz> <nav_present> <measure_sec>")
        sys.exit(1)

    build_dir = Path(sys.argv[1]).resolve()
    wdir = Path(sys.argv[2]).resolve()
    nav_hz = sys.argv[3]
    nav_present = sys.argv[4]
    measure_sec = int(sys.argv[5])

    for sub in ('dump', 'shots'):
        (wdir / sub).mkdir(parents=True, exist_ok=True)
    (wdir / 'pad.txt').write_text('')
    (Path.home() / 'unch-bench' / 'nav-state').write_text(str(wdir))

    # Stop any previous instance
    subprocess.run(['systemctl', '--user', 'stop', 'unch-nav.service'], stderr=subprocess.DEVNULL)
    subprocess.run(['systemctl', '--user', 'reset-failed', 'unch-nav.service'], stderr=subprocess.DEVNULL)
    subprocess.run(['pkill', '-9', '-x', 'bb-probe'], stderr=subprocess.DEVNULL)
    time.sleep(1)

    glog = wdir / 'game.log'
    if glog.exists(): glog.unlink()

    env = dict(os.environ, XDG_RUNTIME_DIR='/run/user/1000', WAYLAND_DISPLAY='wayland-0', DISPLAY=':0',
               BB_GAME_DIR=GAME, BB_USER_DIR=str(wdir / 'user'), BB_DATA_DIR=str(build_dir), BB_EXE='u1',
               BB_WINDOW_SIZE='1280x800', BB_RENDER_720P='1', BB_FULLSCREEN='1', BB_FRAME_STATS='1',
               BB_PRESENT_MODE=nav_present, BB_VBLANK_HZ=nav_hz,
               BB_PAD_FILE=str(wdir / 'pad.txt'), BB_FRAME_DUMP_TRIGGER=str(wdir / 'dump.trigger'),
               BB_DUMP_DIR=str(wdir / 'dump'))

    run = ['systemd-run', '--user', '--unit=unch-nav', '--collect', f'--working-directory={build_dir}',
           f'--property=StandardOutput=truncate:{glog}', '--property=StandardError=inherit']
    run += [f'--setenv={k}={v}' for k, v in env.items() if k.startswith(('BB_', 'XDG_', 'WAYLAND', 'DISPLAY'))]
    subprocess.run(run + ['/usr/bin/python3', 'run.py'], check=True)
    print("Started unch-nav.service", flush=True)

    # Wait for pad opened
    t0 = time.time()
    opened = False
    while time.time() - t0 < 60:
        if glog.exists() and 'Runtime: pad opened' in glog.read_text(errors='replace'):
            opened = True
            break
        time.sleep(0.5)
    if not opened:
        print("FAIL: pad not opened within 60s")
        subprocess.run(['systemctl', '--user', 'stop', 'unch-nav.service'])
        sys.exit(1)
    print(f"Pad opened in {time.time() - t0:.1f}s", flush=True)

    # Step 1: Wait for collection carousel (brightness > 110)
    print("Waiting for collection carousel (a1 > 110)...", flush=True)
    t_step1 = time.time()
    while time.time() - t_step1 < 30:
        b1, _ = get_shot(wdir, 'a1')
        if b1 is not None and b1 > 110:
            print(f"Carousel arrived: brightness={b1:.1f} in {time.time() - t_step1:.1f}s", flush=True)
            break
        press(wdir, 'cross 0.2')
        time.sleep(1.0)

    print("Carousel active. Sending cross to select U1 until brightness drops < 85...", flush=True)
    t_step2 = time.time()
    b2 = None
    while time.time() - t_step2 < 45:
        press(wdir, 'cross 0.3')
        time.sleep(1.5)
        b2, _ = get_shot(wdir, 'a2')
        if b2 is not None and b2 < 85:
            print(f"U1 title loaded: brightness={b2:.1f} in {time.time() - t_step2:.1f}s", flush=True)
            break
    if b2 is None or b2 >= 85:
        print(f"FAIL at step 2 (brightness {b2})", flush=True)
        subprocess.run(['systemctl', '--user', 'stop', 'unch-nav.service'])
        sys.exit(1)

    time.sleep(1.0)
    press(wdir, 'cross 0.3') # dismiss U1 title
    print("Dismissed U1 title. Waiting 4s for main menu (Continue selected)...", flush=True)
    time.sleep(4.0)
    b3, _ = get_shot(wdir, 'a3')
    print(f"Main menu brightness={b3:.1f}", flush=True)

    # Step 3: Select Continue -> offline dialog
    press(wdir, 'cross 0.3')
    print("Selected Continue. Waiting 3s for offline dialog...", flush=True)
    time.sleep(3.0)
    b4, _ = get_shot(wdir, 'a4')
    print(f"Offline dialog brightness={b4:.1f}", flush=True)

    # Step 4: Confirm offline dialog -> level starts loading
    press(wdir, 'cross 0.3')
    print("Confirmed offline dialog. Level is loading...", flush=True)
    # Mark offset in game.log
    post_cross_offset = glog.stat().st_size if glog.exists() else 0
    t_load = time.time()
    gameplay_started = False
    while time.time() - t_load < 90:
        time.sleep(1.0)
        with open(glog, 'r', errors='replace') as f:
            f.seek(post_cross_offset)
            new_text = f.read()
        if 'Frame stats:' in new_text:
            # Check brightness of current frame to confirm it's not the loading screen
            b5, _ = get_shot(wdir, 'a5')
            if b5 is not None and b5 < 45.0: # Gameplay is dark courtyard (~29.0)
                gameplay_started = True
                print(f"Gameplay detected in {time.time() - t_load:.1f}s (brightness {b5:.1f})", flush=True)
                break
        elif time.time() - t_load > 5.0 and int(time.time() - t_load) % 4 == 0:
            # If dialog took longer to show up, press cross to ensure it dismisses
            press(wdir, 'cross 0.3')
    if not gameplay_started:
        print("FAIL: gameplay not reached within 90s")
        subprocess.run(['systemctl', '--user', 'stop', 'unch-nav.service'])
        sys.exit(1)

    # 5s settle
    print("5s settle...", flush=True)
    time.sleep(5.0)

    # Measurement window
    print(f"Starting {measure_sec}s measurement window...", flush=True)
    get_shot(wdir, 'measure_start')
    m_start_offset = glog.stat().st_size if glog.exists() else 0
    m_t0 = time.time()

    gpu_samples = []
    cam_state = 0
    next_cam = time.time() + 6.0
    pad(wdir, 'rx=255')

    while time.time() - m_t0 < measure_sec:
        now = time.time()
        if now >= next_cam:
            cam_state = 1 - cam_state
            pad(wdir, 'rx=128' if cam_state == 1 else 'rx=255')
            next_cam = now + 6.0
        g = sample_gpu_busy()
        if g is not None:
            gpu_samples.append(g)
        time.sleep(0.5)

    pad(wdir, '')
    m_t1 = time.time()
    get_shot(wdir, 'measure_end')
    print(f"Measurement complete in {m_t1 - m_t0:.1f}s", flush=True)

    # Extract window log
    with open(glog, 'r', errors='replace') as f:
        f.seek(m_start_offset)
        window_log = f.read()

    (wdir / 'window.log').write_text(window_log)
    (wdir / 'gpu_busy.txt').write_text('\n'.join(str(s) for s in gpu_samples))

    subprocess.run(['systemctl', '--user', 'stop', 'unch-nav.service'])
    time.sleep(1)
    subprocess.run(['pkill', '-9', '-x', 'bb-probe'], stderr=subprocess.DEVNULL)
    print("Done run", flush=True)

if __name__ == '__main__':
    main()

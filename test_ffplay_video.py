#!/usr/bin/env python3
"""
Debug script az ffplay videó pozicionálásához
Teszteli különböző paraméterekkel
"""

import subprocess
import sys
from screeninfo import get_monitors

def get_screen_info():
    """Kigyűjti a képernyők információit"""
    monitors = get_monitors()
    print("\n" + "="*60)
    print("MONITOROK:")
    print("="*60)
    for i, m in enumerate(monitors):
        print(f"Monitor {i}: {m.name}")
        print(f"  Pozíció: x={m.x}, y={m.y}")
        print(f"  Méret: {m.width}x{m.height}")
        print(f"  Primary: {m.is_primary}")
        print()
    return monitors

def test_ffplay(video_path, config_num):
    """Teszteli az ffplay-t különböző konfigurációkkal"""

    monitors = get_screen_info()

    if len(monitors) < 2:
        print("HIBA: Nincs 2. monitor!")
        return

    second = monitors[1]

    # Alap ffplay argumentumok
    base_args = ["ffplay"]

    configs = {
        1: {
            "name": "Csak -left -top",
            "args": [
                "-left", str(second.x),
                "-top", str(second.y),
            ]
        },
        2: {
            "name": "-left -top + -fs (fullscreen)",
            "args": [
                "-left", str(second.x),
                "-top", str(second.y),
                "-fs",
            ]
        },
        3: {
            "name": "-left -top + -fs + -alwaysontop",
            "args": [
                "-left", str(second.x),
                "-top", str(second.y),
                "-fs",
                "-alwaysontop",
            ]
        },
        4: {
            "name": "-geometry (SDL_VIDEO_WINDOW_POS)",
            "args": [],
            "env": {
                "SDL_VIDEO_WINDOW_POS": f"{second.x},{second.y}"
            }
        },
        5: {
            "name": "-geometry + -fs",
            "args": ["-fs"],
            "env": {
                "SDL_VIDEO_WINDOW_POS": f"{second.x},{second.y}"
            }
        },
        6: {
            "name": "-x -y méret beállítás + -left -top + -fs",
            "args": [
                "-left", str(second.x),
                "-top", str(second.y),
                "-x", str(second.width),
                "-y", str(second.height),
                "-fs",
            ]
        },
    }

    if config_num not in configs:
        print(f"HIBA: Nincs {config_num} számú konfiguráció!")
        return

    config = configs[config_num]

    print("="*60)
    print(f"KONFIGURÁCIÓ {config_num}: {config['name']}")
    print("="*60)
    print(f"Videó: {video_path}")
    print(f"2. Monitor: {second.name} ({second.width}x{second.height}+{second.x}+{second.y})")
    print()

    # Összeállítjuk a parancsot
    args = base_args + config["args"] + [video_path]
    env = config.get("env")

    print("Parancs:")
    if env:
        for k, v in env.items():
            print(f"  {k}={v}")
    print(f"  {' '.join(args)}")
    print()
    print("→ Nyomd meg 'q'-t vagy ESC-et a kilépéshez")
    print("→ Nyomd meg 'w'-t az audio vizualizáció váltásához")
    print("="*60)
    print()

    # Futtatjuk az ffplay-t
    try:
        if env:
            import os
            full_env = os.environ.copy()
            full_env.update(env)
            subprocess.run(args, env=full_env)
        else:
            subprocess.run(args)
    except KeyboardInterrupt:
        print("\nMegszakítva!")
    except Exception as e:
        print(f"\nHIBA: {e}")

def main():
    print("\n" + "="*60)
    print("FFPLAY VIDEÓ POZICIONÁLÁSI TESZT")
    print("="*60)
    print("\nHasználat:")
    print("  python3 test_ffplay_video.py <video_fájl> [konfig_szám]")
    print("\nKonfigurációk:")
    print("  1: Csak -left -top")
    print("  2: -left -top + -fs")
    print("  3: -left -top + -fs + -alwaysontop (jelenlegi)")
    print("  4: SDL_VIDEO_WINDOW_POS env var")
    print("  5: SDL_VIDEO_WINDOW_POS + -fs")
    print("  6: -left -top + -x -y méret + -fs")
    print("="*60 + "\n")

    if len(sys.argv) < 2:
        print("HIBA: Add meg a videó fájl útvonalát!")
        print("\nPélda:")
        print('  python3 test_ffplay_video.py "/Users/petersandor/Downloads/2025.07.18-19 Szendrőlád remete napok-5/20250719_182838.mp4" 3')
        return

    video_path = sys.argv[1]
    config_num = 3  # default: jelenlegi konfig

    if len(sys.argv) > 2:
        try:
            config_num = int(sys.argv[2])
        except ValueError:
            print("HIBA: Hibás konfig szám!")
            return

    test_ffplay(video_path, config_num)

if __name__ == "__main__":
    main()

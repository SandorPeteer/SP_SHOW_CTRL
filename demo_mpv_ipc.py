#!/usr/bin/env python3
"""
MPV IPC Demo - Playlist váltogatás UGYANABBAN az ablakban!

Ez mutatja meg hogy az MPV IPC-vel hogyan lehet dinamikusan
váltogatni a médiákat anélkül hogy új ablakot nyitnánk.
"""

import subprocess
import time
import json
import socket
from pathlib import Path
from screeninfo import get_monitors

# Test videos - prioritize Samsung
TEST_PLAYLIST = [
    "/Users/petersandor/Downloads/2025.07.18-19 Szendrőlád remete napok-5/20250719_182838.mp4",
    "/Users/petersandor/Downloads/2025.07.18-19 Szendrőlád remete napok-5/20250718_210237.mp4",
    "/Users/petersandor/Music/BULIS/aha-takeonme_remix.mp4",
]

SOCKET_PATH = "/tmp/mpv_demo_socket"

def get_second_monitor():
    """Get second monitor info"""
    monitors = get_monitors()
    if len(monitors) >= 2:
        return monitors[1]
    return None

def send_mpv_command(command_dict):
    """Send JSON command to MPV via IPC socket"""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(SOCKET_PATH)
        command = json.dumps(command_dict) + '\n'
        sock.send(command.encode('utf-8'))
        response = sock.recv(4096).decode('utf-8')
        sock.close()
        return json.loads(response) if response else None
    except Exception as e:
        print(f"IPC Error: {e}")
        return None

def start_mpv_with_ipc():
    """Start MPV with IPC enabled"""
    second = get_second_monitor()
    if not second:
        print("❌ No second monitor!")
        return None

    # Start with first video
    first_video = next((v for v in TEST_PLAYLIST if Path(v).exists()), None)
    if not first_video:
        print("❌ No test videos found!")
        return None

    args = [
        'mpv',
        '--fullscreen',
        f'--geometry={second.width}x{second.height}+{second.x}+{second.y}',
        '--keep-open=yes',  # Keep window after playback
        f'--input-ipc-server={SOCKET_PATH}',  # IPC socket
        '--hwdec=auto',  # Hardware decoding for 4K
        '--idle=yes',  # Stay open even without media
        '--force-window=yes',
        first_video
    ]

    print(f"\n🎬 Starting MPV on second monitor: {second.name}")
    print(f"   Resolution: {second.width}x{second.height}")
    print(f"   IPC Socket: {SOCKET_PATH}")
    print(f"   First video: {Path(first_video).name}\n")

    # Start MPV in background
    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    # Wait for socket to be ready
    print("⏳ Waiting for MPV to initialize...")
    for i in range(30):
        if Path(SOCKET_PATH).exists():
            time.sleep(0.5)  # Extra time for socket to be ready
            print("✓ MPV ready!\n")
            return proc
        time.sleep(0.1)

    print("❌ MPV socket not ready!")
    proc.kill()
    return None

def demo_playlist_switching(proc):
    """Demo: Switch videos in the same MPV window"""
    print("="*70)
    print("MPV IPC PLAYLIST SWITCHING DEMO")
    print("="*70)
    print("\nThis demo will:")
    print("1. Keep the SAME MPV window open")
    print("2. Switch videos dynamically using IPC")
    print("3. No new windows, no flickering!\n")
    print("="*70)

    # Find available videos
    available = [v for v in TEST_PLAYLIST if Path(v).exists()]

    for i, video in enumerate(available, 1):
        print(f"\n▶ [{i}/{len(available)}] Switching to: {Path(video).name}")

        # Send loadfile command via IPC
        cmd = {
            "command": ["loadfile", video, "replace"]
        }

        result = send_mpv_command(cmd)

        if result:
            print(f"   ✓ IPC command successful")
            print(f"   → Watch the video on second monitor!")
            print(f"   → Video will play for 10 seconds...")

            # Wait 10 seconds
            for sec in range(10, 0, -1):
                print(f"   ⏱  {sec}s...", end='\r', flush=True)
                time.sleep(1)
            print()
        else:
            print(f"   ❌ IPC command failed!")
            break

    print("\n✓ Demo completed!")
    print("\nNow let's try PAUSING via IPC...")

    # Pause command
    cmd = {"command": ["set_property", "pause", True]}
    result = send_mpv_command(cmd)
    if result:
        print("✓ Paused via IPC")
        time.sleep(2)

        # Unpause
        cmd = {"command": ["set_property", "pause", False]}
        send_mpv_command(cmd)
        print("✓ Resumed via IPC")
        time.sleep(2)

    print("\nNow let's try SEEKING via IPC...")

    # Seek to 50% position
    cmd = {"command": ["seek", "50", "absolute-percent"]}
    result = send_mpv_command(cmd)
    if result:
        print("✓ Seeked to 50% via IPC")
        time.sleep(3)

    print("\n" + "="*70)
    print("DEMO SUMMARY")
    print("="*70)
    print("\n✓ All video switches happened in THE SAME WINDOW!")
    print("✓ No new processes created")
    print("✓ No window flickering")
    print("✓ Full control via IPC (play, pause, seek, loadfile)")
    print("\nThis is PERFECT for SP Show Control!")
    print("\nQuitting MPV in 3 seconds...")
    time.sleep(3)

    # Quit MPV gracefully
    cmd = {"command": ["quit"]}
    send_mpv_command(cmd)
    time.sleep(0.5)

    # Ensure cleanup
    try:
        proc.kill()
    except:
        pass

def main():
    print("\n" + "="*70)
    print("MPV IPC PLAYLIST DEMO")
    print("="*70)
    print("\nThis demo shows how MPV can switch videos in the same window")
    print("using IPC (Inter-Process Communication).\n")
    print("No new windows, no process restarts - just smooth switching!")
    print("="*70)
    print("\nStarting demo in 2 seconds...")
    time.sleep(2)

    # Start MPV
    proc = start_mpv_with_ipc()
    if not proc:
        return

    try:
        # Demo playlist switching
        demo_playlist_switching(proc)
    except KeyboardInterrupt:
        print("\n\n⚠ Demo interrupted by user")
    finally:
        # Cleanup
        try:
            proc.kill()
        except:
            pass

        if Path(SOCKET_PATH).exists():
            Path(SOCKET_PATH).unlink()

    print("\n✓ Demo finished. MPV closed.\n")

if __name__ == "__main__":
    main()

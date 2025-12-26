#!/usr/bin/env python3
"""
Összehasonlító teszt különböző media player backend-ekkel.
Teszteli: MPV, VLC, PyQt6+QtMultimedia
"""

import sys
import subprocess
import time
from pathlib import Path
from screeninfo import get_monitors

# Test video paths from your JSON - prioritize Samsung video first!
TEST_VIDEOS = [
    "/Users/petersandor/Downloads/2025.07.18-19 Szendrőlád remete napok-5/20250719_182838.mp4",  # Samsung video
    "/Users/petersandor/Downloads/2025.07.18-19 Szendrőlád remete napok-5/20250718_210237.mp4",  # Samsung video 2
    "/Users/petersandor/Music/BULIS/aha-takeonme_remix.mp4",
]

TEST_AUDIO = "/Users/petersandor/Music/BULIS/Bagossy Brothers-Olyan Ő ( FRG Club remix) [dl9VD6l].mp3"

def get_second_monitor():
    """Get second monitor coordinates"""
    monitors = get_monitors()
    if len(monitors) >= 2:
        second = monitors[1]
        return {
            'x': second.x,
            'y': second.y,
            'width': second.width,
            'height': second.height,
            'name': second.name
        }
    return None

def test_mpv():
    """Test MPV player"""
    print("\n" + "="*60)
    print("TESTING MPV PLAYER")
    print("="*60)

    # Check if mpv is installed
    try:
        result = subprocess.run(['which', 'mpv'], capture_output=True, text=True)
        if not result.stdout.strip():
            print("❌ MPV not installed!")
            print("Install with: brew install mpv")
            return False
    except Exception as e:
        print(f"❌ Error checking mpv: {e}")
        return False

    second = get_second_monitor()
    if not second:
        print("❌ No second monitor detected!")
        return False

    print(f"✓ MPV installed")
    print(f"✓ Second monitor: {second['name']} ({second['width']}x{second['height']}+{second['x']}+{second['y']})")

    # MPV supports IPC for playlist control!
    socket_path = "/tmp/mpv_socket"

    print("\n--- Test 1: Simple fullscreen video ---")
    video = next((v for v in TEST_VIDEOS if Path(v).exists()), None)
    if not video:
        print("❌ No test video found!")
        return False

    args = [
        'mpv',
        '--fullscreen',
        f'--geometry={second["width"]}x{second["height"]}+{second["x"]}+{second["y"]}',
        '--keep-open=yes',  # Keep window open after playback
        f'--input-ipc-server={socket_path}',  # Enable IPC for control
        '--force-window=yes',  # Force window focus
        '--ontop',  # Keep on top
        video
    ]

    print(f"\nPlaying Samsung video: {Path(video).name}")
    print(f"Command: mpv --fullscreen --geometry=... {Path(video).name}")
    print("\n→ MPV window should appear on second monitor")
    print("→ Press 'q' in the MPV window to quit and continue test")
    print("→ Or press Ctrl+C here in terminal to abort")

    try:
        # Run in foreground so it can receive keyboard input
        proc = subprocess.run(args, stdin=None)
        print("\n✓ MPV playback completed")
    except KeyboardInterrupt:
        print("\n⚠ Interrupted by user")
        subprocess.run(['pkill', '-9', 'mpv'], capture_output=True)
        return False
    except Exception as e:
        print(f"❌ MPV error: {e}")
        return False

    print("\n--- Test 2: Playlist switching (IPC) ---")
    print("MPV supports --input-ipc-server for dynamic playlist control!")
    print("You can send commands like: echo 'loadfile video.mp4 replace' | socat - /tmp/mpv_socket")

    return True

def test_vlc():
    """Test VLC player"""
    print("\n" + "="*60)
    print("TESTING VLC PLAYER")
    print("="*60)

    # Check if VLC is installed
    vlc_path = "/Applications/VLC.app/Contents/MacOS/VLC"
    if not Path(vlc_path).exists():
        print("❌ VLC not installed!")
        print("Install from: https://www.videolan.org/vlc/")
        return False

    second = get_second_monitor()
    if not second:
        print("❌ No second monitor detected!")
        return False

    print(f"✓ VLC installed")
    print(f"✓ Second monitor: {second['name']} ({second['width']}x{second['height']}+{second['x']}+{second['y']})")

    print("\n--- Test 1: Simple fullscreen video ---")
    video = next((v for v in TEST_VIDEOS if Path(v).exists()), None)
    if not video:
        print("❌ No test video found!")
        return False

    args = [
        vlc_path,
        '--fullscreen',
        '--no-video-title-show',  # Don't show filename
        '--video-x', str(second['x']),
        '--video-y', str(second['y']),
        video
    ]

    print(f"Command: vlc --fullscreen --video-x {second['x']} --video-y {second['y']} {Path(video).name}")
    print("→ Press Cmd+Q to quit and continue test")

    try:
        proc = subprocess.run(args)
        print("✓ VLC playback completed")
    except Exception as e:
        print(f"❌ VLC error: {e}")
        return False

    print("\n--- Note: VLC HTTP/RC interface ---")
    print("VLC supports --rc-host for remote control!")
    print("You can control playback via HTTP or socket interface")

    return True

def test_pyqt6_qtmultimedia():
    """Test PyQt6 + QtMultimedia"""
    print("\n" + "="*60)
    print("TESTING PyQt6 + QtMultimedia")
    print("="*60)

    try:
        from PyQt6.QtWidgets import QApplication, QMainWindow
        from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
        from PyQt6.QtMultimediaWidgets import QVideoWidget
        from PyQt6.QtCore import QUrl, Qt
        print("✓ PyQt6 and QtMultimedia installed")
    except ImportError as e:
        print(f"❌ Missing dependencies: {e}")
        print("Install with:")
        print("  pip3 install PyQt6 PyQt6-QtMultimedia")
        return False

    second = get_second_monitor()
    if not second:
        print("❌ No second monitor detected!")
        return False

    print(f"✓ Second monitor: {second['name']} ({second['width']}x{second['height']}+{second['x']}+{second['y']})")

    print("\n--- Test 1: Video playback ---")
    video = next((v for v in TEST_VIDEOS if Path(v).exists()), None)
    if not video:
        print("❌ No test video found!")
        return False

    app = QApplication(sys.argv)

    # Create video player
    player = QMediaPlayer()
    audio_output = QAudioOutput()
    player.setAudioOutput(audio_output)

    video_widget = QVideoWidget()
    player.setVideoOutput(video_widget)

    # Position on second monitor
    video_widget.setGeometry(second['x'], second['y'], second['width'], second['height'])
    video_widget.setWindowTitle("PyQt6 Media Player Test")
    video_widget.showFullScreen()

    # Load and play
    player.setSource(QUrl.fromLocalFile(video))
    player.play()

    print(f"Playing: {Path(video).name}")
    print("→ Press ESC to close and continue test")

    # Handle close
    def handle_key(event):
        if event.key() == Qt.Key.Key_Escape:
            player.stop()
            app.quit()

    video_widget.keyPressEvent = handle_key

    try:
        app.exec()
        print("✓ PyQt6 playback completed")
    except Exception as e:
        print(f"❌ PyQt6 error: {e}")
        return False

    print("\n--- PyQt6 Advantages ---")
    print("✓ Full programmatic control")
    print("✓ Easy playlist switching: player.setSource(new_url)")
    print("✓ No external process management")
    print("✓ Built-in audio visualization widgets available")

    return True

def main():
    print("\n" + "="*70)
    print("MEDIA PLAYER COMPARISON TEST")
    print("="*70)
    print("\nTesting different media player backends for SP Show Control")
    print("\nAvailable tests:")
    print("  1. MPV (lightweight, IPC support)")
    print("  2. VLC (full-featured, RC interface)")
    print("  3. PyQt6 + QtMultimedia (native Python)")
    print("  4. All of the above")
    print("="*70)

    # Check command line argument
    if len(sys.argv) > 1:
        choice = sys.argv[1]
    else:
        try:
            choice = input("\nSelect test (1-4): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nAborted.")
            return

    results = {}

    if choice == '1' or choice == '4':
        results['mpv'] = test_mpv()

    if choice == '2' or choice == '4':
        results['vlc'] = test_vlc()

    if choice == '3' or choice == '4':
        results['pyqt6'] = test_pyqt6_qtmultimedia()

    # Summary
    print("\n" + "="*70)
    print("TEST RESULTS SUMMARY")
    print("="*70)

    for player, result in results.items():
        status = "✓ PASS" if result else "❌ FAIL"
        print(f"{player.upper():15} {status}")

    print("\n" + "="*70)
    print("RECOMMENDATIONS")
    print("="*70)
    print("\n1. MPV - Best for stability and IPC control")
    print("   Pros: Lightweight, stable, supports all formats, IPC for playlist control")
    print("   Cons: External dependency")
    print("\n2. PyQt6 + QtMultimedia - Best for integration")
    print("   Pros: Full Python control, no process management, easy media switching")
    print("   Cons: Larger dependency, some codec issues on macOS")
    print("\n3. VLC - Most compatible")
    print("   Pros: Plays everything, stable, well-known")
    print("   Cons: Heavy, slower startup, RC interface less elegant")

    print("\n" + "="*70)

if __name__ == "__main__":
    main()

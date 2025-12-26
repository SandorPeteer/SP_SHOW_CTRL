#!/usr/bin/env python3
"""
MPV Player wrapper with IPC control for SP Show Control.

This module provides a persistent MPV player that uses IPC
(Inter-Process Communication) to switch media without restarting
the player window.
"""

import subprocess
import json
import socket
import time
from pathlib import Path
from typing import Optional
from screeninfo import get_monitors


class MPVPlayer:
    """
    MPV player with IPC control.

    Features:
    - Single persistent window on second monitor
    - Switch media via IPC (no window restart)
    - Full control: play, pause, seek, volume
    - Hardware decoding for 4K videos
    """

    def __init__(self, socket_path: str = "/tmp/sp_show_mpv_socket"):
        self.socket_path = socket_path
        self.process: Optional[subprocess.Popen] = None
        self._current_media: Optional[str] = None

    def is_running(self) -> bool:
        """Check if MPV process is running"""
        if not self.process:
            return False
        return self.process.poll() is None

    def start(self, fullscreen: bool = True, second_monitor: bool = True):
        """
        Start MPV player window.

        Args:
            fullscreen: Start in fullscreen mode
            second_monitor: Position on second monitor if available
        """
        if self.is_running():
            return  # Already running

        args = ['mpv']

        # Position on second monitor if requested
        if second_monitor:
            monitors = get_monitors()
            if len(monitors) >= 2:
                second = monitors[1]
                args.extend([
                    f'--geometry={second.width}x{second.height}+{second.x}+{second.y}',
                ])

        # Player configuration
        args.extend([
            '--idle=yes',  # Keep window open without media
            '--keep-open=yes',  # Keep window after playback
            '--force-window=yes',  # Always show window
            f'--input-ipc-server={self.socket_path}',  # Enable IPC
            '--hwdec=auto',  # Hardware decoding for 4K
            '--no-terminal',  # Don't output to terminal
            '--no-input-default-bindings',  # Disable default key bindings
            '--really-quiet',  # Suppress output
        ])

        if fullscreen:
            args.append('--fullscreen')

        # Start MPV in background
        self.process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL
        )

        # Wait for IPC socket to be ready
        for _ in range(50):  # 5 seconds max
            if Path(self.socket_path).exists():
                time.sleep(0.1)  # Extra time for socket
                break
            time.sleep(0.1)

    def _send_command(self, command: dict) -> Optional[dict]:
        """
        Send JSON command to MPV via IPC socket.

        Args:
            command: JSON-RPC command dict

        Returns:
            Response dict or None on error
        """
        if not self.is_running():
            return None

        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect(self.socket_path)

            # Send command
            cmd_str = json.dumps(command) + '\n'
            sock.send(cmd_str.encode('utf-8'))

            # Read response
            response = sock.recv(4096).decode('utf-8')
            sock.close()

            if response:
                return json.loads(response.strip())
            return None

        except (socket.error, json.JSONDecodeError, Exception):
            return None

    def load_media(self, media_path: str, start_time: float = 0.0, replace: bool = True):
        """
        Load and play media file.

        Args:
            media_path: Path to media file
            start_time: Start playback at this time (seconds)
            replace: Replace current media (True) or append to playlist (False)
        """
        if not self.is_running():
            self.start()

        mode = "replace" if replace else "append-play"

        # Load file
        cmd = {
            "command": ["loadfile", str(media_path), mode]
        }
        self._send_command(cmd)

        # Seek to start time if specified
        if start_time > 0:
            time.sleep(0.2)  # Wait for file to load
            self.seek(start_time, absolute=True)

        self._current_media = str(media_path)

    def play(self):
        """Resume playback"""
        cmd = {"command": ["set_property", "pause", False]}
        self._send_command(cmd)

    def pause(self):
        """Pause playback"""
        cmd = {"command": ["set_property", "pause", True]}
        self._send_command(cmd)

    def stop(self):
        """Stop playback (keep window open)"""
        cmd = {"command": ["stop"]}
        self._send_command(cmd)

    def seek(self, seconds: float, absolute: bool = False):
        """
        Seek to position.

        Args:
            seconds: Time in seconds (or offset if not absolute)
            absolute: True for absolute position, False for relative
        """
        if absolute:
            cmd = {"command": ["seek", str(seconds), "absolute"]}
        else:
            cmd = {"command": ["seek", str(seconds), "relative"]}
        self._send_command(cmd)

    def set_volume(self, volume: int):
        """
        Set volume level.

        Args:
            volume: Volume level (0-100)
        """
        volume = max(0, min(100, volume))
        cmd = {"command": ["set_property", "volume", volume]}
        self._send_command(cmd)

    def get_position(self) -> Optional[float]:
        """
        Get current playback position.

        Returns:
            Position in seconds or None
        """
        cmd = {"command": ["get_property", "time-pos"]}
        response = self._send_command(cmd)

        if response and "data" in response:
            try:
                return float(response["data"])
            except (ValueError, TypeError):
                pass
        return None

    def get_duration(self) -> Optional[float]:
        """
        Get media duration.

        Returns:
            Duration in seconds or None
        """
        cmd = {"command": ["get_property", "duration"]}
        response = self._send_command(cmd)

        if response and "data" in response:
            try:
                return float(response["data"])
            except (ValueError, TypeError):
                pass
        return None

    def is_playing(self) -> bool:
        """
        Check if media is currently playing.

        Returns:
            True if playing, False if paused/stopped
        """
        cmd = {"command": ["get_property", "pause"]}
        response = self._send_command(cmd)

        if response and "data" in response:
            # "pause" property: False means playing
            return not response["data"]
        return False

    def quit(self):
        """Quit MPV player and close window"""
        if self.is_running():
            # Try graceful quit via IPC
            cmd = {"command": ["quit"]}
            self._send_command(cmd)

            # Wait a bit for graceful shutdown
            time.sleep(0.5)

            # Force kill if still running
            if self.is_running():
                try:
                    self.process.terminate()
                    self.process.wait(timeout=2.0)
                except:
                    try:
                        self.process.kill()
                    except:
                        pass

            self.process = None

        # Clean up socket
        if Path(self.socket_path).exists():
            try:
                Path(self.socket_path).unlink()
            except:
                pass

    def __del__(self):
        """Cleanup on deletion"""
        self.quit()


# Example usage
if __name__ == "__main__":
    player = MPVPlayer()

    print("Starting MPV player on second monitor...")
    player.start(fullscreen=True, second_monitor=True)

    print("Testing with sample video...")
    # You would use actual video paths here
    test_video = "/path/to/video.mp4"

    if Path(test_video).exists():
        player.load_media(test_video)
        time.sleep(5)

        print("Pausing...")
        player.pause()
        time.sleep(2)

        print("Resuming...")
        player.play()
        time.sleep(3)

        print("Seeking to 50%...")
        duration = player.get_duration()
        if duration:
            player.seek(duration / 2, absolute=True)
        time.sleep(3)

    print("Quitting...")
    player.quit()
    print("Done!")

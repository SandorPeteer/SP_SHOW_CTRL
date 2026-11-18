"""Bounded, cancellable PCM overview for the operator display."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import threading


def pcm_overview(exe, path, cancelled, max_bars=256, **kwargs):
    import numpy as np

    rate, window_size = 48000, 2048
    frequencies = np.fft.rfftfreq(window_size, 1 / rate)
    cuts = np.searchsorted(frequencies, (25, 250, 2500))
    window = np.hanning(window_size).astype(np.float32)[None, :, None]
    groups = []
    command = [exe, "-nostdin", "-v", "error", "-threads", "1", "-i", str(path),
               "-map", "0:a:0", "-vn", "-ac", "2", "-ar", str(rate), "-f", "s16le", "pipe:1"]
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **kwargs) as proc:
        def kill():
            try:
                proc.kill()
            except OSError:
                pass

        watchdog = threading.Timer(120, kill)
        watchdog.daemon = True
        watchdog.start()

        def cancel_when_requested():
            while proc.poll() is None:
                if cancelled.wait(0.2):
                    kill()
                    return

        threading.Thread(target=cancel_when_requested, daemon=True).start()
        try:
            while not cancelled.is_set():
                block = proc.stdout.read(window_size * 4 * 48)
                if not block:
                    break
                samples = np.frombuffer(block, dtype="<i2").astype(np.float32) / 32768
                samples = samples[:len(samples) // 2 * 2]
                if not len(samples):
                    break
                frames = (len(samples) + window_size * 2 - 1) // (window_size * 2)
                samples = np.pad(samples, (0, frames * window_size * 2 - len(samples)))
                samples = samples.reshape(frames, window_size, 2)
                peak = np.max(np.abs(samples), axis=(1, 2))
                power = np.abs(np.fft.rfft(samples * window, axis=1)) ** 2
                bands = np.add.reduceat(power.sum(axis=2), cuts, axis=1)
                groups.append(np.column_stack((peak, bands)).astype(np.float32))
        finally:
            watchdog.cancel()
            if cancelled.is_set():
                kill()
        if cancelled.is_set() or proc.wait() != 0 or not groups:
            return []

    data = np.concatenate(groups)
    count = min(max_bars, len(data))
    edges = np.arange(count + 1) * len(data) // count
    amp = np.maximum.reduceat(data[:, 0], edges[:-1])
    energies = np.add.reduceat(data[:, 1:], edges[:-1], axis=0)
    # Give weak harmonics some visibility while retaining the dominant band.
    weights = np.sqrt(energies)
    totals = weights.sum(axis=1, keepdims=True)
    weights /= np.maximum(1e-8, totals)
    palette = np.array([[255, 102, 129], [255, 208, 110], [89, 216, 255]])
    colors = (weights @ palette).astype(int)
    heights = 0.94 * (amp / max(0.015, float(amp.max()))) ** 0.65
    return [dict(height=round(float(level), 4), color=("#%02x%02x%02x" % tuple(rgb)) if level else "#27384a")
            for level, rgb in zip(heights, colors)]


class SpectralOverview:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="spectral-waveform")
        self.cache = OrderedDict()
        self.requested = None
        self.future = None
        self.cancelled = threading.Event()

    def select(self, path, executable, kwargs):
        if path == self.requested:
            return
        self.requested = path
        self.cancelled.set()
        if self.future is not None:
            self.future.cancel()
        self.cancelled = threading.Event()
        self.future = self.executor.submit(self._render, path, executable, kwargs, self.cancelled) if path and executable else None

    def _render(self, path, executable, kwargs, cancelled):
        try:
            stat = Path(path).stat()
            key = (path, stat.st_size, stat.st_mtime_ns)
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            value = pcm_overview(executable, path, cancelled, **kwargs)
            if not cancelled.is_set():
                self.cache[key] = value
                if len(self.cache) > 12:
                    self.cache.popitem(last=False)
            return value
        except (OSError, ValueError, ImportError, RuntimeError):
            return []

    def read(self):
        if self.future is None:
            return []
        return self.future.result() if self.future.done() else None

    def close(self):
        self.cancelled.set()
        self.executor.shutdown(wait=False, cancel_futures=True)

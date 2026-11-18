"""Verify that decoded PCM maps frequency and peak amplitude independently."""
from pathlib import Path
import sys
import tempfile
import threading
import wave

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from spectral_waveform import pcm_overview


def test(executable):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "bands.wav"
        time = np.arange(24576) / 48000
        signal = np.concatenate([np.sin(time * 2 * np.pi * f) * a for f, a in
                                 ((90, 0.4), (900, 0.4), (6300, 0.8), (90, 0.0))])
        with wave.open(str(path), "wb") as stream:
            stream.setparams((1, 2, 48000, 0, "NONE", ""))
            stream.writeframes((signal * 32767).astype("<i2").tobytes())
        bars = pcm_overview(str(executable), path, threading.Event())
        first, middle, third, silence = (bars[len(bars) * n // 8] for n in (1, 3, 5, 7))
        red, amber, blue = [bytes.fromhex(bar["color"].lstrip("#")) for bar in (first, middle, third)]
        assert red[0] > red[1] * 1.6
        assert amber[0] > amber[2] * 1.6 and amber[1] > amber[2] * 1.4
        assert blue[2] > blue[0] * 2
        assert third["height"] > 1.4 * first["height"] and silence["height"] == 0
        print("PASS: PCM overview distinguishes bass, mids, highs, dynamics and silence")

        # Distinct timed events in opposite stereo channels must remain at
        # their source positions, including genuine silence between them.
        samples = np.zeros((48000 * 4, 2), dtype=np.float32)
        burst = np.sin(np.arange(12000) * 2 * np.pi * 120 / 48000)
        samples[48000:60000, 0] = burst * 0.25
        samples[144000:156000, 1] = burst * 0.75
        with wave.open(str(path), "wb") as stream:
            stream.setparams((2, 2, 48000, 0, "NONE", ""))
            stream.writeframes((samples * 32767).astype("<i2").tobytes())
        envelope = pcm_overview(str(executable), path, threading.Event())
        assert len(envelope) >= 700, "Short audio must retain a dense waveform, not 256 bars"
        active = [index for index, point in enumerate(envelope)
                  if point["maximum"] > 0 or point["minimum"] < 0]
        at = lambda seconds: envelope[int(seconds / 4 * len(envelope))]
        for seconds in (0.5, 2, 3.75):
            assert at(seconds)["minimum"] == at(seconds)["maximum"] == 0, seconds
        for seconds in (1.1, 3.1):
            assert at(seconds)["minimum"] < 0 < at(seconds)["maximum"], seconds
        assert abs(at(3.1)["maximum"] / at(1.1)["maximum"] - 3) < 0.02
        assert abs(active[0] / len(envelope) * 4 - 1.0) < 0.012
        assert abs((active[-1] + 1) / len(envelope) * 4 - 3.25) < 0.012
        limited = pcm_overview(str(executable), path, threading.Event(), max_bars=37)
        assert len(limited) == 37
        assert max(point["maximum"] for point in limited) == max(point["maximum"] for point in envelope)
        assert min(point["minimum"] for point in limited) == min(point["minimum"] for point in envelope)
        print("PASS: continuous PCM envelope preserves stereo events, silence, 3:1 amplitude and timing within 12 ms")


if __name__ == "__main__":
    test(sys.argv[1])

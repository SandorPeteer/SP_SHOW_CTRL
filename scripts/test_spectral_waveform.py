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


if __name__ == "__main__":
    test(sys.argv[1])

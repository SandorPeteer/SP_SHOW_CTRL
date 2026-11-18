"""Validate the live RMS/peak meter against known PCM and mpv's actual PCM output."""
from contextlib import contextmanager
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
import wave

import numpy as np

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from audio_meter import METER_PROPERTY, levels_from_telemetry, metered_filters
from mpv_ipc import MpvIpcSession

mpv = str(root / "dist/SP Show Control/_internal/tools/mpv/mpv.exe") if sys.platform == "win32" else shutil.which("mpv")
process_options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def wait_for(read, predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = read()
        if predicate(result):
            return result
        time.sleep(0.04)
    raise AssertionError(f"Audio telemetry did not reach expected state: {result!r}")


class AudioMeterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sp-meter-")
        self.folder = Path(self.tmp.name)
        self.source = self.folder / "stereo.wav"
        t = np.arange(48000 * 8) / 48000
        self.signal = np.column_stack((0.5 * np.sin(t * 2 * np.pi * 1000),
                                      0.25 * np.sin(t * 2 * np.pi * 500)))
        with wave.open(str(self.source), "wb") as stream:
            stream.setparams((2, 2, 48000, 0, "NONE", ""))
            stream.writeframes((self.signal * 32767).astype("<i2").tobytes())

    def tearDown(self):
        self.tmp.cleanup()

    @contextmanager
    def player(self, filters=None):
        session = MpvIpcSession(mpv, name="METER_" + uuid.uuid4().hex,
                                second_screen_left=0, second_screen_top=0, fullscreen=False)
        if os.name != "nt":
            session.ipc_server = str(self.folder / "meter.sock")
        session._proc = subprocess.Popen([mpv, "--no-config", "--no-terminal", "--ao=null", "--vo=null",
                                           "--volume=100", "--keep-open=yes", "--audio-channels=stereo",
                                           "--input-ipc-server=" + session.ipc_server,
                                           "--af=" + metered_filters(filters), str(self.source)],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **process_options)
        try:
            session._connect_ipc()
            session.observe_playback()
            wait_for(session.audio_levels, lambda result: result["active"])
            yield session
        finally:
            session.shutdown()

    def pcm_output(self, *, filters=None, volume=100, mute=False):
        target = self.folder / (uuid.uuid4().hex + ".pcm")
        args = [mpv, "--no-config", "--no-terminal", "--vo=null", "--ao=pcm", "--ao-pcm-waveheader=no",
                "--ao-pcm-file=" + str(target), "--audio-format=s16", "--audio-samplerate=48000",
                "--audio-channels=stereo", "--length=0.6", f"--volume={volume}",
                "--mute=" + ("yes" if mute else "no")]
        if filters:
            args.append("--af=" + filters)
        subprocess.run([*args, str(self.source)], check=True, timeout=10,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **process_options)
        return np.fromfile(target, dtype="<i2").reshape(-1, 2).astype(float) / 32768

    def test_real_decoder_rms_peak_gain_pause_mute_and_silence(self):
        with self.player() as session:
            measured = session.audio_levels()
            self.assertEqual(len(measured["channels"]), 2)
            for channel, expected in zip(measured["channels"], (-6.0206, -12.0412)):
                self.assertAlmostEqual(channel["peakDb"], expected, delta=0.08)
                self.assertAlmostEqual(channel["rmsDb"], expected - 3.0103, delta=0.15)
            self.assertTrue(session.set_property("volume", 50))
            lowered = wait_for(session.audio_levels, lambda value: value["active"] and value["channels"][0]["peakDb"] < -24)
            actual = self.pcm_output(filters=metered_filters(), volume=50)
            peak_db = 20 * np.log10(np.max(np.abs(actual), axis=0))
            rms_db = 20 * np.log10(np.sqrt(np.mean(actual * actual, axis=0)))
            for channel, peak, rms in zip(lowered["channels"], peak_db, rms_db):
                self.assertAlmostEqual(channel["peakDb"], peak, delta=0.10)
                self.assertAlmostEqual(channel["rmsDb"], rms, delta=0.15)
            self.assertTrue(session.set_property("pause", True))
            paused = wait_for(session.audio_levels, lambda value: not value["active"])
            self.assertTrue(all(channel["level"] == channel["peak"] == 0 for channel in paused["channels"]))
            self.assertTrue(session.set_property("pause", False))
            wait_for(session.audio_levels, lambda value: value["active"])
            self.assertTrue(session.set_property("mute", True))
            muted = wait_for(session.audio_levels, lambda value: value["muted"])
            self.assertTrue(all(channel["level"] == channel["peak"] == 0 for channel in muted["channels"]))
            self.assertFalse(np.any(self.pcm_output(filters=metered_filters(), mute=True)))
            self.assertTrue(session.set_property("mute", False))
            self.assertTrue(session.set_property("volume", 100))
            # Silence must come from decoded samples, without relying on mute.
            self.assertTrue(session.set_property("af", metered_filters("lavfi=[volume=0]")))
            silent = wait_for(session.audio_levels, lambda value: value["active"] and all(c["peak"] == 0 for c in value["channels"]))
            self.assertFalse(silent["muted"])

    def test_normalization_is_measured_and_meter_is_sample_transparent(self):
        normalization = "lavfi=[volume=-6dB]"
        with self.player(normalization) as session:
            reading = session.audio_levels()
            self.assertAlmostEqual(reading["channels"][0]["peakDb"], -12.0206, delta=0.08)
        plain = self.pcm_output(filters=normalization)
        metered = self.pcm_output(filters=metered_filters(normalization))
        self.assertEqual(plain.shape, metered.shape)
        self.assertTrue(np.array_equal(plain, metered), "The meter altered audio samples")
        self.assertAlmostEqual(20 * math.log10(np.max(np.abs(metered[:, 0]))), -12.0206, delta=0.08)

    def test_stale_or_missing_telemetry_never_becomes_a_fake_meter(self):
        self.assertFalse(levels_from_telemetry({})["available"])
        values = {METER_PROPERTY: {"lavfi.astats.1.RMS_level": "-9.03", "lavfi.astats.1.Peak_level": "-6.02"},
                  "volume": 100, "mute": False, "pause": False, "core-idle": False, "replaygain": "no"}
        self.assertTrue(levels_from_telemetry(values)["active"])
        self.assertFalse(levels_from_telemetry(values, clock_current=False)["available"])
        values["replaygain"] = "track"
        self.assertFalse(levels_from_telemetry(values)["available"])


if __name__ == "__main__":
    unittest.main()

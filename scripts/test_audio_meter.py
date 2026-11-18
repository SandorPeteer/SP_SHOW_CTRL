"""Validate the live RMS/peak meter against known PCM and mpv's actual PCM output."""
from contextlib import contextmanager, ExitStack
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
        self.surround = self.folder / "surround.wav"
        surround = np.column_stack([a * np.sin(t * 2 * np.pi * f)
                                    for a, f in zip((.1, .2, .3, .4, .5, .6),
                                                    (100, 200, 300, 400, 500, 600))])
        with wave.open(str(self.surround), "wb") as stream:
            stream.setparams((6, 2, 48000, 0, "NONE", ""))
            stream.writeframes((surround * 32767).astype("<i2").tobytes())

    def tearDown(self):
        self.tmp.cleanup()

    @contextmanager
    def player(self, filters=None, *, source=None, output_channels=None, muted=False, start=0):
        session = MpvIpcSession(mpv, name="METER_" + uuid.uuid4().hex,
                                second_screen_left=0, second_screen_top=0, fullscreen=False)
        if os.name != "nt":
            session.ipc_server = str(self.folder / (uuid.uuid4().hex[:8] + ".sock"))
        options = ["--audio-channels=" + output_channels] if output_channels else []
        session._proc = subprocess.Popen([mpv, "--no-config", "--no-terminal", "--ao=null", "--vo=null",
                                           "--volume=100", "--keep-open=yes", *options,
                                           "--mute=" + ("yes" if muted else "no"), f"--start={start}",
                                           "--input-ipc-server=" + session.ipc_server,
                                           "--af=" + metered_filters(filters), str(source or self.source)],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **process_options)
        try:
            session._connect_ipc()
            session.observe_playback()
            wait_for(session.audio_levels, lambda result: result["available"] and result["active"] != muted)
            yield session
        finally:
            session.shutdown()

    def pcm_output(self, *, filters=None, volume=100, mute=False, source=None,
                   output_channels="stereo", sample_format="s16"):
        target = self.folder / (uuid.uuid4().hex + ".pcm")
        args = [mpv, "--no-config", "--no-terminal", "--vo=null", "--ao=pcm", "--ao-pcm-waveheader=no",
                "--ao-pcm-file=" + str(target), "--audio-format=" + sample_format, "--audio-samplerate=48000",
                "--audio-channels=" + output_channels, f"--volume={volume}",
                "--mute=" + ("yes" if mute else "no")]
        if filters:
            args.append("--af=" + filters)
        subprocess.run([*args, str(source or self.source)], check=True, timeout=10,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **process_options)
        data = np.fromfile(target, dtype="<f4" if sample_format == "float" else "<i2").reshape(-1, 2).astype(float)
        return data if sample_format == "float" else data / 32768

    def test_surround_downmix_matches_real_output_without_changing_samples(self):
        for volume in (100, 50):
            with self.subTest(volume=volume):
                plain = self.pcm_output(source=self.surround, volume=volume)
                measured_chain = metered_filters(output_channels="stereo")
                metered = self.pcm_output(source=self.surround, filters=measured_chain, volume=volume)
                self.assertTrue(np.array_equal(plain, metered), "Moving mpv's downmix altered output PCM")
                with self.player(source=self.surround, output_channels="stereo") as session:
                    self.assertEqual(session.get_property("audio-params")["channel-count"], 6)
                    self.assertEqual(session.get_property("audio-out-params")["channel-count"], 2)
                    session.set_property("volume", volume)
                    reading = wait_for(session.audio_levels, lambda value: value["active"] and
                                       (volume == 100 or value["channels"][0]["peakDb"] < -20))
                    self.assertEqual(len(reading["channels"]), 2)
                    for channel, actual in zip(reading["channels"], metered.T):
                        self.assertAlmostEqual(channel["peakDb"], 20 * np.log10(np.max(np.abs(actual))), delta=.12)
                        self.assertAlmostEqual(channel["rmsDb"], 20 * np.log10(np.sqrt(np.mean(actual ** 2))), delta=.2)

    def test_normalized_downmix_rounding_is_bounded(self):
        normalization = "lavfi=[volume=-6dB]"
        for sample_format, bound in (("s16", 1 / 32768), ("float", 0)):
            for volume in (100, 50):
                with self.subTest(sample_format=sample_format, volume=volume):
                    plain = self.pcm_output(source=self.surround, filters=normalization,
                                            volume=volume, sample_format=sample_format)
                    metered = self.pcm_output(source=self.surround,
                                              filters=metered_filters(normalization, output_channels="stereo"),
                                              volume=volume, sample_format=sample_format)
                    self.assertEqual(plain.shape, metered.shape)
                    # Splitting float downmix and final sample conversion can
                    # round differently: ≤1 s16 LSB; float32 stays bit-identical.
                    self.assertLessEqual(float(np.max(np.abs(plain - metered))), bound)

    def test_same_session_releases_previous_layout_and_rebinds_unchanged_output(self):
        # Production default keeps surround; a stereo device must still downmix.
        for device in (None, "stereo"):
            with self.subTest(device=device), self.player(output_channels=device) as session:
                for source, count in ((self.surround, 2 if device else 6),
                                      (self.source, 2), (self.surround, 2 if device else 6)):
                    session.loadfile(str(source))
                    reading = wait_for(session.audio_levels, lambda value: value["active"] and
                                       len(value["channels"]) == count)
                    self.assertEqual(session.get_property("audio-out-params")["channel-count"], count)
                    self.assertEqual(len(reading["channels"]), count)

    def test_output_layout_change_does_not_keep_stereo_constraint(self):
        with self.player(source=self.surround) as session:
            for layout, count in (("stereo", 2), ("auto-safe", 6)):
                self.assertTrue(session.set_property("audio-channels", layout))
                wait_for(session.audio_levels, lambda value: value["active"] and len(value["channels"]) == count)
                self.assertEqual(session.get_property("audio-out-params")["channel-count"], count)

    def test_real_two_player_handoff_and_explicit_mix(self):
        from unittest.mock import patch
        from scripts.test_audio_routing import AudioRoutingTests
        from player import App, MediaRunner, Settings
        fixture = AudioRoutingTests()
        fixture.setUp()
        app = fixture.app
        with ExitStack() as stack:
            a = stack.enter_context(self.player())
            b = stack.enter_context(self.player(muted=True))
            runner = app.audio_runner = MediaRunner(Settings())
            app.video_runner._sess = b
            def attach(session):
                runner._proc = session._proc
                runner._media_clock = session
                runner._playing_cue = fixture.cue
                runner._backend = "mpv"
            attach(a)
            wait_for(lambda: a.get_property("time-pos"), lambda pos: pos is not None and pos > .15)
            self.assertTrue(app._ppt_apply_sound())
            pause_pos = app._paused_a[1]
            self.assertGreater(pause_pos, .1)
            self.assertIsNotNone(a._proc.poll())
            wait_for(b.audio_levels, lambda value: value["active"] and value["channels"][0]["level"] > 0)

            def resume(cue, pos, **_):
                nonlocal a
                self.assertTrue(b.get_property("mute"), "B must acknowledge mute before A is launched")
                a = stack.enter_context(self.player(start=pos))
                attach(a)
            with patch.object(runner, "play_at", side_effect=resume):
                App._transport_play_pause(app, "A")
            self.assertTrue(a.audio_levels()["active"])
            self.assertGreaterEqual(a.get_property("time-pos"), pause_pos - .02)
            self.assertTrue(b.get_property("mute"))
            self.assertTrue(app._toggle_audio_mix())
            self.assertTrue(b.get_property("mute"), "Mix ON must not unmute/start B")
            app.var_ppt_sound.set(True)
            self.assertTrue(app._ppt_apply_sound())
            wait_for(b.audio_levels, lambda value: value["active"])
            self.assertTrue(a.audio_levels()["active"])
            before = a.get_property("time-pos")
            wait_for(lambda: a.get_property("time-pos"), lambda pos: pos > before + .05)
            self.assertTrue(app._toggle_audio_mix())
            self.assertFalse(app.audio_mix)
            self.assertIsNotNone(a._proc.poll())
            self.assertTrue(b.audio_levels()["active"])
            retained = app._paused_a
            b.command(["seek", 7.9, "absolute", "exact"])
            wait_for(lambda: b.get_property("eof-reached"), bool)
            self.assertFalse(runner.is_playing(), "B EOF must not restart A")
            self.assertEqual(app._paused_a, retained)

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

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

    def test_production_output_image_to_muted_video_then_presentation_audio(self):
        from unittest.mock import patch
        from PIL import Image
        from player import App, Cue, OutputRunner, Settings
        from scripts.test_audio_routing import AudioRoutingTests
        from scripts.windows_show_rehearsal import null_audio_command, audio_diagnostics
        ffmpeg = (str(root / "dist/SP Show Control/_internal/tools/ffmpeg/ffmpeg.exe")
                  if sys.platform == "win32" else shutil.which("ffmpeg"))
        clip = self.folder / "slide.mp4"
        still = self.folder / "slide.png"
        Image.new("RGB", (160, 90), "red").save(still)
        subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                        "color=c=red:size=160x90:rate=10:duration=4", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=4", "-c:v", "mpeg4", "-c:a", "aac",
                        "-shortest", str(clip)], check=True, timeout=10, **process_options)
        # The real persistent-output start contains --no-config. Its fixture AO
        # must be an explicit CLI option, not an ignored MPV_HOME preference.
        (self.folder / "mpv.conf").write_text("ao=null\n")
        session = MpvIpcSession(mpv, name="OUTPUT_" + uuid.uuid4().hex,
                                second_screen_left=0, second_screen_top=0, fullscreen=False)
        if os.name != "nt":
            session.ipc_server = str(self.folder / "output.sock")
        original_popen = subprocess.Popen
        def launch(command, *args, **kwargs):
            command = null_audio_command(command, mpv)
            self.assertIn("--no-config", command)
            self.assertIn("--ao=null", command)
            return original_popen([*command, "--vo=null"], *args, **kwargs)
        fixture = AudioRoutingTests()
        fixture.setUp()
        app = fixture.app
        app.audio_runner.is_playing.return_value = False
        app.audio_runner._media_clock = None
        app.video_runner = output = OutputRunner(Settings())
        output._sess = session
        app._vu_snapshot = lambda deck: App._vu_snapshot(app, deck)
        try:
            with patch.dict(os.environ, {"MPV_HOME": str(self.folder)}), patch("subprocess.Popen", side_effect=launch):
                session.start()
            with patch.object(output, "ensure_window", return_value=True):
                output.play_for_deck("B", Cue(id="still", path=str(still), kind="image"))
                wait_for(lambda: session.get_property("path"), lambda value: value == str(still))
                output.play_for_deck("B", Cue(id="clip", path=str(clip), kind="video"))
            wait_for(lambda: session.get_property("path"), lambda value: value == str(clip))
            wait_for(lambda: session.get_property("current-ao"), lambda value: value == "null")
            muted = wait_for(session.audio_levels, lambda value: value["available"] and value["muted"])
            self.assertFalse(muted["active"])
            self.assertEqual(session.get_property("volume"), 0)
            self.assertTrue(app._ppt_apply_sound())
            sounding = wait_for(lambda: app._vu_snapshot("B"), lambda value: value["active"] and
                                any(channel["level"] > .01 for channel in value["channels"]))
            self.assertFalse(sounding["muted"])
            self.assertEqual(app._active_audio_meter()["source"], "presentation")
        except Exception:
            print("Production output diagnostic:", audio_diagnostics(app), flush=True)
            raise
        finally:
            session.shutdown()

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

    def test_direct_projection_video_sound_toggle_and_mix(self):
        from types import MethodType
        from unittest.mock import patch
        from scripts.test_audio_routing import AudioRoutingTests
        from player import App, Cue, MediaRunner, OutputRunner, Settings
        ffmpeg = (str(root / "dist/SP Show Control/_internal/tools/ffmpeg/ffmpeg.exe")
                  if sys.platform == "win32" else shutil.which("ffmpeg"))
        clip = self.folder / "direct-projection.mp4"
        subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                        "color=c=blue:size=160x90:rate=10:duration=12", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=12", "-c:v", "mpeg4", "-c:a", "aac",
                        "-shortest", str(clip)], check=True, timeout=10, **process_options)
        fixture = AudioRoutingTests()
        fixture.setUp()
        app = fixture.app
        app._ppt_running, app._ppt_slides = False, []
        fixture.cue.path = str(self.source)
        app.var_ppt_sound.set(False)
        app._ppt_sound_preference = False
        for name in ("_toggle_projection_sound", "_apply_projection_sound", "_ppt_apply_sound", "_transport_is_active"):
            setattr(app, name, MethodType(getattr(App, name), app))
        app._vu_snapshot = lambda deck: App._vu_snapshot(app, deck)
        with ExitStack() as stack:
            a = stack.enter_context(self.player())
            b = stack.enter_context(self.player(source=clip, muted=True))
            runner = app.audio_runner = MediaRunner(Settings())
            output = app.video_runner = OutputRunner(Settings())
            output._sess = b
            direct_cue = Cue(id="projection", path=str(clip), kind="video", volume_percent=65)
            with patch.object(output, "ensure_window", return_value=True):
                output.play_for_deck("B", direct_cue)
            wait_for(lambda: b.get_property("path"), lambda value: value == str(clip))
            self.assertTrue(b.get_property("mute"), "Low-level B load must remain silent")
            self.assertFalse(output.is_paused())
            def attach(session):
                runner._proc = session._proc
                runner._media_clock = session
                runner._playing_cue = fixture.cue
                runner._backend = "mpv"
            attach(a)
            before_takeover = wait_for(lambda: a.get_property("time-pos"),
                                       lambda value: value is not None and value > .15)
            wait_for(lambda: b.observed("time-pos", "core-idle"),
                     lambda value: isinstance(value[0], (int, float)) and value[1] is False)
            self.assertEqual((app._transport_is_active("A"), app._transport_is_active("B")), (True, True))
            self.assertTrue(app._toggle_projection_sound())
            self.assertTrue(app.var_ppt_sound.get())
            self.assertFalse(b.get_property("mute"), "A direct B video must unmute without a PPT running")
            self.assertEqual(b.get_property("volume"), 65)
            paused = app._paused_a
            self.assertEqual(paused[0], fixture.cue.id)
            self.assertGreaterEqual(paused[1], before_takeover)
            self.assertIsNotNone(a._proc.poll(), "Normal mode must silence A before B sounds")
            wait_for(lambda: app._vu_snapshot("B"), lambda value: value["active"] and
                     any(channel["level"] > .01 for channel in value["channels"]))
            self.assertEqual((app._transport_is_active("A"), app._transport_is_active("B")), (False, True))

            before_mute = b.get_property("time-pos")
            self.assertTrue(app._toggle_projection_sound())
            self.assertFalse(app.var_ppt_sound.get())
            self.assertTrue(b.get_property("mute"))
            wait_for(b.audio_levels, lambda value: value["muted"] and not value["active"])
            wait_for(lambda: b.get_property("time-pos"), lambda value: value > before_mute + .1)
            self.assertFalse(runner.is_playing(), "Muting B must not automatically resume A")
            self.assertEqual(app._paused_a, paused)

            def resume(cue, pos, **_):
                nonlocal a
                self.assertTrue(b.get_property("mute"))
                a = stack.enter_context(self.player(start=pos))
                attach(a)
            with patch.object(runner, "play_at", side_effect=resume):
                App._transport_play_pause(app, "A")
            self.assertTrue(a.audio_levels()["active"])
            self.assertTrue(app._toggle_audio_mix())
            self.assertTrue(b.get_property("mute"), "Mix ON alone must not enable projection sound")
            active_process = runner._proc
            self.assertTrue(app._toggle_projection_sound())
            wait_for(b.audio_levels, lambda value: value["active"] and value["channels"][0]["level"] > .01)
            self.assertTrue(a.audio_levels()["active"])
            clocks = (a.get_property("time-pos"), b.get_property("time-pos"))
            wait_for(lambda: (a.get_property("time-pos"), b.get_property("time-pos")),
                     lambda values: values[0] > clocks[0] + .1 and values[1] > clocks[1] + .1)
            self.assertEqual((app._transport_is_active("A"), app._transport_is_active("B")), (True, True))
            self.assertIs(runner._proc, active_process)
            self.assertEqual(app._last_audio_source, "B")
            self.assertTrue(app._toggle_projection_sound())
            self.assertTrue(b.get_property("mute"))
            self.assertIs(runner._proc, active_process, "Muting B in Mix must leave the existing A player alone")
            self.assertTrue(a.audio_levels()["active"])
            self.assertEqual((app._transport_is_active("A"), app._transport_is_active("B")), (True, True))

    def test_real_media_runner_attached_clock_drives_play_highlight(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from player import App, Cue, MediaRunner, Settings
        runner = MediaRunner(Settings())
        runner.observe_clock = True
        def clock_session(*args, **kwargs):
            session = MpvIpcSession(*args, **kwargs)
            if os.name != "nt":
                session.ipc_server = str(self.folder / "attach.sock")
            return session
        clock = process = None
        try:
            # Exercise the real MediaRunner attach path on both CI Windows and
            # the local Unix IPC adapter; all audio/video outputs remain null.
            with patch("player.sys.platform", "win32"), patch("player.MpvIpcSession", side_effect=clock_session), \
                    patch("player._no_console_subprocess_kwargs", return_value=process_options):
                process = runner._proc = runner._spawn_player("mpv", [mpv, "--no-config", "--no-terminal",
                    "--ao=null", "--vo=null", "--keep-open=yes", str(self.source)])
                runner._playing_cue = Cue(id="attached-a", path=str(self.source), kind="audio")
                runner._backend = "mpv"
                clock = runner._media_clock
                wait_for(lambda: clock.observed("time-pos")[0],
                         lambda value: isinstance(value, (int, float)) and value > .1)
                app = SimpleNamespace(audio_runner=runner, video_runner=SimpleNamespace(owner_deck=None))
                self.assertTrue(App._transport_is_active(app, "A"),
                                "The attached observer must use its MediaRunner's actual process state")
                self.assertTrue(clock.set_property("pause", True))
                wait_for(lambda: clock.observed("pause")[0], lambda value: value is True)
                self.assertFalse(App._transport_is_active(app, "A"))
        finally:
            runner.stop()
            if clock is not None:
                clock.shutdown()
            if process is not None and process.stderr is not None:
                process.stderr.close()

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

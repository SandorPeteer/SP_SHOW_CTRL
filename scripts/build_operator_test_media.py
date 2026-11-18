#!/usr/bin/env python3
"""Build deterministic, synthetic operator fixtures; never open an AV device.

Requires FFmpeg/ffprobe, Pillow and numpy. The same tool versions and font produce
the same media. All levels describe generated digital samples, not a calibrated
speaker, projector or physical AV latency measurement.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

WIDTH, HEIGHT, FPS, RATE = 1920, 1080, 25, 48000
DISCLAIMER = ("Synthetic operator test stimulus, not a physical calibration reference. "
              "Compressed H.264/AAC can alter pixels and sample peaks; use the PNG/WAV "
              "for original pixel/sample values. No physical AV latency is asserted.")


def run(command, **kwargs):
    return subprocess.run([str(arg) for arg in command], check=True, **kwargs)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def find_font(requested):
    candidates = [requested] if requested else [
        "/System/Library/Fonts/Menlo.ttc", "C:/Windows/Fonts/consola.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise FileNotFoundError("Pass --font with a readable TrueType/OpenType font.")


class Graphics:
    def __init__(self, font):
        self.font_path, self.fonts = font, {}

    def text(self, image, xy, text, size=36, fill="white", anchor="mm"):
        if size not in self.fonts:
            self.fonts[size] = ImageFont.truetype(str(self.font_path), size)
        ImageDraw.Draw(image).text(xy, text, font=self.fonts[size], fill=fill, anchor=anchor)

    def monoscope(self):
        image = Image.new("RGB", (WIDTH, HEIGHT), (20, 24, 30))
        draw = ImageDraw.Draw(image)
        draw.line((0, 0, WIDTH - 1, HEIGHT - 1), fill=(115, 115, 115), width=2)
        draw.line((WIDTH - 1, 0, 0, HEIGHT - 1), fill=(115, 115, 115), width=2)
        draw.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), outline="white", width=2)
        for fraction, color in ((.05, "cyan"), (.10, "yellow")):
            x, y = round(WIDTH * fraction), round(HEIGHT * fraction)
            draw.rectangle((x, y, WIDTH - 1 - x, HEIGHT - 1 - y), outline=color, width=2)
        for xy, text, anchor in (((32, 25), "TOP LEFT", "lt"),
                                 ((1888, 25), "TOP RIGHT", "rt"),
                                 ((32, 1055), "BOTTOM LEFT", "lb"),
                                 ((1888, 1055), "BOTTOM RIGHT", "rb")):
            self.text(image, xy, text, 28, anchor=anchor)
        self.text(image, (960, 78), "PROJECTOR MONOSCOPE  /  1920 x 1080  /  1:1 PIXELS", 34)
        self.text(image, (960, 132), "CYAN = 5% SAFE AREA    YELLOW = 10% SAFE AREA", 26)
        colors = [("R", (255, 0, 0)), ("G", (0, 255, 0)), ("B", (0, 0, 255)),
                  ("C", (0, 255, 255)), ("M", (255, 0, 255)), ("Y", (255, 255, 0)),
                  ("W", (255, 255, 255)), ("K", (0, 0, 0))]
        for i, (label, color) in enumerate(colors):
            x = 200 + 190 * i
            draw.rectangle((x, 170, x + 189, 280), fill=color)
            self.text(image, (x + 95, 302), label, 28)
        draw.ellipse((700, 280, 1220, 800), outline="white", width=4)
        draw.ellipse((830, 410, 1090, 670), outline=(140, 140, 140), width=2)
        draw.line((650, 540, 1270, 540), fill="cyan", width=2)
        draw.line((960, 250, 960, 830), fill="cyan", width=2)
        draw.rectangle((956, 536, 964, 544), fill="white")
        self.text(image, (960, 725), "TRUE CIRCLE", 30)
        self.text(image, (960, 765), "SQUARE PIXELS / SAR 1:1", 24)
        # A small exact-pixel grid and directional resolution targets. They are
        # deliberately supplied as PNG too, since 4:2:0 video is not pixel-exact.
        draw.rectangle((240, 400, 559, 639), fill="black")
        for x in range(240, 560, 8):
            draw.line((x, 400, x, 639), fill="white", width=1)
        for y in range(400, 640, 8):
            draw.line((240, y, 559, y), fill="white", width=1)
        self.text(image, (400, 675), "8px GRID / 1px LINES", 24)
        for row, stripe in enumerate((1, 2, 4, 8)):
            y = 385 + row * 70
            draw.rectangle((1360, y, 1679, y + 49), fill="black")
            for x in range(1360, 1520, stripe * 2):
                draw.rectangle((x, y, x + stripe - 1, y + 49), fill="white")
            for yy in range(y, y + 50, stripe * 2):
                draw.rectangle((1520, yy, 1679, min(y + 49, yy + stripe - 1)), fill="white")
            self.text(image, (1700, y + 25), f"{stripe}px", 22, anchor="lm")
        self.text(image, (1520, 700), "RESOLUTION TARGETS", 24)
        for i in range(11):
            x, value = 200 + i * 138, round(i * 255 / 10)
            draw.rectangle((x, 850, x + 137, 925), fill=(value,) * 3)
            self.text(image, (x + 69, 948), str(value), 22)
        self.text(image, (960, 995), "OPERATOR TEST ONLY - COMPRESSED VIDEO IS NOT A CALIBRATION REFERENCE", 24)
        return image

    def standby(self):
        image = Image.new("RGB", (WIDTH, HEIGHT), (12, 26, 45))
        draw = ImageDraw.Draw(image)
        draw.rectangle((42, 42, 1877, 1037), outline=(51, 100, 143), width=3)
        draw.ellipse((730, 100, 1190, 560), outline=(30, 61, 89), width=8)
        draw.line((480, 680, 1440, 680), fill=(43, 184, 212), width=4)
        self.text(image, (960, 440), "SHOW CONTROL TEST", 90)
        self.text(image, (960, 585), "STANDBY", 80, fill=(255, 191, 77))
        self.text(image, (960, 780), "Original synthetic test artwork / 1920 x 1080", 30)
        return image

    def timecode_frame(self, frame, seconds, title, *, black_opening=False, countdown=False):
        if black_opening and frame < FPS:
            return Image.new("RGB", (WIDTH, HEIGHT), "black")
        flash = frame % FPS == 0
        background, ink = ((245, 245, 245), (12, 20, 31)) if flash else ((12, 26, 45), (245, 245, 245))
        image = Image.new("RGB", (WIDTH, HEIGHT), background)
        draw = ImageDraw.Draw(image)
        draw.rectangle((24, 24, 1895, 1055), outline=ink, width=4)
        self.text(image, (960, 140), title, 48, fill=ink)
        self.text(image, (960, 250), "1920 x 1080 / 25 fps / 48 kHz stereo", 32, fill=ink)
        shown_frame = seconds * FPS - frame if countdown else frame
        whole, ff = divmod(shown_frame, FPS)
        hh, remainder = divmod(whole, 3600)
        mm, ss = divmod(remainder, 60)
        self.text(image, (960, 480), f"{hh:02}:{mm:02}:{ss:02}:{ff:02}", 150, fill=ink)
        self.text(image, (960, 645), f"FRAME {frame:04} / {seconds * FPS - 1:04}", 64, fill=ink)
        self.text(image, (960, 755), "REMAINING TO EOF" if countdown else "HH:MM:SS:FF / FRAME COUNTER", 36, fill=ink)
        self.text(image, (960, 835), "1 FRAME FLASH + 40 ms BEEP AT EACH WHOLE SECOND", 32, fill=ink)
        self.text(image, (960, 975), "GENERATED SYNC STIMULUS - PHYSICAL LATENCY IS NOT PRE-CALIBRATED", 27, fill=ink)
        draw.rectangle((130, 900, 1789, 925), outline=ink, width=2)
        extent = 130 + round(1659 * frame / (seconds * FPS - 1))
        draw.rectangle((130, 900, extent, 925), fill=ink)
        return image


def tone(seconds, frequencies, peaks, fade=.01):
    count = round(seconds * RATE)
    t = np.arange(count, dtype=np.float64) / RATE
    channels = []
    for frequency, peak in zip(frequencies, peaks):
        signal = np.sin(2 * np.pi * frequency * t) if peak is not None else np.zeros(count)
        if peak is not None:
            # The specified value is a digital sample peak, including tones
            # such as 8 kHz whose sampled sine does not reach exactly 1.0.
            signal *= 10 ** (peak / 20) / np.max(np.abs(signal))
        channels.append(signal)
    samples = np.column_stack(channels)
    ramp = min(round(fade * RATE), count // 2)
    if ramp:
        edge = np.sin(np.linspace(0, np.pi / 2, ramp)) ** 2
        samples[:ramp] *= edge[:, None]
        samples[-ramp:] *= edge[::-1, None]
    return samples


def write_wav(path, samples):
    pcm = np.rint(np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as target:
        target.setparams((2, 2, RATE, 0, "NONE", ""))
        target.writeframes(pcm.tobytes())
    return pcm.astype(np.float64) / 32768


def levels(samples):
    peak = np.max(np.abs(samples), axis=0)
    rms = np.sqrt(np.mean(samples * samples, axis=0))
    return {name: [round(20 * math.log10(value), 5) if value else None for value in values]
            for name, values in (("peak_dbfs", peak), ("rms_dbfs", rms))}


def segmented_audio(kind):
    sections = ([(0, 2, "Silence", (1000, 1000), (None, None)),
                 (2, 6, "Left only", (1000, 1000), (-18, None)),
                 (6, 10, "Right only", (1000, 1000), (None, -18)),
                 (10, 14, "Both channels", (1000, 1000), (-18, -18)),
                 (14, 18, "Both channels", (1000, 1000), (-6, -6)),
                 (18, 22, "Different L/R levels", (1000, 500), (-6, -18)),
                 (22, 24, "Silence", (1000, 1000), (None, None))] if kind == "levels" else
                [(0, 2, "Silence", (80, 80), (None, None)),
                 (2, 6, "Low frequency", (80, 80), (-24, -24)),
                 (6, 10, "Mid frequency", (1000, 1000), (-18, -18)),
                 (10, 14, "High frequency", (8000, 8000), (-12, -12)),
                 (14, 20, "Log frequency/amplitude sweep", (80, 8000), (-36, -6)),
                 (20, 22, "Stereo contrast", (1000, 250), (-6, -18)),
                 (22, 24, "Silence", (1000, 1000), (None, None))])
    samples = np.zeros((24 * RATE, 2), dtype=np.float64)
    descriptors = []
    for start, end, label, frequency, peak in sections:
        descriptor = dict(start_seconds=start, end_seconds=end, label=label, edge_fade_ms=10)
        if label == "Log frequency/amplitude sweep":
            t = np.arange((end - start) * RATE) / RATE
            phase = 2 * np.pi * 80 * (np.exp(t * math.log(100) / 6) - 1) / (math.log(100) / 6)
            amplitude = 10 ** ((-36 + 30 * t / 6) / 20)
            mono = np.sin(phase) * amplitude
            edge = np.sin(np.linspace(0, np.pi / 2, 480)) ** 2
            mono[:480] *= edge
            mono[-480:] *= edge[::-1]
            part = np.column_stack((mono, mono))
            descriptor.update(frequency_hz_start=80, frequency_hz_end=8000,
                              peak_envelope_dbfs_start=-36, peak_envelope_dbfs_end=-6)
        else:
            part = tone(end - start, frequency, peak)
            descriptor.update(frequency_hz_lr=list(frequency), nominal_peak_dbfs_lr=list(peak))
        samples[start * RATE:end * RATE] = part
        descriptors.append(descriptor)
    return samples, descriptors


def beeps(seconds):
    samples = np.zeros((seconds * RATE, 2))
    beep = tone(.04, (1000, 1000), (-12, -12), fade=.002)
    for second in range(seconds):
        samples[second * RATE:second * RATE + len(beep)] = beep
    return samples


def encode_video(ffmpeg, path, audio, frames):
    args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo",
            "-pixel_format", "rgb24", "-video_size", f"{WIDTH}x{HEIGHT}", "-framerate", FPS,
            "-i", "pipe:0", "-i", audio, "-map", "0:v:0", "-map", "1:a:0",
            "-vf", "scale=iw:ih:out_color_matrix=bt709:out_range=tv,setsar=1",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-threads", "2",
            "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709",
            "-color_trc", "bt709", "-color_range", "tv", "-c:a", "aac", "-b:a", "192k", "-ar", RATE,
            "-ac", "2", "-movflags", "+faststart", "-metadata", "comment=" + DISCLAIMER, path]
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen([str(arg) for arg in args], stdin=subprocess.PIPE, stderr=errors)
        try:
            for frame in frames:
                process.stdin.write(frame.tobytes())
            process.stdin.close()
            if process.wait(timeout=60):
                errors.seek(0)
                raise RuntimeError(errors.read().decode(errors="replace"))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            if not process.stdin.closed:
                process.stdin.close()


def probe(ffprobe, path):
    return json.loads(run([ffprobe, "-v", "error", "-show_streams", "-show_format",
                           "-of", "json", path], capture_output=True).stdout)


def check_sync(ffmpeg, ffprobe, path, source, seconds, black_opening=False):
    raw = run([ffmpeg, "-v", "error", "-i", path, "-map", "0:a:0", "-f", "f32le",
               "-acodec", "pcm_f32le", "pipe:1"], capture_output=True).stdout
    decoded = np.frombuffer(raw, dtype="<f4").reshape(-1, 2)
    offsets = []
    for second in range(seconds):
        at, count, radius = second * RATE, round(.04 * RATE), 128
        start = max(0, at - radius)
        window = decoded[start:at + count + radius, 0]
        correlation = np.correlate(window, source[at:at + count, 0], mode="valid")
        offset = int(np.argmax(correlation)) + start - at
        assert abs(offset) <= 2, (path.name, second, "decoded beep shift in samples", offset)
        offsets.append(offset)
    # Decode known adjacent frames; the whole flash frame must be visibly
    # distinct, while the black-opening fixture really has 25 black frames.
    indices = [0, 1, 24, 25, 26, seconds * FPS - 1]
    expression = "+".join(f"eq(n\\,{index})" for index in indices)
    pixels = run([ffmpeg, "-v", "error", "-i", path, "-vf", "select=" + expression,
                  "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                 capture_output=True).stdout
    frames = np.frombuffer(pixels, dtype=np.uint8).reshape(-1, HEIGHT, WIDTH, 3)
    assert len(frames) == len(indices), (path.name, len(frames))
    means = [round(float(frame.mean()), 3) for frame in frames]
    assert means[3] > 180 and means[4] < 60, (path.name, means)
    if black_opening:
        assert max(means[:3]) < 1, (path.name, "opening not black", means)
    else:
        assert means[0] > 180 and means[1] < 60, (path.name, means)
    packets = json.loads(run([ffprobe, "-v", "error", "-select_streams", "a:0", "-show_packets",
        "-read_intervals", "%+#2", "-of", "json", path], capture_output=True).stdout)["packets"]
    priming = next((entry.get("skip_samples", 0) for entry in packets[0].get("side_data_list", [])
                    if entry.get("side_data_type") == "Skip Samples"), 0)
    assert packets[0]["pts"] + priming == 0, (path.name, "uncompensated encoder priming", packets[0])
    return dict(decoded_beep_offset_samples=offsets, max_abs_offset_ms=max(map(abs, offsets)) * 1000 / RATE,
                aac_priming_samples=priming, first_audio_packets=packets, decoded_audio_samples=len(decoded),
                checked_video_frames=indices, checked_frame_mean_rgb=means), frames


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg"))
    parser.add_argument("--ffprobe", default=shutil.which("ffprobe"))
    parser.add_argument("--font")
    parser.add_argument("--preview", type=Path, help="Optional validation contact sheet outside the Media folder")
    args = parser.parse_args()
    if not args.ffmpeg or not args.ffprobe:
        parser.error("FFmpeg and ffprobe are required")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    font = find_font(args.font)
    graphics = Graphics(font)
    manifest = dict(schema_version=1, disclaimer=DISCLAIMER, sample_rate=RATE, video_fps=FPS,
                    timecode="non-drop HH:MM:SS:FF, frame zero at media presentation timestamp zero",
                    tools={name: run([program, "-version"], capture_output=True, text=True).stdout.splitlines()[0]
                           for name, program in (("ffmpeg", args.ffmpeg), ("ffprobe", args.ffprobe))},
                    font=dict(filename=font.name, sha256=sha256(font)), files=[])
    previews = []
    def record(path, **details):
        info = probe(args.ffprobe, path)
        streams = [{key: stream[key] for key in ("codec_type", "codec_name", "width", "height", "pix_fmt",
            "sample_aspect_ratio", "display_aspect_ratio", "r_frame_rate", "avg_frame_rate", "sample_rate",
            "channels", "channel_layout", "duration", "start_time", "nb_frames", "time_base",
            "color_range", "color_space", "color_transfer", "color_primaries") if key in stream}
                   for stream in info["streams"]]
        manifest["files"].append(dict(filename=path.name, bytes=path.stat().st_size, sha256=sha256(path),
                                      streams=streams, **details))
        print(json.dumps({"created": path.name, "bytes": path.stat().st_size,
                          "duration_seconds": details.get("duration_seconds")}), flush=True)
        return streams
    for name, kind in (("01_STEREO_LR_LEVELS.wav", "levels"), ("02_WAVEFORM_DYNAMICS.wav", "dynamics")):
        samples, sections = segmented_audio(kind)
        path = output / name
        actual = write_wav(path, samples)
        with wave.open(str(path), "rb") as saved:
            assert saved.getnframes() == 24 * RATE and saved.getnchannels() == 2 and saved.getframerate() == RATE
            decoded = np.frombuffer(saved.readframes(saved.getnframes()), dtype="<i2").reshape(-1, 2) / 32768
        assert np.array_equal(actual, decoded), (name, "WAV sample round-trip")
        for section in sections:
            part = actual[section["start_seconds"] * RATE:section["end_seconds"] * RATE]
            assert not part[0].any() and not part[-1].any(), (name, section["label"], "unfaded boundary")
            section["measured"] = levels(part)
            for target, measured in zip(section.get("nominal_peak_dbfs_lr", []), section["measured"]["peak_dbfs"]):
                if target is None:
                    assert measured is None
                else:
                    assert abs(target - measured) < .02, (name, section, measured)
        record(path, duration_seconds=24, audio_sections=sections, digital_level_definition="sample peak relative to full scale; null = digital silence")
    mono = graphics.monoscope()
    png_metadata = PngImagePlugin.PngInfo()
    png_metadata.add_text("Description", DISCLAIMER)
    path = output / "05_PROJECTOR_MONOSCOPE_1080P.png"
    mono.save(path, pnginfo=png_metadata)
    record(path, duration_seconds=None, original_rgb_pixels=True, outer_border_pixels=2, safe_areas_percent=[5, 10])
    path = output / "06_STILL_LOGO.png"
    graphics.standby().save(path, pnginfo=png_metadata)
    record(path, duration_seconds=None, original_artwork=True)
    with tempfile.TemporaryDirectory(prefix="operator-media-") as temporary:
        temp = Path(temporary)
        specs = [("03_AV_SYNC_TIMECODE.mp4", 20, "A/V SYNC TIMECODE", False, False),
                 ("04_BLACK_OPENING_TIMECODE.mp4", 20, "BLACK OPENING / TIMECODE", True, False),
                 ("07_END_OF_CUE.mp4", 6, "END OF CUE / REPEAT / STILL", False, True)]
        for name, seconds, title, black, countdown in specs:
            source = write_wav(temp / "beeps.wav", beeps(seconds))
            path = output / name
            encode_video(args.ffmpeg, path, temp / "beeps.wav", (graphics.timecode_frame(i, seconds, title,
                black_opening=black, countdown=countdown) for i in range(seconds * FPS)))
            validation, frames = check_sync(args.ffmpeg, args.ffprobe, path, source, seconds, black)
            previews.extend(Image.fromarray(frames[index]).resize((480, 270)) for index in (0, 3, 5))
            streams = record(path, duration_seconds=seconds, expected_video_frames=seconds * FPS,
                black_opening_frames=25 if black else 0,
                audio_sections=[dict(event_seconds=list(range(seconds)), duration_ms=40, frequency_hz=1000,
                                     nominal_peak_dbfs_lr=[-12, -12], edge_fade_ms=2)],
                flash_frames=list(range(25 if black else 0, seconds * FPS, FPS)), validation=validation)
            video, audio = streams
            assert video["codec_name"] == "h264" and video["pix_fmt"] == "yuv420p"
            assert video["width"] == WIDTH and video["height"] == HEIGHT and video["avg_frame_rate"] == "25/1"
            assert video["sample_aspect_ratio"] == "1:1" and video["color_space"] == "bt709"
            assert int(video["nb_frames"]) == seconds * FPS and float(video["start_time"]) == 0
            assert audio["codec_name"] == "aac" and audio["sample_rate"] == str(RATE) and audio["channels"] == 2
            assert float(audio["start_time"]) == 0 and abs(float(audio["duration"]) - seconds) < 1 / RATE
        write_wav(temp / "monoscope.wav", tone(20, (1000, 500), (-18, -18)))
        path = output / "05_PROJECTOR_MONOSCOPE_1080P.mp4"
        encode_video(args.ffmpeg, path, temp / "monoscope.wav", (mono for _ in range(20 * FPS)))
        streams = record(path, duration_seconds=20, expected_video_frames=500,
            audio_sections=[dict(start_seconds=0, end_seconds=20, frequency_hz_lr=[1000, 500],
                                 nominal_peak_dbfs_lr=[-18, -18], edge_fade_ms=10)])
        assert streams[0]["codec_name"] == "h264" and streams[0]["pix_fmt"] == "yuv420p" and int(streams[0]["nb_frames"]) == 500
        assert streams[0]["sample_aspect_ratio"] == "1:1" and streams[0]["color_space"] == "bt709"
    manifest["files"].sort(key=lambda item: item["filename"])
    manifest["source_script_sha256"] = sha256(Path(__file__))
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.preview:
        previews.append(mono.resize((480, 270)))
        contact = Image.new("RGB", (1440, math.ceil(len(previews) / 3) * 270), (35, 35, 35))
        for i, item in enumerate(previews):
            contact.paste(item, ((i % 3) * 480, (i // 3) * 270))
        args.preview.parent.mkdir(parents=True, exist_ok=True)
        contact.save(args.preview)
    print("Validated manifest:", output / "manifest.json", flush=True)


if __name__ == "__main__":
    main()

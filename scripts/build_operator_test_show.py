"""Build the portable operator rehearsal show after its Media assets are ready.

Developer dependency: python-pptx==1.0.2 and Pillow. Nothing is installed by the
resulting show. The PPTX embeds its MP4; the supplied cache is assembled directly
from those same assets, not claimed to be a verified Microsoft Office export.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
from types import MethodType
from unittest.mock import Mock, patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from player import App, Cue, Scene
from ppt_media import _moving_slides, prepare, read_timing

PPT_NAME = "08_PPT_LOOP_TEST.pptx"
MEDIA_NAMES = (
    "01_STEREO_LR_LEVELS.wav", "02_WAVEFORM_DYNAMICS.wav",
    "03_AV_SYNC_TIMECODE.mp4", "04_BLACK_OPENING_TIMECODE.mp4",
    "05_PROJECTOR_MONOSCOPE_1080P.mp4", "05_PROJECTOR_MONOSCOPE_1080P.png",
    "06_STILL_LOGO.png", "07_END_OF_CUE.mp4",
)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def make_presentation(folder: Path) -> dict:
    from PIL import Image, ImageDraw, ImageFont
    from pptx import Presentation
    from pptx.util import Inches

    source = folder / PPT_NAME
    prepared = source.with_name(source.name + ".mpv")
    prepared.mkdir(exist_ok=True)
    font = next((Path(name) for name in (
        "/System/Library/Fonts/Supplemental/Arial.ttf", "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf") if Path(name).is_file()), None)
    typography = lambda size: ImageFont.truetype(str(font), size) if font else ImageFont.load_default(size=size)

    def card(path, number, color, title, detail):
        image = Image.new("RGB", (1920, 1080), color)
        draw = ImageDraw.Draw(image)
        draw.rectangle((50, 50, 1870, 1030), outline="#ffffff", width=5)
        draw.text((100, 85), "SP SHOW CONTROL / PPT TEST", font=typography(58), fill="white")
        draw.text((100, 210), number, font=typography(310), fill="white")
        draw.text((110, 630), title, font=typography(76), fill="white")
        draw.text((110, 770), detail, font=typography(42), fill="white")
        draw.text((110, 940), "1920 x 1080 / 16:9", font=typography(32), fill="white")
        image.save(path)

    first, poster, last = prepared / "0001.png", prepared / "0002_poster.png", prepared / "0003.png"
    card(first, "01", "#1457a5", "FIRST STILL", "Still interval: 2 s or 5 s / Repeat OFF: manual")
    card(poster, "02", "#944c10", "EMBEDDED VIDEO + AUDIO", "07_END_OF_CUE.mp4 / 6 s / Sound switch controls audio")
    card(last, "03", "#147348", "FINAL STILL", "Repeat ON: returns to slide 01 / Repeat OFF: manual")
    movie = folder / "Media/07_END_OF_CUE.mp4"
    presentation = Presentation()
    presentation.slide_width, presentation.slide_height = Inches(13.333333), Inches(7.5)
    for index, image in enumerate((first, poster, last), 1):
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        if index == 2:
            slide.shapes.add_movie(str(movie), 0, 0, presentation.slide_width, presentation.slide_height,
                                   poster_frame_image=str(poster), mime_type="video/mp4")
            slide.notes_slide.notes_text_frame.text = (
                "The MP4 is embedded, not linked. The supplied .pptx.mpv cache plays the same MP4. "
                "PowerPoint autoplay and Office export have not been verified by this generator.")
        else:
            slide.shapes.add_picture(str(image), 0, 0, presentation.slide_width, presentation.slide_height)
    presentation.core_properties.title = "SP Show Control — PPT interval and embedded-video test"
    presentation.core_properties.subject = "Three slides; identical source/cache video; manual Office validation optional"
    presentation.save(source)
    shutil.copyfile(movie, prepared / "0002.mp4")
    metadata = dict(version=1, sha256=digest(source), slides=["png", "mp4", "png"], seconds=[None, None, None])
    (prepared / "slides.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    reopened = Presentation(source)
    assert len(reopened.slides) == 3
    with zipfile.ZipFile(source) as package:
        embedded = [name for name in package.namelist() if name.startswith("ppt/media/") and name.endswith(".mp4")]
        assert len(embedded) == 1
        assert hashlib.sha256(package.read(embedded[0])).hexdigest() == digest(movie)
    assert _moving_slides(source) == {2}
    return dict(file=PPT_NAME, slides=3, moving_slide=2, embedded_video="Media/07_END_OF_CUE.mp4",
                embedded_video_sha256=digest(movie), prepared_files=metadata["slides"],
                intervals_seconds=[2, 5], prepared_cache="directly assembled from identical source assets",
                limitation="Microsoft PowerPoint autoplay and Office export were not exercised.")


def show_document() -> dict:
    a, b, scenes = [], [], []

    def cue(deck, cue_id, filename, kind, title, **options):
        path = "./" + filename if filename == PPT_NAME else "./Media/" + filename
        value = Cue(id=cue_id, path=path, kind=kind, media_name=title, auto_play=False, **options)
        (a if deck == "A" else b).append(value)
        return value.id

    def scene(number, name, ids_a, ids_b, note):
        scenes.append(Scene(id=f"scene-{number:02d}", name=f"{number:02d} · {name}",
                            cue_ids_a=ids_a, cue_ids_b=ids_b, notes=note, auto_advance=False))

    still = cue("B", "b-still-logo", MEDIA_NAMES[6], "image", "Still · visszatérési logó")
    scope_image = cue("B", "b-monoscope-image", MEDIA_NAMES[5], "image", "Projektor · 1080p álló tesztábra")
    scope_video = cue("B", "b-monoscope-video", MEDIA_NAMES[4], "video", "Projektor · 1080p mozgó tesztábra")
    stereo = cue("A", "a-stereo", MEDIA_NAMES[0], "audio", "Sztereó · bal / jobb / szintek")
    waveform = cue("A", "a-waveform", MEDIA_NAMES[1], "audio", "Hullámforma · csend és dinamika")
    fade = cue("A", "a-fade", MEDIA_NAMES[1], "audio", "Fade · 8 s-tól 2 s alatt 30%-ra",
               fade_at_sec=8.0, fade_dur_sec=2.0, fade_to_percent=30)
    sync = cue("B", "b-av-sync", MEDIA_NAMES[2], "video", "Videó · villanás és hang szinkron")
    black = cue("B", "b-black-opening", MEDIA_NAMES[3], "video", "Videó · 1 s fekete kezdés, majd kép")
    trimmed = cue("B", "b-av-trim", MEDIA_NAMES[2], "video", "IN/OUT · szinkronvideó 3–11 s",
                  start_sec=3.0, stop_at_sec=11.0)
    audio_only = cue("A", "a-video-audio-only", MEDIA_NAMES[2], "video", "Videó az A oldalon · Audio only",
                     video_mode="audio_only", open_on_second_screen=False)
    end = cue("B", "b-end", MEDIA_NAMES[7], "video", "Videó · 6 s vég / EOF / Still")
    ppt2 = cue("B", "b-ppt-2s", PPT_NAME, "ppt", "PPT · 2 s álló diák + 6 s videó", ppt_interval_sec=2.0)
    ppt5 = cue("B", "b-ppt-5s", PPT_NAME, "ppt", "PPT · 5 s álló diák + 6 s videó", ppt_interval_sec=5.0)
    scene(1, "Projektor és képarány", [], [scope_image, scope_video, still],
          "Kijelző, teljes kép, képarány, sarokjelölések. Semmi nem indul magától.")
    scene(2, "Sztereó hang és hullámforma", [stereo, waveform, fade], [still, scope_image],
          "Bal/jobb csatorna, VU, dinamikus hullámforma, pause/resume; fade 8–10 s között 30%-ra.")
    scene(3, "Videóhang, Mix és keresés", [waveform, audio_only], [sync, black, trimmed, end, still],
          "Sound on/off, kizárólagos átadás, Mix, seek és Audio only; külön 3–11 s IN/OUT cue.")
    scene(4, "PPT időköz: 2 s / 5 s", [waveform], [ppt2, ppt5, still],
          "Azonos prezentáció két külön cue-időközzel. Repeat OFF kézi; ON diakör. A videó a saját végéig tart.")
    scene(5, "Still és média vége", [waveform], [end, still, ppt2],
          "Show újra és Stop: Still. A 6 s videó vége, Repeat OFF/ON. Betöltéskor a logó a Still.")
    long_a = [cue("A", f"a-long-{i:02d}", MEDIA_NAMES[1], "audio", f"Audio {i:02d} / 60 · hullámforma")
              for i in range(1, 61)]
    long_b = [cue("B", f"b-long-{i:02d}", MEDIA_NAMES[2], "video", f"Projection {i:02d} / 60 · szinkronvideó")
              for i in range(1, 61)]
    scene(6, "Hosszú lista · 60 + 60 elem", long_a, long_b,
          "Lejátszás közben görgess, jelöld ki a 40. sort; a futó elem és a következő kijelölés külön marad.")
    save3 = cue("A", "a-save-in3", MEDIA_NAMES[1], "audio", "Mentés · ugyanaz a fájl, IN = 3 s", start_sec=3)
    save6 = cue("A", "a-save-in6", MEDIA_NAMES[1], "audio", "Mentés · ugyanaz a fájl, IN = 6 s", start_sec=6)
    scene(7, "Mentés és visszatöltés", [save3, save6], [still, ppt2, ppt5],
          "Save As új néven; ellenőrizd az eltérő IN pontokat, Stillt és a két PPT-időközt.")
    return dict(version=3, media_paths="show-folder", active_scene=scenes[0].id,
                settings=dict(mpv_output_mode="ipc", mpv_persistent_output=True, output_aspect="Source",
                              video_fullscreen=False, startup_volume=100, normalize_enabled=False),
                playback=dict(repeat_audio=False, repeat_projection=False, slide_audio=False,
                              slide_seconds="5", standby_cue_id=still),
                cues_a=[value.to_dict() for value in a], cues_b=[value.to_dict() for value in b],
                scenes=[value.to_dict() for value in scenes])


class Variable:
    def __init__(self, value=None): self.value = value
    def set(self, value): self.value = value
    def get(self): return self.value


def validate_show(folder: Path) -> dict:
    """Use the real document loader with UI/media side effects stubbed out."""
    path = folder / "Test Show.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    ids = [cue["id"] for key in ("cues_a", "cues_b") for cue in data[key]]
    assert len(ids) == len(set(ids))
    assert all(not cue["auto_play"] for key in ("cues_a", "cues_b") for cue in data[key])
    assert all(not scene["auto_advance"] for scene in data["scenes"])
    for key in ("cues_a", "cues_b"):
        for cue in data[key]:
            assert cue["path"].startswith("./") and "\\" not in cue["path"]
            media = (folder / cue["path"]).resolve(strict=True)
            assert media.is_relative_to(folder.resolve()) and media.stat().st_size > 0

    app = Mock(spec=App)
    app.audio_runner, app.video_runner = Mock(), Mock()
    app.tree_a, app.tree_b, app.scene_listbox = Mock(), Mock(), Mock()
    app._checked_cues_a, app._checked_cues_b = set(), set()
    for name in ("var_output_aspect", "var_loop_a", "var_loop_b", "var_ppt_sound",
                 "var_ppt_interval", "var_ppt_interval_target"):
        setattr(app, name, Variable())
    for name in ("_reset_audio_routing", "_prune_orphan_cues", "_activate_scene",
                 "_ppt_interval_for_cue", "_sync_ppt_interval_control", "_standby_cue"):
        setattr(app, name, MethodType(getattr(App, name), app))
    with patch("player.sys.platform", "win32"):
        App._load_show_from_path(app, path, set_show_path=True)
    assert app._selected_scene_idx == 0 and len(app._scenes) == 7
    assert not app.audio_mix and not app.var_ppt_sound.get()
    assert not app._loop_a_enabled and not app._loop_b_enabled
    assert app._standby_cue().id == "b-still-logo"
    assert all(Path(cue.path).is_file() for cue in app._all_cues_a + app._all_cues_b)
    for number, scene in enumerate(app._scenes):
        app._selected_scene_idx = number
        app._activate_scene()
        assert [cue.id for cue in app._cues_a] == scene.cue_ids_a
        assert [cue.id for cue in app._cues_b] == scene.cue_ids_b
    assert len(app._scenes[5].cue_ids_a) == len(app._scenes[5].cue_ids_b) == 60
    assert [cue.ppt_interval_sec for cue in app._all_cues_b if cue.kind == "ppt"] == [2, 5]
    fade = next(cue for cue in app._all_cues_a if cue.id == "a-fade")
    trim = next(cue for cue in app._all_cues_b if cue.id == "b-av-trim")
    audio_only = next(cue for cue in app._all_cues_a if cue.id == "a-video-audio-only")
    assert (fade.fade_at_sec, fade.fade_dur_sec, fade.fade_to_percent) == (8, 2, 30)
    assert (trim.start_sec, trim.stop_at_sec) == (3, 11)
    assert audio_only.video_mode == "audio_only"
    app.audio_runner.play.assert_not_called()
    app.video_runner.play_for_deck.assert_not_called()
    app._play_deck_a.assert_not_called()
    app._play_deck_b.assert_not_called()

    def no_office(*_args):
        raise AssertionError("The supplied PPT cache must be sufficient without Office")
    ppt = folder / PPT_NAME
    files = prepare(ppt, folder / ".unused-render-cache", lambda _message: None, render=no_office)
    assert files == [ppt.with_name(ppt.name + ".mpv") / name for name in ("0001.png", "0002.mp4", "0003.png")]
    assert read_timing(ppt, files) == [None, None, None]
    assert digest(files[1]) == digest(folder / "Media/07_END_OF_CUE.mp4")
    return dict(loader="actual App._load_show_from_path; UI/player callbacks stubbed; no live output",
                scenes=7, audio_cues=len(app._all_cues_a), projection_cues=len(app._all_cues_b),
                unique_cue_ids=len(ids), relative_media_paths=True, safe_open_verified=True,
                prepared_ppt_without_office=True, all_scenes_resolve=True)


def build(folder: Path) -> dict:
    folder = folder.resolve()
    missing = [name for name in MEDIA_NAMES if not (folder / "Media" / name).is_file()]
    if missing:
        raise FileNotFoundError("Media generator must finish first: " + ", ".join(missing))
    ppt = make_presentation(folder)
    show = show_document()
    (folder / "Test Show.json").write_text(json.dumps(show, ensure_ascii=False, indent=2), encoding="utf-8")
    report = validate_show(folder)
    manifest = dict(version=1, show="Test Show.json", validation=report, presentation=ppt,
                    safe_open=dict(sound=False, repeat_audio=False, repeat_projection=False, mix=False,
                                   still="Media/06_STILL_LOGO.png", auto_start=False),
                    cue_checks=dict(fade=dict(cue_id="a-fade", start_seconds=8, duration_seconds=2, target_percent=30),
                                    trim=dict(cue_id="b-av-trim", in_seconds=3, out_seconds=11),
                                    audio_only=dict(cue_id="a-video-audio-only", projected_picture_unchanged=True)),
                    scenes=[dict(id=scene["id"], name=scene["name"], audio_rows=len(scene["cue_ids_a"]),
                                 projection_rows=len(scene["cue_ids_b"]), purpose=scene["notes"]) for scene in show["scenes"]])
    (folder / "TEST_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for name in ("KEZDD ITT.txt", "ELLENORZO LAP.html"):
        shutil.copyfile(ROOT / "packaging/operator-test-pack" / name, folder / name)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/operator-test-pack/Show Control Test Pack")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    print(json.dumps(validate_show(args.output.resolve()) if args.validate_only else build(args.output),
                     ensure_ascii=False, indent=2))

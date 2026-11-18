"""Add the operator's offline rehearsal to an already verified Windows build."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import zipfile


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def package(app_zip, tests, inventory_path, output, commit, run):
    if output.exists() or output.with_suffix(".part").exists():
        raise FileExistsError(f"Delivery already exists: {output}")
    show = json.loads((tests / "Test Show.json").read_text(encoding="utf-8-sig"))
    for cue in show["cues_a"] + show["cues_b"]:
        relative = PurePosixPath(cue["path"])
        if relative.is_absolute() or ".." in relative.parts or ":" in cue["path"]:
            raise ValueError(f"Non-portable test cue: {cue['path']}")
        if not (tests / relative).is_file():
            raise FileNotFoundError(tests / relative)
    for name in ("KEZDD ITT.txt", "ELLENORZO LAP.html",
                 "Media/05_PROJECTOR_MONOSCOPE_1080P.png",
                 "Media/03_AV_SYNC_TIMECODE.mp4", "08_PPT_LOOP_TEST.pptx"):
        if not (tests / name).is_file():
            raise FileNotFoundError(tests / name)
    files = sorted(path for path in tests.rglob("*") if path.is_file())
    if any(path.is_symlink() for path in files):
        raise ValueError("Test pack must contain ordinary files, not links")
    inventory = json.loads(inventory_path.read_text(encoding="utf-8-sig"))
    manifest = {
        "application_commit": commit,
        "verified_windows_run": f"https://github.com/SandorPeteer/SP_SHOW_CTRL/actions/runs/{run}",
        "packaged_utc": datetime.now(timezone.utc).isoformat(),
        "test_show": "Show Control Test Pack/Test Show.json",
        "files": [{"path": path.relative_to(tests).as_posix(), "bytes": path.stat().st_size,
                   "sha256": sha256(path)} for path in files],
        "checks": {"portable_cue_paths": True, "verified_application_binaries": len(inventory["signatures"])},
        "limits": ["Physical projector, sound device and presenter are tested by the operator.",
                   "Prepared PPT media do not prove Microsoft Office export on the target PC.",
                   "The compressed projector video is a setup aid, not a calibrated color reference.",
                   "Smart App Control compatibility remains unresolved; security settings were not changed."],
    }
    instructions = f"""SP SHOW CONTROL — TELJES ÉLŐ PRÓBACSOMAG — {commit}

1. Bontsd ki a TELJES ZIP-et egy új mappába.
2. Indítsd el a mappa SP Show Control.exe fájlját. Az _internal maradjon mellette.
3. File → Open: Show Control Test Pack / Test Show.json.
4. A Show Control Test Pack / KEZDD ITT.txt vezet végig az induláson.
5. Nyisd meg és szükség esetén nyomtasd ki az ELLENORZO LAP.html fájlt.

A mintaműsor megnyitása nem indít lejátszást. Az anyagban szereplő hangok
szintetikus tesztjelek; indulj alacsony kimeneti hangerővel.

Timecode és kép-hang szinkron, projektorábra, bal/jobb és jelszintteszt,
valódi hullámforma, feketével induló videó, PPT-időzítés, Still, Repeat,
Mix, tekerés, hosszú lista és mentéspróba is szerepel a csomagban.
Az Audio only gomb megmaradt az Audio / Video fejlécében: videó kijelölésekor látszik.

Az alkalmazás Windows-kapui: 173 célzott teszt és 36 natív felületellenőrzés,
valamint külön valódi mpv- és Qt-kezelési próbák. A géped hangkártyáját,
projektorát, Office-telepítését és prezentálóját az élő tesztlap ellenőrzi.
Build: https://github.com/SandorPeteer/SP_SHOW_CTRL/actions/runs/{run}

A csomag nem teljesen kiadói aláírású; a Smart App Control megfelelősége
továbbra sem megoldott. Biztonsági beállítást nem változtattunk.
A binary-signatures.json és TEST-PACK-MANIFEST.json tartalmazza az ellenőrzőadatokat.
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".part")
    shutil.copyfile(app_zip, temporary)
    with zipfile.ZipFile(temporary, "a", zipfile.ZIP_DEFLATED) as archive:
        existing = set(archive.namelist())
        if "SP Show Control.exe" not in existing:
            raise ValueError("The verified application executable is missing")
        additions = {"OLVASS EL.txt", "binary-signatures.json", "TEST-PACK-MANIFEST.json"}
        additions.update("Show Control Test Pack/" + item["path"] for item in manifest["files"])
        if existing & additions:
            raise ValueError("The application ZIP already contains delivery extras")
        archive.writestr("OLVASS EL.txt", instructions.encode("utf-8-sig"))
        archive.writestr("binary-signatures.json", inventory_path.read_bytes())
        archive.writestr("TEST-PACK-MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for path in files:
            archive.write(path, "Show Control Test Pack/" + path.relative_to(tests).as_posix())
    with zipfile.ZipFile(temporary) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP integrity check failed")
        for item in inventory["signatures"]:
            with archive.open(item["path"].replace("\\", "/")) as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != item["sha256"].lower():
                    raise ValueError(f"Application binary changed: {item['path']}")
        for item in manifest["files"]:
            with archive.open("Show Control Test Pack/" + item["path"]) as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != item["sha256"]:
                    raise ValueError(f"Test media changed during packaging: {item['path']}")
    temporary.replace(output)
    digest = sha256(output)
    output.with_suffix(".sha256").write_text(f"{digest}  {output.name}\n")
    return {"path": str(output), "bytes": output.stat().st_size, "sha256": digest,
            "verified_binaries": len(inventory["signatures"]), "test_files": len(files)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-zip", type=Path, required=True)
    parser.add_argument("--tests", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run", required=True)
    args = parser.parse_args()
    print(json.dumps(package(args.app_zip, args.tests, args.inventory, args.output,
                             args.commit, args.run), ensure_ascii=False))

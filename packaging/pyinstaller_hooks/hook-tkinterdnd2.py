"""Only the x64 Windows runtime belongs in the portable Windows package."""
from pathlib import Path
from PyInstaller.utils.hooks import get_package_paths

runtime = Path(get_package_paths("tkinterdnd2")[1]) / "tkdnd" / "win-x64"
if not (runtime / "pkgIndex.tcl").is_file():
    raise RuntimeError(f"Missing tkinterdnd2 Windows x64 runtime: {runtime}")
libraries = list(runtime.glob("*.dll"))
if not libraries:
    raise RuntimeError(f"Missing tkinterdnd2 Windows x64 DLL: {runtime}")
destination = "tkinterdnd2/tkdnd/win-x64"
datas = [(str(path), destination) for path in runtime.glob("*.tcl")]
binaries = [(str(path), destination) for path in libraries]

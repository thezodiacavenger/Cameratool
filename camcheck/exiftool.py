"""Locate and run ExifTool (https://exiftool.org) - the metadata engine.

ExifTool by Phil Harvey decodes the proprietary (and in Nikon/Sony's case
encrypted) MakerNote blocks where camera makers hide shutter counters and
internal serial numbers. We drive it as a subprocess and parse its JSON.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

_cached = None


def _candidates():
    env = os.environ.get("CAMCHECK_EXIFTOOL")
    if env:
        yield env
    roots = [ROOT]
    if getattr(sys, "frozen", False):  # PyInstaller build: look beside the .exe
        roots.insert(0, os.path.dirname(sys.executable))
    for tools in [os.path.join(r, "tools", "exiftool") for r in roots]:
        yield from _tool_dir(tools)
    for name in ("exiftool", "exiftool.exe"):
        p = shutil.which(name)
        if p:
            yield p
    # common macOS install locations (app launched from Finder has a thin PATH)
    for p in ("/usr/local/bin/exiftool", "/opt/homebrew/bin/exiftool"):
        yield p


def _tool_dir(tools):
    if os.name == "nt":
        yield os.path.join(tools, "exiftool.exe")
    yield os.path.join(tools, "exiftool")          # Perl distribution (macOS/Linux)


def _cmd_for(path):
    """Perl script distributions need to be launched through perl on some systems."""
    if path.endswith(".exe") or os.name == "nt":
        return [path]
    try:
        with open(path, "rb") as fh:
            head = fh.read(64)
        if head.startswith(b"#!") and b"perl" in head:
            perl = shutil.which("perl") or "/usr/bin/perl"
            return [perl, path]
    except OSError:
        pass
    return [path]


def find_exiftool():
    """Return (command list, version) or (None, None)."""
    global _cached
    if _cached:
        return _cached
    for p in _candidates():
        if p and os.path.isfile(p):
            cmd = _cmd_for(p)
            try:
                out = subprocess.run(cmd + ["-ver"], capture_output=True, text=True,
                                     timeout=20, **_no_window())
                ver = out.stdout.strip()
                if out.returncode == 0 and ver:
                    _cached = (cmd, ver)
                    return _cached
            except (OSError, subprocess.SubprocessError):
                continue
    return None, None


def reset_cache():
    global _cached
    _cached = None


def _no_window():
    if os.name == "nt":
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {}


def _pairs_hook(pairs):
    """Keep duplicate keys (ExifTool may emit the same tag from several sub-tables)."""
    d = {}
    for k, v in pairs:
        if k in d:
            i = 1
            while f"{k} #{i}" in d:
                i += 1
            d[f"{k} #{i}"] = v
        else:
            d[k] = v
    return d


def read_metadata(paths):
    """Run ExifTool on one or more files. Returns list of dicts keyed 'Group:Tag'."""
    cmd, _ = find_exiftool()
    if not cmd:
        raise RuntimeError("ExifTool not found. Run the installer in the tools folder "
                           "(see README) or install it from exiftool.org.")
    # Use an argfile so non-ASCII file names survive on Windows.
    args = ["-j", "-a", "-u", "-G1", "-n", "-api", "LargeFileSupport=1",
            "-charset", "filename=utf8"] + list(paths)
    fd, argfile = tempfile.mkstemp(suffix=".args")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(args))
        out = subprocess.run(cmd + ["-@", argfile], capture_output=True, timeout=180,
                             **_no_window())
    finally:
        try:
            os.remove(argfile)
        except OSError:
            pass
    text = out.stdout.decode("utf-8", "replace").strip()
    if not text:
        err = out.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(err or "ExifTool returned no data")
    return json.loads(text, object_pairs_hook=_pairs_hook)


if __name__ == "__main__":
    print(find_exiftool())
    if len(sys.argv) > 1:
        print(json.dumps(read_metadata(sys.argv[1:]), indent=1))

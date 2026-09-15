from pathlib import Path
import bz2
import gzip
import lzma
import os
import shutil
import subprocess
import tarfile

HANDLER = {
    "name": "formats",
    "extensions": [".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz2", ".txz", ".tar", ".rar", ".7z", ".z", ".gz", ".bz2", ".xz"],
    "priority": 90,
}
HERE = Path(__file__).resolve().parent


def find_tool(names):
    for name in names:
        local = HERE / name
        if local.exists():
            return local
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def run(command):
    options = {"text": True, "capture_output": True, "check": False}
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(command, **options)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "Extraction failed").strip())


def extract_tar(source: Path, destination: Path, progress) -> None:
    root = destination.resolve()
    with tarfile.open(source, "r:*") as archive:
        members = archive.getmembers()
        for item in members:
            target = (destination / item.name).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"Unsafe path: {item.name}")
            if item.issym() or item.islnk() or item.isdev() or item.isfifo():
                raise ValueError(f"Special entry blocked: {item.name}")
        total = sum(item.size for item in members if item.isfile())
        done = 0
        if progress:
            progress(0, total)
        for item in members:
            target = destination / item.name
            if item.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not item.isfile():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            src = archive.extractfile(item)
            if src is None:
                continue
            with src, target.open("wb") as dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
        if progress:
            progress(total, total)


def extract_stream(source: Path, destination: Path, suffix: str, progress) -> None:
    output = destination / source.with_suffix("").name
    total = source.stat().st_size
    raw = source.open("rb")
    try:
        if suffix == ".gz":
            stream = gzip.GzipFile(fileobj=raw, mode="rb")
        elif suffix == ".bz2":
            stream = bz2.BZ2File(raw, "rb")
        else:
            stream = lzma.LZMAFile(raw, "rb")
        with stream, output.open("wb") as dst:
            if progress:
                progress(0, total)
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
                if progress:
                    progress(min(raw.tell(), total), total)
            if progress:
                progress(total, total)
    finally:
        if not raw.closed:
            raw.close()


def extract(source: Path, destination: Path, progress=None) -> None:
    lower = source.name.lower()
    if lower.endswith((".tar", ".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz2", ".txz")):
        extract_tar(source, destination, progress)
        return
    suffix = source.suffix.lower()
    if suffix in {".gz", ".bz2", ".xz"}:
        extract_stream(source, destination, suffix, progress)
        return
    total = source.stat().st_size
    if progress:
        progress(0, total)
    if suffix == ".rar":
        unrar = find_tool(("UnRAR.exe", "unrar.exe", "unrar"))
        if unrar:
            run([str(unrar), "x", "-o+", str(source), str(destination) + os.sep])
            if progress:
                progress(total, total)
            return
    seven = find_tool(("7z.exe", "7za.exe", "7zr.exe", "7z", "7za", "7zr"))
    if not seven:
        raise RuntimeError("RAR/7Z/.Z needs 7-Zip or UnRAR")
    run([str(seven), "x", "-y", f"-o{destination}", str(source)])
    if progress:
        progress(total, total)

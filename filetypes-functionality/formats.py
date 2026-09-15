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


def find_tool(names: tuple[str, ...]) -> Path | None:
    for name in names:
        local = HERE / name
        if local.exists():
            return local
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def run(command: list[str]) -> None:
    options = {"text": True, "capture_output": True, "check": False}
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(command, **options)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "Extraction failed").strip())


def extract_tar(source: Path, destination: Path) -> None:
    root = destination.resolve()
    with tarfile.open(source, "r:*") as archive:
        members = archive.getmembers()
        for item in members:
            target = (destination / item.name).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"Unsafe path: {item.name}")
            if item.issym() or item.islnk() or item.isdev() or item.isfifo():
                raise ValueError(f"Special entry blocked: {item.name}")
        try:
            archive.extractall(destination, members=members, filter="data")
        except TypeError:
            archive.extractall(destination, members=members)


def extract(source: Path, destination: Path) -> None:
    lower = source.name.lower()
    if lower.endswith((".tar", ".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz2", ".txz")):
        extract_tar(source, destination)
        return

    suffix = source.suffix.lower()
    if suffix in {".gz", ".bz2", ".xz"}:
        opener = {".gz": gzip.open, ".bz2": bz2.open, ".xz": lzma.open}[suffix]
        output = destination / source.with_suffix("").name
        with opener(source, "rb") as src, output.open("wb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        return

    if suffix == ".rar":
        unrar = find_tool(("UnRAR.exe", "unrar.exe", "unrar"))
        if unrar:
            run([str(unrar), "x", "-o+", str(source), str(destination) + os.sep])
            return

    seven = find_tool(("7z.exe", "7za.exe", "7zr.exe", "7z", "7za", "7zr"))
    if not seven:
        raise RuntimeError("RAR/7Z/.Z needs 7-Zip or UnRAR")
    run([str(seven), "x", "-y", f"-o{destination}", str(source)])

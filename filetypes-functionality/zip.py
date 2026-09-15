from pathlib import Path
import stat
import zipfile

HANDLER = {"name": "zip", "extensions": [".zip"], "priority": 100}


def extract(source: Path, destination: Path, progress=None) -> None:
    root = destination.resolve()
    with zipfile.ZipFile(source) as archive:
        items = archive.infolist()
        for item in items:
            target = (destination / item.filename).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"Unsafe path: {item.filename}")
            if stat.S_ISLNK((item.external_attr >> 16) & 0xFFFF):
                raise ValueError(f"Link blocked: {item.filename}")
        total = sum(item.file_size for item in items if not item.is_dir())
        done = 0
        if progress:
            progress(0, total)
        for item in items:
            target = destination / item.filename
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item, "r") as src, target.open("wb") as dst:
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

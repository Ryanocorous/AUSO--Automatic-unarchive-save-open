from pathlib import Path
import stat
import zipfile

HANDLER = {
    "name": "zip",
    "extensions": [".zip"],
    "priority": 100,
}


def extract(source: Path, destination: Path) -> None:
    root = destination.resolve()
    with zipfile.ZipFile(source) as archive:
        for item in archive.infolist():
            target = (destination / item.filename).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"Unsafe path: {item.filename}")
            if stat.S_ISLNK((item.external_attr >> 16) & 0xFFFF):
                raise ValueError(f"Link blocked: {item.filename}")
        archive.extractall(destination)

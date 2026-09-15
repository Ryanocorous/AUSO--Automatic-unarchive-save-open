from pathlib import Path

HANDLER = {"name": "myformat", "extensions": [".myformat"], "priority": 50}


def extract(source: Path, destination: Path, progress=None) -> None:
    if progress:
        progress(0, source.stat().st_size)
    raise NotImplementedError

from pathlib import Path

# Rename this file and list the extensions it handles.
HANDLER = {"name": "myformat", "extensions": [".myformat"], "priority": 50}


def extract(source: Path, destination: Path) -> None:
    # Extract source into destination.
    raise NotImplementedError

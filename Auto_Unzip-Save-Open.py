from __future__ import annotations

import argparse
import bz2
import configparser
import ctypes
from ctypes import wintypes
import gzip
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import zipfile

APP_NAME = "Auto_Unzip-Save-Open-Lightweight"
TEMP_SUFFIXES = (".crdownload", ".part", ".partial", ".download", ".tmp")
ARCHIVE_SUFFIXES = (
    ".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz2", ".txz",
    ".zip", ".tar", ".gz", ".bz2", ".xz",
)


def base_dir() -> Path:
    return Path(__file__).resolve().parent


def load_config() -> configparser.ConfigParser:
    config = configparser.ConfigParser(interpolation=None)
    config.read(base_dir() / "config.ini", encoding="utf-8")
    if "settings" not in config:
        config["settings"] = {}
    return config


def expand_path(text: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(text.strip().strip('"')))).resolve()


def paths(config: configparser.ConfigParser) -> tuple[Path, Path]:
    settings = config["settings"]
    downloads_text = settings.get("downloads_folder", "").strip()
    downloads = expand_path(downloads_text) if downloads_text else (Path.home() / "Downloads").resolve()
    dump_text = settings.get("dump_folder", "").strip()
    dump = expand_path(dump_text) if dump_text else (downloads / "Dump").resolve()
    return downloads, dump


def excluded(path: Path, text: str) -> bool:
    name = path.name.lower()
    for item in text.split(","):
        value = item.strip().lower()
        if not value:
            continue
        if not value.startswith("."):
            value = "." + value
        if name.endswith(value):
            return True
    return False


def archive_suffix(path: Path) -> str | None:
    name = path.name.lower()
    for suffix in ARCHIVE_SUFFIXES:
        if name.endswith(suffix):
            return suffix
    return None


def safe_name(name: str) -> bool:
    name = name.replace("\\", "/")
    p = PurePosixPath(name)
    return not p.is_absolute() and ".." not in p.parts and not (p.parts and ":" in p.parts[0])


def extract_zip(source: Path, destination: Path) -> None:
    with zipfile.ZipFile(source) as archive:
        for info in archive.infolist():
            if not safe_name(info.filename):
                raise ValueError("Unsafe ZIP path")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError("ZIP links are not supported")
        archive.extractall(destination)


def extract_tar(source: Path, destination: Path) -> None:
    with tarfile.open(source, "r:*") as archive:
        members = archive.getmembers()
        for member in members:
            if not safe_name(member.name):
                raise ValueError("Unsafe TAR path")
            if member.issym() or member.islnk() or member.isdev():
                raise ValueError("TAR links and devices are not supported")
        try:
            archive.extractall(destination, members=members, filter="data")
        except TypeError:
            archive.extractall(destination, members=members)


def extract_stream(source: Path, destination: Path, suffix: str) -> None:
    name = source.name[:-len(suffix)] or source.stem or "output"
    target = destination / name
    opener = {".gz": gzip.open, ".bz2": bz2.open, ".xz": __import__("lzma").open}[suffix]
    with opener(source, "rb") as src, target.open("wb") as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)


def output_folder(source: Path, suffix: str, dump: Path) -> Path:
    name = source.name[:-len(suffix)].rstrip(". ") or source.stem
    target = dump / name
    number = 2
    while target.exists():
        target = dump / f"{name} ({number})"
        number += 1
    return target


def signature(path: Path) -> tuple[int, int]:
    st = path.stat()
    return st.st_size, st.st_mtime_ns


def stable(path: Path, seconds: float) -> bool:
    try:
        previous = signature(path)
    except OSError:
        return False
    unchanged_since = time.monotonic()
    deadline = time.monotonic() + 3600
    while time.monotonic() < deadline:
        time.sleep(0.25)
        try:
            current = signature(path)
        except OSError:
            return False
        if current != previous:
            previous = current
            unchanged_since = time.monotonic()
            continue
        if time.monotonic() - unchanged_since >= seconds:
            try:
                with path.open("rb"):
                    return True
            except OSError:
                unchanged_since = time.monotonic()
    return False


def process_file(source: Path, seen: dict[str, tuple[int, int]] | None = None) -> Path | None:
    source = source.resolve()
    if not source.is_file() or source.name.lower().endswith(TEMP_SUFFIXES):
        return None
    config = load_config()
    settings = config["settings"]
    if excluded(source, settings.get("filetypes_to_exclude", "")):
        return None
    suffix = archive_suffix(source)
    if not suffix:
        return None
    current = signature(source)
    key = str(source).lower()
    if seen is not None and seen.get(key) == current:
        return None
    _, dump = paths(config)
    dump.mkdir(parents=True, exist_ok=True)
    destination = output_folder(source, suffix, dump)
    destination.mkdir(parents=True, exist_ok=False)
    try:
        if suffix == ".zip":
            extract_zip(source, destination)
        elif suffix in {".tar", ".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz2", ".txz"}:
            extract_tar(source, destination)
        else:
            extract_stream(source, destination, suffix)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    if seen is not None:
        seen[key] = current
    if settings.getboolean("open_folder", fallback=True) and os.name == "nt":
        os.startfile(destination)
    return destination


def mutex() -> wintypes.HANDLE | None:
    if os.name != "nt":
        return None
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateMutexW(None, False, f"Local\\{APP_NAME}")
    if handle and kernel32.GetLastError() == 183:
        kernel32.CloseHandle(handle)
        return None
    return handle


def watch() -> int:
    if os.name != "nt":
        return 1
    guard = mutex()
    if not guard:
        return 0
    config = load_config()
    downloads, _ = paths(config)
    downloads.mkdir(parents=True, exist_ok=True)
    stable_seconds = max(0.5, config.getfloat("settings", "stable_seconds", fallback=2.0))

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.ReadDirectoryChangesW.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, wintypes.BOOL,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, ctypes.c_void_p,
    ]
    kernel32.ReadDirectoryChangesW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateFileW(
        str(downloads),
        0x0001,
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,
        0x02000000,
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        kernel32.CloseHandle(guard)
        return 2

    buffer = ctypes.create_string_buffer(65536)
    returned = wintypes.DWORD()
    seen: dict[str, tuple[int, int]] = {}

    try:
        while True:
            ok = kernel32.ReadDirectoryChangesW(
                handle,
                ctypes.byref(buffer),
                len(buffer),
                False,
                0x00000001 | 0x00000008 | 0x00000010,
                ctypes.byref(returned),
                None,
                None,
            )
            if not ok:
                return 3
            names: set[str] = set()
            offset = 0
            data = buffer.raw[:returned.value]
            while offset + 12 <= len(data):
                next_offset = int.from_bytes(data[offset:offset + 4], "little")
                name_len = int.from_bytes(data[offset + 8:offset + 12], "little")
                name = data[offset + 12:offset + 12 + name_len].decode("utf-16-le", errors="ignore")
                if name:
                    names.add(name)
                if next_offset == 0:
                    break
                offset += next_offset
            for name in names:
                path = downloads / name
                try:
                    if not path.is_file() or path.name.lower().endswith(TEMP_SUFFIXES):
                        continue
                    if not archive_suffix(path):
                        continue
                    if stable(path, stable_seconds):
                        process_file(path, seen)
                except Exception:
                    pass
    finally:
        kernel32.CloseHandle(handle)
        kernel32.CloseHandle(guard)


def pythonw() -> Path:
    exe = Path(sys.executable).resolve()
    candidate = exe.with_name("pythonw.exe")
    return candidate if candidate.exists() else exe


def set_startup(enabled: bool, script: Path) -> None:
    import winreg
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            command = subprocess.list2cmdline([str(pythonw()), str(script), "watch"])
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


def launch_watcher(script: Path) -> None:
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200 | 0x08000000
    subprocess.Popen(
        [str(pythonw()), str(script), "watch"],
        cwd=str(script.parent),
        creationflags=flags,
        close_fds=True,
    )


def install() -> int:
    if os.name != "nt":
        print("Windows only.")
        return 1

    add_startup = input("Add to startup? (Y / N) ").strip().lower() in {"y", "yes"}
    downloads_text = input("Downloads folder? Leave blank = default ").strip()
    downloads = expand_path(downloads_text) if downloads_text else (Path.home() / "Downloads").resolve()
    dump_text = input("Dump folder? Leave blank = default ").strip()
    dump = expand_path(dump_text) if dump_text else (downloads / "Dump").resolve()
    install_text = input("Install folder? Leave blank = default ").strip()
    default_install = Path(os.environ["LOCALAPPDATA"]) / APP_NAME
    folder = expand_path(install_text) if install_text else default_install.resolve()
    excludes = input("Filetypes to exclude? Put a comma between ").strip()

    folder.mkdir(parents=True, exist_ok=True)
    downloads.mkdir(parents=True, exist_ok=True)
    dump.mkdir(parents=True, exist_ok=True)
    script = folder / "Auto_Unzip-Save-Open.py"
    shutil.copy2(Path(__file__).resolve(), script)

    config = configparser.ConfigParser(interpolation=None)
    config["settings"] = {
        "downloads_folder": str(downloads),
        "dump_folder": str(dump),
        "install_folder": str(folder),
        "filetypes_to_exclude": excludes,
        "open_folder": "yes",
        "stable_seconds": "2.0",
        "startup": "yes" if add_startup else "no",
    }
    with (folder / "config.ini").open("w", encoding="utf-8") as handle:
        config.write(handle)

    set_startup(add_startup, script)
    launch_watcher(script)
    print(f"Installed: {folder}")
    return 0


def uninstall() -> int:
    if os.name != "nt":
        return 1
    set_startup(False, base_dir() / "Auto_Unzip-Save-Open.py")
    print(f"Startup entry removed. Delete this folder when the watcher is stopped: {base_dir()}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="Auto_Unzip-Save-Open")
    parser.add_argument("command", nargs="?", choices=("install", "watch", "process", "uninstall"), default="install")
    parser.add_argument("path", nargs="?")
    args = parser.parse_args()
    if args.command == "install":
        return install()
    if args.command == "watch":
        return watch()
    if args.command == "uninstall":
        return uninstall()
    if not args.path:
        return 2
    result = process_file(Path(args.path))
    if result:
        print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import configparser
import ctypes
from ctypes import wintypes
from contextlib import contextmanager
from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Callable

APP_NAME = "Auto_Unzip-Save-Open"
SERVICE_NAME = APP_NAME
TEMP_SUFFIXES = (".crdownload", ".part", ".partial", ".download", ".tmp")


@dataclass
class Handler:
    name: str
    extensions: tuple[str, ...]
    priority: int
    extract: Callable[[Path, Path], None]


def base_dir() -> Path:
    return Path(__file__).resolve().parent


def load_config() -> configparser.ConfigParser:
    config = configparser.ConfigParser(interpolation=None)
    config.read(base_dir() / "config.ini", encoding="utf-8")
    if "settings" not in config:
        config["settings"] = {}
    return config


def downloads_default() -> Path:
    return Path.home() / "Downloads"


def configured_paths(config: configparser.ConfigParser) -> tuple[Path, Path]:
    settings = config["settings"]
    text = settings.get("downloads_folder", "").strip()
    downloads = Path(os.path.expandvars(os.path.expanduser(text))).resolve() if text else downloads_default().resolve()
    text = settings.get("dump_folder", "").strip()
    dump = Path(os.path.expandvars(os.path.expanduser(text))).resolve() if text else (downloads / "Dump").resolve()
    return downloads, dump


def run_hidden(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    options = {
        "cwd": str(cwd) if cwd else None,
        "text": True,
        "capture_output": True,
        "check": False,
    }
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(command, **options)


def run_exe_extract(path: Path, source: Path, destination: Path) -> None:
    result = run_hidden([str(path), "--autoextract-extract", str(source), str(destination)], path.parent)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "Handler failed").strip())


def exe_handler(path: Path) -> Handler | None:
    name = path.name.lower()
    if name in {"7z.exe", "7za.exe", "7zr.exe"}:
        def extract(source: Path, destination: Path) -> None:
            result = run_hidden([str(path), "x", "-y", f"-o{destination}", str(source)], path.parent)
            if result.returncode:
                raise RuntimeError((result.stderr or result.stdout or "7-Zip failed").strip())
        return Handler("sevenzip-cli", (".7z", ".rar", ".z"), 80, extract)

    if name in {"unrar.exe", "rar.exe"}:
        def extract(source: Path, destination: Path) -> None:
            result = run_hidden([str(path), "x", "-o+", str(source), str(destination) + os.sep], path.parent)
            if result.returncode:
                raise RuntimeError((result.stderr or result.stdout or "UnRAR failed").strip())
        return Handler("unrar-cli", (".rar",), 85, extract)

    result = run_hidden([str(path), "--autoextract-describe"], path.parent)
    if result.returncode:
        return None
    try:
        meta = json.loads(result.stdout)
        extensions = tuple(sorted({str(item).lower() for item in meta["extensions"]}, key=len, reverse=True))
        name = str(meta["name"]).strip().lower()
        priority = int(meta.get("priority", 50))
        return Handler(name, extensions, priority, lambda source, destination: run_exe_extract(path, source, destination))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def load_handlers(config: configparser.ConfigParser) -> list[Handler]:
    folder = base_dir() / "filetypes-functionality"
    folder.mkdir(parents=True, exist_ok=True)
    handlers: list[Handler] = []

    for path in sorted(folder.glob("*.py")):
        if path.stem.lower().startswith("example"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"auso_{path.stem}", path)
            if not spec or not spec.loader:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            meta = module.HANDLER
            name = str(meta["name"]).strip().lower()
            if not config.getboolean("handlers", name, fallback=True):
                continue
            extensions = tuple(sorted({str(item).lower() for item in meta["extensions"]}, key=len, reverse=True))
            handlers.append(Handler(name, extensions, int(meta.get("priority", 50)), module.extract))
        except Exception:
            log(f"Could not load {path.name}")

    for path in sorted(folder.glob("*.exe")):
        if path.stem.lower().startswith("example"):
            continue
        try:
            handler = exe_handler(path)
            if handler and config.getboolean("handlers", handler.name, fallback=True):
                handlers.append(handler)
        except Exception:
            log(f"Could not load {path.name}")

    handlers.sort(key=lambda item: item.priority, reverse=True)
    return handlers


def choose_handler(path: Path, handlers: list[Handler]) -> tuple[Handler, str] | None:
    name = path.name.lower()
    matches: list[tuple[int, int, Handler, str]] = []
    for handler in handlers:
        for extension in handler.extensions:
            if name.endswith(extension):
                matches.append((len(extension), handler.priority, handler, extension))
    if not matches:
        return None
    _, _, handler, extension = max(matches, key=lambda item: (item[0], item[1]))
    return handler, extension


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


def signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def output_folder(source: Path, extension: str, dump: Path) -> Path:
    name = source.name[:-len(extension)].rstrip(". ") or source.stem
    target = dump / name
    number = 2
    while target.exists():
        target = dump / f"{name} ({number})"
        number += 1
    return target


def log(message: str) -> None:
    try:
        with (base_dir() / f"{APP_NAME}.log").open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass


@contextmanager
def process_lock():
    path = base_dir() / ".extract.lock"
    handle = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            if handle.read(1) == b"":
                handle.seek(0)
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def process_file(source: Path) -> Path | None:
    try:
        source = source.resolve()
        if not source.is_file() or source.name.lower().endswith(TEMP_SUFFIXES):
            return None
        config = load_config()
        settings = config["settings"]
        if excluded(source, settings.get("filetypes_to_exclude", "")):
            return None
        picked = choose_handler(source, load_handlers(config))
        if not picked:
            return None

        current = signature(source)
        state_path = base_dir() / ".state.json"
        with process_lock():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                state = {}
            key = str(source)
            if state.get(key) == list(current):
                return None

            handler, extension = picked
            _, dump = configured_paths(config)
            dump.mkdir(parents=True, exist_ok=True)
            destination = output_folder(source, extension, dump)
            destination.mkdir(parents=True, exist_ok=False)
            try:
                handler.extract(source, destination)
            except Exception:
                shutil.rmtree(destination, ignore_errors=True)
                raise

            state[key] = list(current)
            state_path.write_text(json.dumps(state), encoding="utf-8")
            if settings.getboolean("delete_after_extract", fallback=False):
                source.unlink(missing_ok=True)
            return destination
    except Exception as exc:
        log(f"{source}: {exc}")
        return None


def is_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def service_exists() -> bool:
    if os.name != "nt":
        return False
    return run_hidden(["sc.exe", "query", SERVICE_NAME]).returncode == 0


def wait_for_service_gone(seconds: float = 10.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if run_hidden(["sc.exe", "query", SERVICE_NAME]).returncode != 0:
            return True
        time.sleep(0.2)
    return False


def service_admin(action: str, watcher: Path | None = None, app_folder: Path | None = None) -> int:
    if not is_admin():
        return 5

    secure_dir = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / APP_NAME
    secure_watcher = secure_dir / "watcher.exe"
    app_folder = app_folder or base_dir()

    if action == "remove":
        if service_exists():
            run_hidden(["sc.exe", "stop", SERVICE_NAME])
            result = run_hidden(["sc.exe", "delete", SERVICE_NAME])
            if result.returncode and result.returncode != 1060:
                return result.returncode
            if not wait_for_service_gone():
                return 1072
        try:
            secure_watcher.unlink(missing_ok=True)
            secure_dir.rmdir()
        except OSError:
            pass
        return 0

    if not watcher or not watcher.exists():
        return 2

    if service_exists():
        run_hidden(["sc.exe", "stop", SERVICE_NAME])
        result = run_hidden(["sc.exe", "delete", SERVICE_NAME])
        if result.returncode and result.returncode != 1060:
            return result.returncode
        if not wait_for_service_gone():
            return 1072

    secure_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(watcher, secure_watcher)
    binary = f'"{secure_watcher}" --service --app-dir "{app_folder}"'
    result = run_hidden([
        "sc.exe", "create", SERVICE_NAME,
        "binPath=", binary,
        "start=", "auto",
        "DisplayName=", APP_NAME,
    ])
    if result.returncode:
        return result.returncode
    run_hidden(["sc.exe", "description", SERVICE_NAME, "Watches Downloads and extracts supported archives."])
    run_hidden(["sc.exe", "failure", SERVICE_NAME, "reset=", "86400", "actions=", "restart/5000/restart/15000/none/0"])
    return run_hidden(["sc.exe", "start", SERVICE_NAME]).returncode


def run_elevated_service(action: str, folder: Path, watcher: Path | None = None) -> bool:
    if is_admin():
        return service_admin(action, watcher, folder) == 0

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HANDLE),
            ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    args = [str(folder / f"{APP_NAME}.py"), "service-admin", action]
    if watcher:
        args.append(str(watcher))

    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040
    info.lpVerb = "runas"
    info.lpFile = str(Path(sys.executable).resolve())
    info.lpParameters = subprocess.list2cmdline(args)
    info.lpDirectory = str(folder)
    info.nShow = 1

    shell32 = ctypes.windll.shell32
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        return False

    ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
    code = wintypes.DWORD(1)
    ctypes.windll.kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(info.hProcess)
    return code.value == 0


def compiler_candidates() -> list[Path]:
    names = (
        "x86_64-w64-mingw32-g++.exe",
        "x86_64-w64-mingw32-c++.exe",
        "g++.exe",
        "clang++.exe",
    )
    found: list[Path] = []
    for name in names:
        value = shutil.which(name)
        if value:
            found.append(Path(value))

    roots = (
        Path(r"C:\Mingw\Mingw64\bin"),
        Path(r"C:\Mingw\mingw64\bin"),
        Path(r"C:\MinGW\Mingw64\bin"),
        Path(r"C:\MinGW\mingw64\bin"),
        Path(r"C:\mingw64\bin"),
        Path(r"C:\msys64\mingw64\bin"),
        Path(r"C:\Mingw\64\bin"),
    )
    for root in roots:
        for name in names:
            path = root / name
            if path.exists():
                found.append(path)

    unique: list[Path] = []
    seen: set[str] = set()
    for path in found:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def build_watcher(folder: Path) -> Path | None:
    output = folder / "watcher.exe"
    source = folder / "watcher.cpp"

    for compiler in compiler_candidates():
        command = [
            str(compiler), "-std=c++17", "-O2", "-s", "-municode", "-mwindows", "-static",
            str(source), "-o", str(output),
            "-lshell32", "-lwtsapi32", "-luserenv", "-ladvapi32",
        ]
        if run_hidden(command, folder).returncode == 0 and output.exists():
            return output

    compiler = shutil.which("cl.exe")
    if compiler:
        command = [
            compiler, "/nologo", "/std:c++17", "/O2", "/EHsc", "/MT", str(source), f"/Fe{output}",
            "/link", "/SUBSYSTEM:WINDOWS", "Shell32.lib", "Wtsapi32.lib", "Userenv.lib", "Advapi32.lib",
        ]
        if run_hidden(command, folder).returncode == 0 and output.exists():
            return output
    return None


def install() -> int:
    if os.name != "nt":
        print("Windows only.")
        return 1

    startup_service = input("Add startup service? (Y / N) ").strip().lower() in {"y", "yes"}
    downloads_text = input("Downloads folder? Leave black = default ").strip()
    downloads = Path(os.path.expandvars(os.path.expanduser(downloads_text.strip('"')))).resolve() if downloads_text else downloads_default().resolve()
    dump_text = input("Dump folder? Leave blank = default ").strip()
    dump = Path(os.path.expandvars(os.path.expanduser(dump_text.strip('"')))).resolve() if dump_text else (downloads / "Dump").resolve()
    install_text = input("Install folder? Leave blank = default ").strip()
    default_install = Path(os.environ["LOCALAPPDATA"]) / APP_NAME
    folder = Path(os.path.expandvars(os.path.expanduser(install_text.strip('"')))).resolve() if install_text else default_install.resolve()
    excludes = input("Filetypes to exclude? Put a comma between ").strip()

    source = base_dir()
    if service_exists() and not run_elevated_service("remove", source):
        print("Could not replace the existing startup service.")
        return 1
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / f"{APP_NAME}.py", folder / f"{APP_NAME}.py")
    shutil.copy2(source / "watcher.cpp", folder / "watcher.cpp")
    shutil.copytree(source / "filetypes-functionality", folder / "filetypes-functionality", dirs_exist_ok=True)

    bundled = source / "watcher.exe"
    if bundled.exists():
        shutil.copy2(bundled, folder / "watcher.exe")
    watcher = folder / "watcher.exe"
    if not watcher.exists():
        watcher = build_watcher(folder)
    if not watcher:
        print("Could not build watcher.exe. Install MinGW-w64 or Visual Studio C++ tools and run install.bat again.")
        return 1

    downloads.mkdir(parents=True, exist_ok=True)
    dump.mkdir(parents=True, exist_ok=True)

    config = configparser.ConfigParser(interpolation=None)
    config["settings"] = {
        "downloads_folder": str(downloads),
        "dump_folder": str(dump),
        "install_folder": str(folder),
        "filetypes_to_exclude": excludes,
        "open_folder": "yes",
        "delete_after_extract": "no",
        "startup_service": "yes" if startup_service else "no",
        "stable_seconds": "2.0",
    }
    config["handlers"] = {"zip": "yes", "formats": "yes"}
    config["runtime"] = {"python": str(Path(sys.executable).resolve())}
    with (folder / "config.ini").open("w", encoding="utf-8") as handle:
        config.write(handle)

    if startup_service:
        if not run_elevated_service("install", folder, watcher):
            config["settings"]["startup_service"] = "no"
            with (folder / "config.ini").open("w", encoding="utf-8") as handle:
                config.write(handle)
            print("Could not install the startup service.")
            return 1

    print(f"Installed: {folder}")
    return 0


def uninstall() -> int:
    if os.name != "nt":
        return 1
    folder = base_dir()
    if service_exists():
        if not run_elevated_service("remove", folder):
            print("Could not remove the startup service.")
            return 1
    print(f"Remove this folder: {folder}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument("command", nargs="?", choices=("install", "process", "uninstall", "service-admin"), default="install")
    parser.add_argument("path", nargs="?")
    parser.add_argument("result", nargs="?")
    args = parser.parse_args()

    if args.command == "install":
        return install()
    if args.command == "uninstall":
        return uninstall()
    if args.command == "service-admin":
        watcher = Path(args.result) if args.result else None
        return service_admin(args.path or "", watcher)
    if not args.path:
        return 2

    destination = process_file(Path(args.path))
    if destination:
        if args.result:
            Path(args.result).write_text(str(destination), encoding="utf-8")
        else:
            print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

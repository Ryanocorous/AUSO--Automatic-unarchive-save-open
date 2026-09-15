from __future__ import annotations

import argparse
import configparser
import ctypes
from ctypes import wintypes
from contextlib import contextmanager
from dataclasses import dataclass
import importlib.util
import inspect
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
TRAY_CLASS = APP_NAME + "-Tray"
WATCHER_STOP_EVENT = "Local\\" + APP_NAME + "-Watcher-Stop"
TEMP_SUFFIXES = (".crdownload", ".part", ".partial", ".download", ".tmp")
TRAY_FRAMES = [f"loading{i}.ico" for i in range(1, 15)]


@dataclass
class Handler:
    name: str
    extensions: tuple[str, ...]
    priority: int
    extract: Callable


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


def configured_paths(config: configparser.ConfigParser) -> tuple[Path, Path]:
    settings = config["settings"]
    text = settings.get("downloads_folder", "").strip()
    downloads = expand_path(text) if text else (Path.home() / "Downloads").resolve()
    text = settings.get("dump_folder", "").strip()
    dump = expand_path(text) if text else (downloads / "Dump").resolve()
    return downloads, dump


def pythonw() -> Path:
    exe = Path(sys.executable).resolve()
    candidate = exe.with_name("pythonw.exe")
    return candidate if candidate.exists() else exe


def run_hidden(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    options = {"cwd": str(cwd) if cwd else None, "text": True, "capture_output": True, "check": False}
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(command, **options)




def copy_file(source: Path, destination: Path) -> None:
    if source.resolve() == destination.resolve():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def copy_tree(source: Path, destination: Path) -> None:
    if source.resolve() == destination.resolve():
        return
    shutil.copytree(source, destination, dirs_exist_ok=True)

def log(message: str) -> None:
    try:
        with (base_dir() / f"{APP_NAME}.log").open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass


def progress_result(value, source: Path) -> tuple[int, int] | None:
    if value is None:
        return None
    total = max(1, source.stat().st_size)
    if isinstance(value, (int, float)):
        percent = max(0.0, min(100.0, float(value)))
        return int(total * percent / 100), total
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return max(0, int(value[0])), max(0, int(value[1]))
    if isinstance(value, dict):
        if "done" in value and "total" in value:
            return max(0, int(value["done"])), max(0, int(value["total"]))
        if "percent" in value:
            percent = max(0.0, min(100.0, float(value["percent"])))
            return int(total * percent / 100), total
    return None


def load_exe_progress_hook(path: Path):
    hook_path = path.with_name(path.stem + ".progress.py")
    if not hook_path.exists():
        return None
    spec = importlib.util.spec_from_file_location(f"auso_progress_{path.stem}", hook_path)
    if not spec or not spec.loader:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, "progress", None)


def direct_exe_progress(line: str, source: Path) -> tuple[int, int] | None:
    if not line.startswith("AUSO_PROGRESS "):
        return None
    fields = line.split()[1:]
    try:
        if len(fields) == 1:
            return progress_result(float(fields[0]), source)
        if len(fields) >= 2:
            return max(0, int(fields[0])), max(0, int(fields[1]))
    except ValueError:
        pass
    return None


def run_exe_extract(path: Path, source: Path, destination: Path, progress=None) -> None:
    hook = load_exe_progress_hook(path)
    options = {"cwd": str(path.parent), "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "text": True, "bufsize": 1}
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen([str(path), "--autoextract-extract", str(source), str(destination)], **options)
    output: list[str] = []
    assert process.stdout is not None
    for raw in process.stdout:
        line = raw.rstrip("\r\n")
        output.append(line)
        update = direct_exe_progress(line, source)
        if update is None and hook:
            try:
                update = progress_result(hook(line, source, destination), source)
            except Exception as exc:
                log(f"Progress hook failed for {path.name}: {exc}")
                hook = None
        if update is not None and progress:
            progress(*update)
    code = process.wait()
    if code:
        raise RuntimeError("\n".join(output[-20:]).strip() or "Handler failed")


def exe_handler(path: Path) -> Handler | None:
    name = path.name.lower()
    if name in {"7z.exe", "7za.exe", "7zr.exe"}:
        def extract(source: Path, destination: Path, progress=None) -> None:
            total = source.stat().st_size
            if progress:
                progress(0, total)
            result = run_hidden([str(path), "x", "-y", f"-o{destination}", str(source)], path.parent)
            if result.returncode:
                raise RuntimeError((result.stderr or result.stdout or "7-Zip failed").strip())
            if progress:
                progress(total, total)
        return Handler("sevenzip-cli", (".7z", ".rar", ".z"), 80, extract)
    if name in {"unrar.exe", "rar.exe"}:
        def extract(source: Path, destination: Path, progress=None) -> None:
            total = source.stat().st_size
            if progress:
                progress(0, total)
            result = run_hidden([str(path), "x", "-o+", str(source), str(destination) + os.sep], path.parent)
            if result.returncode:
                raise RuntimeError((result.stderr or result.stdout or "UnRAR failed").strip())
            if progress:
                progress(total, total)
        return Handler("unrar-cli", (".rar",), 85, extract)
    result = run_hidden([str(path), "--autoextract-describe"], path.parent)
    if result.returncode:
        return None
    try:
        meta = json.loads(result.stdout)
        extensions = tuple(sorted({str(item).lower() for item in meta["extensions"]}, key=len, reverse=True))
        name = str(meta["name"]).strip().lower()
        priority = int(meta.get("priority", 50))
        return Handler(name, extensions, priority, lambda source, destination, progress=None: run_exe_extract(path, source, destination, progress))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def load_handlers(config: configparser.ConfigParser) -> list[Handler]:
    folder = base_dir() / "filetypes-functionality"
    folder.mkdir(parents=True, exist_ok=True)
    handlers: list[Handler] = []
    for path in sorted(folder.glob("*.py")):
        if path.stem.lower().startswith("example") or path.name.lower().endswith(".progress.py"):
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
        except Exception as exc:
            log(f"Could not load {path.name}: {exc}")
    for path in sorted(folder.glob("*.exe")):
        if path.stem.lower().startswith("example"):
            continue
        try:
            handler = exe_handler(path)
            if handler and config.getboolean("handlers", handler.name, fallback=True):
                handlers.append(handler)
        except Exception as exc:
            log(f"Could not load {path.name}: {exc}")
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


def unique_archive_target(folder: Path, source: Path) -> Path:
    suffix = "".join(source.suffixes)
    stem = source.name[:-len(suffix)] if suffix else source.name
    target = folder / source.name
    number = 2
    while target.exists():
        target = folder / f"{stem} ({number}){suffix}"
        number += 1
    return target


def recycle_file(path: Path) -> None:
    class FileOp(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR), ("fFlags", wintypes.WORD), ("fAnyOperationsAborted", wintypes.BOOL), ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
    op = FileOp()
    op.wFunc = 3
    op.pFrom = str(path) + "\0\0"
    op.fFlags = 0x0040 | 0x0010 | 0x0004 | 0x0400
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if result or op.fAnyOperationsAborted:
        raise OSError(result, "Could not move file to Recycle Bin")


def handle_original(source: Path, action: str) -> None:
    action = action.strip().strip('"')
    if not action or action.lower() in {"n", "no"}:
        return
    if action.lower() == "recycle":
        recycle_file(source)
        return
    folder = expand_path(action)
    folder.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(unique_archive_target(folder, source)))


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
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        handle.close()


def ensure_tray(folder: Path | None = None, force: bool = False) -> None:
    folder = folder or base_dir()
    if os.name != "nt":
        return
    if not force and not load_config().getboolean("settings", "add_to_tray", fallback=False):
        return
    user32 = ctypes.windll.user32
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    if user32.FindWindowW(TRAY_CLASS, None):
        return
    flags = 0x00000008 | 0x00000200 | 0x08000000
    subprocess.Popen([str(pythonw()), str(folder / f"{APP_NAME}.py"), "tray"], cwd=str(folder), creationflags=flags, close_fds=True)


def send_tray_status(file: str, percent: int, done: int, total: int, state: str = "extracting") -> None:
    if os.name != "nt" or not load_config().getboolean("settings", "add_to_tray", fallback=False):
        return
    ensure_tray()
    user32 = ctypes.windll.user32
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    hwnd = user32.FindWindowW(TRAY_CLASS, None)
    if not hwnd:
        return
    payload = json.dumps({"file": file, "percent": percent, "done": done, "total": total, "state": state}).encode("utf-8") + b"\0"
    buffer = ctypes.create_string_buffer(payload)
    class CopyData(ctypes.Structure):
        _fields_ = [("dwData", ctypes.c_size_t), ("cbData", wintypes.DWORD), ("lpData", ctypes.c_void_p)]
    data = CopyData(1, len(payload), ctypes.cast(buffer, ctypes.c_void_p))
    user32.SendMessageW(hwnd, 0x004A, 0, ctypes.addressof(data))


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
            last_total = source.stat().st_size
            last_percent = -1
            def progress(done: int, total: int) -> None:
                nonlocal last_total, last_percent
                last_total = total
                percent = 100 if total <= 0 and done else int(min(100, max(0, done * 100 // max(total, 1))))
                if percent != last_percent:
                    last_percent = percent
                    send_tray_status(source.name, percent, done, total)
            send_tray_status(source.name, 0, 0, last_total)
            try:
                try:
                    supports_progress = len(inspect.signature(handler.extract).parameters) >= 3
                except (TypeError, ValueError):
                    supports_progress = False
                if supports_progress:
                    handler.extract(source, destination, progress)
                else:
                    handler.extract(source, destination)
            except Exception:
                shutil.rmtree(destination, ignore_errors=True)
                send_tray_status(source.name, 0, 0, 0, "failed")
                raise
            state[key] = list(current)
            state_path.write_text(json.dumps(state), encoding="utf-8")
            send_tray_status(source.name, 100, last_total, last_total, "done")
            if settings.getboolean("open_folder", fallback=True) and os.name == "nt":
                os.startfile(destination)
            try:
                handle_original(source, settings.get("move_original_after", "no"))
            except OSError as exc:
                log(f"Could not move original {source}: {exc}")
            return destination
    except Exception as exc:
        log(f"{source}: {exc}")
        return None


def is_admin() -> bool:
    return os.name == "nt" and bool(ctypes.windll.shell32.IsUserAnAdmin())


def service_exists() -> bool:
    return os.name == "nt" and run_hidden(["sc.exe", "query", SERVICE_NAME]).returncode == 0


def wait_for_service_gone(seconds: float = 10.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not service_exists():
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
        code = service_admin("remove", app_folder=app_folder)
        if code:
            return code
    secure_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(watcher, secure_watcher)
    binary = f'"{secure_watcher}" --service --app-dir "{app_folder}"'
    result = run_hidden(["sc.exe", "create", SERVICE_NAME, "binPath=", binary, "start=", "auto", "DisplayName=", APP_NAME])
    if result.returncode:
        return result.returncode
    run_hidden(["sc.exe", "description", SERVICE_NAME, "Watches Downloads and extracts supported archives."])
    run_hidden(["sc.exe", "failure", SERVICE_NAME, "reset=", "86400", "actions=", "restart/5000/restart/15000/none/0"])
    return run_hidden(["sc.exe", "start", SERVICE_NAME]).returncode


def run_elevated_service(action: str, folder: Path, watcher: Path | None = None) -> bool:
    if is_admin():
        return service_admin(action, watcher, folder) == 0
    shell32 = ctypes.windll.shell32
    shell32.ShellExecuteW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
    shell32.ShellExecuteW.restype = wintypes.HINSTANCE
    args = [str(folder / f"{APP_NAME}.py"), "service-admin", action]
    if watcher:
        args.append(str(watcher))
    params = subprocess.list2cmdline(args)
    result = shell32.ShellExecuteW(None, "runas", str(Path(sys.executable).resolve()), params, str(folder), 1)
    if int(result) <= 32:
        return False
    time.sleep(1)
    if action == "remove":
        return not service_exists()
    for _ in range(30):
        if service_exists():
            return True
        time.sleep(0.2)
    return False


def set_startup(enabled: bool, script: Path) -> None:
    import winreg
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            command = subprocess.list2cmdline([str(pythonw()), str(script), "startup"])
            winreg.SetValueEx(key, APP_NAME + "-Startup", 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, APP_NAME + "-Startup")
            except FileNotFoundError:
                pass


def launch_user_watcher(folder: Path | None = None) -> None:
    folder = folder or base_dir()
    watcher = folder / "watcher.exe"
    if watcher.exists():
        subprocess.Popen([str(watcher), "--app-dir", str(folder)], cwd=str(folder), creationflags=0x08000000 | 0x00000008, close_fds=True)


def signal_user_watcher_stop() -> None:
    if os.name != "nt":
        return
    kernel32 = ctypes.windll.kernel32
    event = kernel32.OpenEventW(0x0002, False, WATCHER_STOP_EVENT)
    if event:
        kernel32.SetEvent(event)
        kernel32.CloseHandle(event)


def stop_background() -> None:
    signal_user_watcher_stop()
    if service_exists():
        shell32 = ctypes.windll.shell32
        shell32.ShellExecuteW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
        shell32.ShellExecuteW.restype = wintypes.HINSTANCE
        shell32.ShellExecuteW(None, "runas", "sc.exe", f'stop "{SERVICE_NAME}"', None, 0)


def close_tray() -> None:
    if os.name != "nt":
        return
    user32 = ctypes.windll.user32
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
    user32.PostMessageW.restype = wintypes.BOOL
    hwnd = user32.FindWindowW(TRAY_CLASS, None)
    if hwnd:
        user32.PostMessageW(hwnd, 0x0010, 0, 0)


def startup() -> int:
    config = load_config()
    if config.getboolean("settings", "add_to_tray", fallback=False):
        ensure_tray()
    if not config.getboolean("settings", "startup_service", fallback=False):
        launch_user_watcher()
    return 0


def tray() -> int:
    if os.name != "nt":
        return 1
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.DestroyIcon.argtypes = [wintypes.HANDLE]
    user32.DestroyIcon.restype = wintypes.BOOL
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.CreatePopupMenu.argtypes = []
    user32.CreatePopupMenu.restype = wintypes.HANDLE
    user32.AppendMenuW.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
    user32.AppendMenuW.restype = wintypes.BOOL
    user32.TrackPopupMenu.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
    user32.TrackPopupMenu.restype = wintypes.UINT
    user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    user32.MessageBoxW.restype = ctypes.c_int
    user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterWindowMessageW.restype = wintypes.UINT
    user32.DestroyMenu.argtypes = [wintypes.HANDLE]
    user32.DestroyMenu.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    guard = kernel32.CreateMutexW(None, False, "Local\\" + APP_NAME + "-Tray")
    if guard and kernel32.GetLastError() == 183:
        kernel32.CloseHandle(guard)
        return 0
    LRESULT = ctypes.c_ssize_t
    WPARAM = ctypes.c_size_t
    LPARAM = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)
    class Point(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]
    class CopyData(ctypes.Structure):
        _fields_ = [("dwData", ctypes.c_size_t), ("cbData", wintypes.DWORD), ("lpData", ctypes.c_void_p)]
    class WndClass(ctypes.Structure):
        _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HANDLE), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HANDLE), ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]
    class Guid(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]
    class NotifyIcon(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT), ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HANDLE), ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256), ("uTimeoutOrVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD), ("guidItem", Guid), ("hBalloonIcon", wintypes.HANDLE)]
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WndClass)]
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HANDLE, wintypes.HINSTANCE, ctypes.c_void_p]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NotifyIcon)]
    shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    callback_message = 0x8001
    taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
    icons = base_dir() / "tray-icons"
    current_icon = None
    nid = NotifyIcon()
    def load_icon(name: str):
        return user32.LoadImageW(None, str(icons / name), 1, 16, 16, 0x0010)
    def set_icon(hwnd, name: str, tip: str) -> None:
        nonlocal current_icon
        icon = load_icon(name)
        if not icon:
            return
        nid.cbSize = ctypes.sizeof(NotifyIcon); nid.hWnd = hwnd; nid.uID = 1; nid.uFlags = 0x2 | 0x4; nid.hIcon = icon; nid.szTip = tip[:127]
        shell32.Shell_NotifyIconW(1, ctypes.byref(nid))
        if current_icon:
            user32.DestroyIcon(current_icon)
        current_icon = icon
    def update_status(hwnd, status: dict) -> None:
        state = status.get("state", "extracting")
        name = str(status.get("file", ""))
        percent = max(0, min(100, int(status.get("percent", 0))))
        done = int(status.get("done", 0)); total = int(status.get("total", 0))
        if state == "failed":
            set_icon(hwnd, "au logo1.ico", f"{name} | failed"); return
        if state == "done":
            percent = 100
        index = round(percent * 13 / 100)
        suffix = f" | {percent}% | {done / 1048576:.1f} MB/{total / 1048576:.1f} MB"
        set_icon(hwnd, TRAY_FRAMES[index], name[:max(1, 127-len(suffix))] + suffix)
    @WNDPROC
    def wndproc(hwnd, msg, wparam, lparam):
        if msg == taskbar_created:
            shell32.Shell_NotifyIconW(0, ctypes.byref(nid)); return 0
        if msg == 0x004A:
            data = ctypes.cast(lparam, ctypes.POINTER(CopyData)).contents
            try:
                update_status(hwnd, json.loads(ctypes.string_at(data.lpData, data.cbData).rstrip(b"\0").decode("utf-8")))
            except Exception:
                pass
            return 1
        if msg == callback_message and int(lparam) == 0x0205:
            menu = user32.CreatePopupMenu()
            for ident, label in [(1,"Config"),(2,"Exit"),(3,"Open Output Location"),(5,"Uninstall"),(4,"Close")]:
                user32.AppendMenuW(menu, 0, ident, label)
            point = Point(); user32.GetCursorPos(ctypes.byref(point)); user32.SetForegroundWindow(hwnd)
            command = user32.TrackPopupMenu(menu, 0x0100 | 0x0002, point.x, point.y, 0, hwnd, None); user32.DestroyMenu(menu)
            if command == 1:
                os.startfile(base_dir() / "config.ini")
            elif command == 2:
                stop_background(); user32.DestroyWindow(hwnd)
            elif command == 3:
                _, dump = configured_paths(load_config()); dump.mkdir(parents=True, exist_ok=True); os.startfile(dump)
            elif command == 5:
                answer = user32.MessageBoxW(hwnd, "Uninstall Auto_Unzip-Save-Open?\n\nExtracted files will be kept.", APP_NAME, 0x24)
                if answer == 6 and uninstall(False) == 0:
                    user32.DestroyWindow(hwnd)
            elif command == 4:
                user32.DestroyWindow(hwnd)
            return 0
        if msg == 0x0002:
            shell32.Shell_NotifyIconW(2, ctypes.byref(nid)); user32.PostQuitMessage(0); return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
    instance = kernel32.GetModuleHandleW(None)
    wc = WndClass(); wc.lpfnWndProc = wndproc; wc.hInstance = instance; wc.lpszClassName = TRAY_CLASS
    user32.RegisterClassW(ctypes.byref(wc))
    hwnd = user32.CreateWindowExW(0, TRAY_CLASS, APP_NAME, 0, 0, 0, 0, 0, None, None, instance, None)
    if not hwnd:
        return 2
    nid.cbSize = ctypes.sizeof(NotifyIcon); nid.hWnd = hwnd; nid.uID = 1; nid.uFlags = 1|2|4; nid.uCallbackMessage = callback_message; nid.hIcon = load_icon("au logo1.ico"); current_icon = nid.hIcon; nid.szTip = "Watching Downloads"
    shell32.Shell_NotifyIconW(0, ctypes.byref(nid))
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg)); user32.DispatchMessageW(ctypes.byref(msg))
    if current_icon:
        user32.DestroyIcon(current_icon)
    if guard:
        kernel32.CloseHandle(guard)
    return 0


def compiler_candidates() -> list[Path]:
    names = ("x86_64-w64-mingw32-g++.exe", "x86_64-w64-mingw32-c++.exe", "g++.exe", "clang++.exe")
    found: list[Path] = []
    for name in names:
        value = shutil.which(name)
        if value:
            found.append(Path(value))
    for root in (Path(r"C:\Mingw\Mingw64\bin"), Path(r"C:\Mingw\mingw64\bin"), Path(r"C:\mingw64\bin"), Path(r"C:\msys64\mingw64\bin"), Path(r"C:\Mingw\64\bin")):
        for name in names:
            path = root / name
            if path.exists():
                found.append(path)
    return list(dict.fromkeys(found))


def build_watcher(folder: Path) -> Path | None:
    output = folder / "watcher.exe"
    src = folder / "src"
    sources = [src / "watcher.cpp", src / "runtime.cpp"]
    for compiler in compiler_candidates():
        command = [str(compiler), "-std=c++17", "-O2", "-s", "-municode", "-mwindows", "-static", *map(str, sources), "-I", str(src), "-o", str(output), "-lshell32", "-lwtsapi32", "-luserenv", "-ladvapi32"]
        if run_hidden(command, folder).returncode == 0 and output.exists():
            return output
    return None


def schedule_install_cleanup(folder: Path, protected: list[Path]) -> None:
    folder = folder.resolve(); protected = [p.resolve() for p in protected]
    names = [f"{APP_NAME}.py", "config.ini", "watcher.exe", "src", "filetypes-functionality", "tray-icons", f"{APP_NAME}.log", ".state.json", ".extract.lock"]
    targets = []
    for name in names:
        target = (folder / name).resolve()
        if not any(_contains(target, p) for p in protected):
            targets.append(str(target))
    ps = ["Start-Sleep -Seconds 2"]
    for target in targets:
        ps.append(f"Remove-Item -LiteralPath '{target.replace(chr(39), chr(39)*2)}' -Recurse -Force -ErrorAction SilentlyContinue")
    ps.append(f"Remove-Item -LiteralPath '{str(folder).replace(chr(39), chr(39)*2)}' -Force -ErrorAction SilentlyContinue")
    subprocess.Popen(["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", "; ".join(ps)], creationflags=0x08000000 | 0x00000008, close_fds=True)


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent); return True
    except ValueError:
        return False


def install() -> int:
    if os.name != "nt":
        print("Windows only."); return 1
    startup_service = input("Add startup service? (Y / N) ").strip().lower() in {"y","yes"}
    add_startup = input("Add to startup? (Y / N) ").strip().lower() in {"y","yes"}
    add_tray = input("Add to tray? (Y / N) ").strip().lower() in {"y","yes"}
    text = input("Downloads folder? Leave blank = default ").strip(); downloads = expand_path(text) if text else (Path.home()/"Downloads").resolve()
    text = input("Dump folder? Leave blank = default ").strip(); dump = expand_path(text) if text else (downloads/"Dump").resolve()
    text = input("Install folder? Leave blank = default ").strip(); folder = expand_path(text) if text else (Path(os.environ["LOCALAPPDATA"])/APP_NAME).resolve()
    excludes = input("File types to exclude? Put a comma between each file type ").strip()
    move_after = input('Move the original ZIP after extraction? Usage: Enter a directory address, "N"/"no", or "recycle" to move it to the Recycle Bin. ').strip()
    if not move_after or move_after.lower() in {"n","no"}: move_after = "no"
    elif move_after.lower() == "recycle": move_after = "recycle"
    else:
        move_folder = expand_path(move_after); move_folder.mkdir(parents=True, exist_ok=True); move_after = str(move_folder)
    source = base_dir()
    signal_user_watcher_stop()
    if service_exists() and not run_elevated_service("remove", source):
        print("Could not stop the existing startup service.")
        return 1
    close_tray()
    time.sleep(0.5)
    folder.mkdir(parents=True, exist_ok=True); downloads.mkdir(parents=True, exist_ok=True); dump.mkdir(parents=True, exist_ok=True)
    for name in [f"{APP_NAME}.py", "install.bat", "Build-Watcher-MinGW.bat", "Build-Watcher-MSVC.bat"]:
        if (source/name).exists(): copy_file(source/name, folder/name)
    copy_tree(source/"src", folder/"src")
    copy_tree(source/"filetypes-functionality", folder/"filetypes-functionality")
    copy_tree(source/"tray-icons", folder/"tray-icons")
    watcher = folder/"watcher.exe"
    if (source/"watcher.exe").exists(): copy_file(source/"watcher.exe", watcher)
    if not watcher.exists(): watcher = build_watcher(folder)
    if not watcher:
        print("Could not build watcher.exe. Run Build-Watcher-MinGW.bat or Build-Watcher-MSVC.bat first."); return 1
    config = configparser.ConfigParser(interpolation=None)
    config["settings"] = {"downloads_folder":str(downloads),"dump_folder":str(dump),"install_folder":str(folder),"filetypes_to_exclude":excludes,"move_original_after":move_after,"open_folder":"yes","startup_service":"yes" if startup_service else "no","startup":"yes" if add_startup else "no","add_to_tray":"yes" if add_tray else "no","stable_seconds":"2.0"}
    config["handlers"] = {"zip":"yes","formats":"yes"}
    config["runtime"] = {"python":str(Path(sys.executable).resolve())}
    with (folder/"config.ini").open("w",encoding="utf-8") as handle: config.write(handle)
    if startup_service:
        if not run_elevated_service("install", folder, watcher): print("Could not install the startup service."); return 1
    else: launch_user_watcher(folder)
    set_startup(add_startup, folder/f"{APP_NAME}.py")
    if add_tray: ensure_tray(folder, True)
    print(f"Installed: {folder}"); return 0


def uninstall(close_tray_window: bool = True) -> int:
    if os.name != "nt": return 1
    config = load_config(); settings = config["settings"]
    folder = expand_path(settings.get("install_folder", "")) if settings.get("install_folder", "").strip() else base_dir()
    downloads, dump = configured_paths(config); protected = [downloads, dump]
    move_after = settings.get("move_original_after", "no").strip()
    if move_after and move_after.lower() not in {"n","no","recycle"}: protected.append(expand_path(move_after))
    set_startup(False, folder/f"{APP_NAME}.py"); signal_user_watcher_stop()
    if service_exists() and not run_elevated_service("remove", folder): print("Could not remove the startup service."); return 1
    if close_tray_window: close_tray()
    schedule_install_cleanup(folder, protected)
    print("Uninstalled. Extracted files were left untouched."); return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument("command", nargs="?", choices=("install","process","uninstall","service-admin","startup","tray"), default="install")
    parser.add_argument("path", nargs="?"); parser.add_argument("extra", nargs="?")
    args = parser.parse_args()
    if args.command == "install": return install()
    if args.command == "uninstall": return uninstall()
    if args.command == "startup": return startup()
    if args.command == "tray": return tray()
    if args.command == "service-admin": return service_admin(args.path or "", Path(args.extra) if args.extra else None, base_dir())
    if not args.path: return 2
    result = process_file(Path(args.path))
    if result: print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

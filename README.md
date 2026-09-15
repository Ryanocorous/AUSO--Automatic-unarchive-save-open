# AUSO - Auto unzip, save, and open
Downloaded a zip file? Great. Let's rawdog that badboy and tear it wide open, filing it away and opening it up automatically. This project adds a config to make it customisable, supports multiple archive formats, and saves you seconds on routine downloads.

## What does it do?
AUSO watches folders and automatically extracts new archives.

| Setting | What it does |
|---|---|
| Watcher | Watches enabled folders for new archives. |
| Output | Where extracted files are saved. |
| Open output | Opens the extracted folder when finished. |
| Move original | Leaves, moves, or recycles the original archive. |
| Startup | Starts AUSO when you sign in. |
| Service | Keeps the Original edition watcher running in the background. |
| Tray | Shows AUSO in the Windows tray. |

Employs a watcher to check when archive files are downloaded. It then automatically unzips them, opens the folder, and sorts them. It adds a tray icon that gives progress and settings config. It has a C++ watcher and Python script base. Filetype functionality allows more archive types to be added, and they can be wired into the main Python script. That's it really.

Still to do: Add comments to the code, add alerts, filtering options, a TypeScript GUI, folder hotkeys, extra watched folders, about, history, pause, malicious zip rejection (coming very soon), Linux tests, and Contract.json shared commands.

Use: Just download the release and run install.bat

## Build

One-command build:

```text
Build-Complete.bat
```

MinGW-w64:

```text
Build-Watcher-MinGW.bat
```

MSVC Developer Command Prompt:

```text
Build-Watcher-MSVC.bat
```

Both build `watcher.exe` from:

```text
src\watcher.cpp
src\runtime.cpp
src\runtime.hpp
```

Then run `install.bat`.

The project also includes the Python extractor, tray UI, all loading icons 1–14, Python/EXE handler examples, EXE progress hooks, service/startup support, move/recycle handling and uninstall.

# AUSO - Auto unzip, save, and open source

For releases, check releases on the github page. This needs compiling still. Alternatively, use the portable.

NOTE: THIS IS NOT THE RELEASE, THIS IS THE SOURCE FILES.

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

Employs a watcher to check when archive files are downloaded. It then automatically unzips them, opens the folder, and sorts them. It adds a tray icon that gives progress and settings config. It has a cpp watcher and python script base. filetypes-functionality gives the ability to add more archive types. Also add these to the main python script. That's it really.

Still to do: Add comments to the code, I originally made this just for myself so I never bothered. I regret that. I also need to add alerts, filtering options, a typescript gui, folder hotkeys, extra watched folders, about, history, pause for one  Malicious zip rejection (coming very soon). Linux tests. Contract.json shared commands.

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

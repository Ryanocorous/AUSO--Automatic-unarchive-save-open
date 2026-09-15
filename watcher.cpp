#define UNICODE
#define _UNICODE
#include <windows.h>
#include <shellapi.h>
#include <wtsapi32.h>
#include <userenv.h>
#include <filesystem>
#include <fstream>
#include <map>
#include <string>
#include <vector>
#include <chrono>
#include <algorithm>
#include <cwctype>
#include <iterator>

using Clock = std::chrono::steady_clock;
namespace fs = std::filesystem;

const wchar_t* SERVICE_NAME = L"Auto_Unzip-Save-Open";
SERVICE_STATUS_HANDLE serviceHandle = nullptr;
SERVICE_STATUS serviceStatus{};
HANDLE stopEvent = nullptr;
fs::path configuredAppDir;

struct Pending {
    unsigned long long size = 0;
    unsigned long long write = 0;
    Clock::time_point changed = Clock::now();
};

fs::path appDir() {
    if (!configuredAppDir.empty()) return configuredAppDir;
    std::vector<wchar_t> buffer(32768);
    DWORD length = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    return fs::path(std::wstring(buffer.data(), length)).parent_path();
}

std::wstring expandEnv(const std::wstring& value) {
    if (value.empty()) return value;
    DWORD length = ExpandEnvironmentStringsW(value.c_str(), nullptr, 0);
    if (!length) return value;
    std::vector<wchar_t> buffer(length);
    if (!ExpandEnvironmentStringsW(value.c_str(), buffer.data(), length)) return value;
    return buffer.data();
}

std::wstring iniValue(const wchar_t* section, const wchar_t* key, const std::wstring& fallback = L"") {
    wchar_t buffer[32768];
    auto ini = appDir() / L"config.ini";
    GetPrivateProfileStringW(section, key, fallback.c_str(), buffer, 32768, ini.c_str());
    return expandEnv(buffer);
}

bool iniBool(const wchar_t* section, const wchar_t* key, bool fallback) {
    auto value = iniValue(section, key, fallback ? L"yes" : L"no");
    std::transform(value.begin(), value.end(), value.begin(), towlower);
    return value == L"yes" || value == L"true" || value == L"1" || value == L"y";
}

std::wstring quote(const std::wstring& value) {
    return L"\"" + value + L"\"";
}

bool fileSignature(const fs::path& path, unsigned long long& size, unsigned long long& write) {
    WIN32_FILE_ATTRIBUTE_DATA data{};
    if (!GetFileAttributesExW(path.c_str(), GetFileExInfoStandard, &data)) return false;
    if (data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) return false;
    ULARGE_INTEGER s{};
    s.HighPart = data.nFileSizeHigh;
    s.LowPart = data.nFileSizeLow;
    ULARGE_INTEGER t{};
    t.HighPart = data.ftLastWriteTime.dwHighDateTime;
    t.LowPart = data.ftLastWriteTime.dwLowDateTime;
    size = s.QuadPart;
    write = t.QuadPart;
    return true;
}

bool temporaryName(const fs::path& path) {
    std::wstring name = path.filename().wstring();
    std::transform(name.begin(), name.end(), name.begin(), towlower);
    const wchar_t* suffixes[] = {L".crdownload", L".part", L".partial", L".download", L".tmp"};
    for (auto suffix : suffixes) {
        size_t length = wcslen(suffix);
        if (name.size() >= length && name.compare(name.size() - length, length, suffix) == 0) return true;
    }
    return false;
}

std::wstring utf8ToWide(const std::string& text) {
    if (text.empty()) return L"";
    int length = MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), nullptr, 0);
    if (!length) return L"";
    std::wstring out(length, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), out.data(), length);
    return out;
}

HANDLE activeUserToken() {
    DWORD session = WTSGetActiveConsoleSessionId();
    if (session == 0xFFFFFFFF) return nullptr;
    HANDLE token = nullptr;
    if (!WTSQueryUserToken(session, &token)) return nullptr;
    HANDLE primary = nullptr;
    if (!DuplicateTokenEx(token, TOKEN_ALL_ACCESS, nullptr, SecurityImpersonation, TokenPrimary, &primary)) primary = nullptr;
    CloseHandle(token);
    return primary;
}

bool createAsUser(HANDLE token, std::wstring command, const fs::path& cwd, PROCESS_INFORMATION& process) {
    void* environment = nullptr;
    CreateEnvironmentBlock(&environment, token, FALSE);
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    startup.lpDesktop = const_cast<LPWSTR>(L"winsta0\\default");
    DWORD flags = CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW;
    BOOL ok = CreateProcessAsUserW(token, nullptr, command.data(), nullptr, nullptr, FALSE, flags,
        environment, cwd.c_str(), &startup, &process);
    if (environment) DestroyEnvironmentBlock(environment);
    return ok == TRUE;
}

bool openFolderForUser(const fs::path& folder) {
    HANDLE token = activeUserToken();
    if (!token) return false;

    wchar_t windowsDir[MAX_PATH];
    GetWindowsDirectoryW(windowsDir, MAX_PATH);
    fs::path explorer = fs::path(windowsDir) / L"explorer.exe";
    std::wstring command = quote(explorer.wstring()) + L" " + quote(folder.wstring());

    void* environment = nullptr;
    CreateEnvironmentBlock(&environment, token, FALSE);
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    startup.lpDesktop = const_cast<LPWSTR>(L"winsta0\\default");
    PROCESS_INFORMATION process{};
    BOOL ok = CreateProcessAsUserW(token, explorer.c_str(), command.data(), nullptr, nullptr, FALSE,
        CREATE_UNICODE_ENVIRONMENT | CREATE_NEW_PROCESS_GROUP, environment, nullptr, &startup, &process);

    if (ok) {
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
    }
    if (environment) DestroyEnvironmentBlock(environment);
    CloseHandle(token);
    return ok == TRUE;
}

void openFolder(const fs::path& folder, bool serviceMode) {
    if (!iniBool(L"settings", L"open_folder", true)) return;
    if (serviceMode) {
        openFolderForUser(folder);
    } else {
        ShellExecuteW(nullptr, L"open", folder.c_str(), nullptr, nullptr, SW_SHOWNORMAL);
    }
}

bool runExtractor(const fs::path& source, bool serviceMode) {
    auto python = iniValue(L"runtime", L"python");
    if (python.empty()) return false;

    auto script = appDir() / L"Auto_Unzip-Save-Open.py";
    auto result = appDir() / (L".result-" + std::to_wstring(GetCurrentProcessId()) + L".txt");
    DeleteFileW(result.c_str());
    std::wstring command = quote(python) + L" " + quote(script.wstring()) + L" process " + quote(source.wstring()) + L" " + quote(result.wstring());

    PROCESS_INFORMATION process{};
    BOOL ok = FALSE;
    HANDLE token = nullptr;

    if (serviceMode) {
        token = activeUserToken();
        if (token) ok = createAsUser(token, command, appDir(), process);
    } else {
        STARTUPINFOW startup{};
        startup.cb = sizeof(startup);
        ok = CreateProcessW(nullptr, command.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW,
            nullptr, appDir().c_str(), &startup, &process);
    }

    if (token) CloseHandle(token);
    if (!ok) return false;

    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD exitCode = 1;
    GetExitCodeProcess(process.hProcess, &exitCode);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    if (exitCode != 0 || !fs::exists(result)) return false;

    std::ifstream input(result, std::ios::binary);
    std::string text((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
    input.close();
    DeleteFileW(result.c_str());
    while (!text.empty() && (text.back() == '\r' || text.back() == '\n')) text.pop_back();
    if (!text.empty()) openFolder(fs::path(utf8ToWide(text)), serviceMode);
    return true;
}

void addPending(std::map<std::wstring, Pending>& pending, const fs::path& path) {
    if (temporaryName(path)) return;
    unsigned long long size = 0, write = 0;
    if (!fileSignature(path, size, write)) return;
    pending[path.wstring()] = {size, write, Clock::now()};
}

void processPending(std::map<std::wstring, Pending>& pending, int stableMs, bool serviceMode) {
    auto now = Clock::now();
    for (auto it = pending.begin(); it != pending.end();) {
        fs::path path(it->first);
        unsigned long long size = 0, write = 0;
        if (!fileSignature(path, size, write)) {
            it = pending.erase(it);
            continue;
        }
        if (size != it->second.size || write != it->second.write) {
            it->second.size = size;
            it->second.write = write;
            it->second.changed = now;
            ++it;
            continue;
        }
        auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(now - it->second.changed).count();
        if (elapsed < stableMs) {
            ++it;
            continue;
        }
        runExtractor(path, serviceMode);
        it = pending.erase(it);
    }
}

int watchDirectory(HANDLE stop, bool serviceMode) {
    auto downloads = iniValue(L"settings", L"downloads_folder");
    if (downloads.empty()) return 2;

    int stableMs = 2000;
    try {
        stableMs = std::max(500, static_cast<int>(std::stod(iniValue(L"settings", L"stable_seconds", L"2.0")) * 1000.0));
    } catch (...) {}

    HANDLE directory = CreateFileW(downloads.c_str(), FILE_LIST_DIRECTORY,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr, OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OVERLAPPED, nullptr);
    if (directory == INVALID_HANDLE_VALUE) return 3;

    HANDLE changeEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (!changeEvent) {
        CloseHandle(directory);
        return 4;
    }

    OVERLAPPED overlapped{};
    overlapped.hEvent = changeEvent;
    std::vector<unsigned char> buffer(65536);
    std::map<std::wstring, Pending> pending;

    auto issueRead = [&]() -> bool {
        ResetEvent(changeEvent);
        return ReadDirectoryChangesW(directory, buffer.data(), static_cast<DWORD>(buffer.size()), FALSE,
            FILE_NOTIFY_CHANGE_FILE_NAME | FILE_NOTIFY_CHANGE_SIZE | FILE_NOTIFY_CHANGE_LAST_WRITE,
            nullptr, &overlapped, nullptr) == TRUE;
    };

    if (!issueRead()) {
        CloseHandle(changeEvent);
        CloseHandle(directory);
        return 5;
    }

    while (true) {
        DWORD timeout = pending.empty() ? INFINITE : 250;
        DWORD waitResult;

        if (stop) {
            HANDLE handles[] = {changeEvent, stop};
            waitResult = WaitForMultipleObjects(2, handles, FALSE, timeout);
            if (waitResult == WAIT_OBJECT_0 + 1) break;
        } else {
            waitResult = WaitForSingleObject(changeEvent, timeout);
        }

        if (waitResult == WAIT_OBJECT_0) {
            DWORD bytes = 0;
            if (!GetOverlappedResult(directory, &overlapped, &bytes, FALSE)) break;
            size_t offset = 0;
            while (offset < bytes) {
                auto info = reinterpret_cast<FILE_NOTIFY_INFORMATION*>(buffer.data() + offset);
                std::wstring name(info->FileName, info->FileNameLength / sizeof(wchar_t));
                if (info->Action == FILE_ACTION_ADDED || info->Action == FILE_ACTION_MODIFIED || info->Action == FILE_ACTION_RENAMED_NEW_NAME) {
                    addPending(pending, fs::path(downloads) / name);
                }
                if (info->NextEntryOffset == 0) break;
                offset += info->NextEntryOffset;
            }
            if (!issueRead()) break;
        } else if (waitResult == WAIT_FAILED) {
            break;
        }

        if (!pending.empty()) processPending(pending, stableMs, serviceMode);
    }

    CancelIoEx(directory, &overlapped);
    CloseHandle(changeEvent);
    CloseHandle(directory);
    return 0;
}

void setServiceState(DWORD state, DWORD win32Exit = NO_ERROR, DWORD waitHint = 0, DWORD serviceExit = 0) {
    serviceStatus.dwServiceType = SERVICE_WIN32_OWN_PROCESS;
    serviceStatus.dwCurrentState = state;
    serviceStatus.dwControlsAccepted = state == SERVICE_RUNNING ? SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_SHUTDOWN : 0;
    serviceStatus.dwWin32ExitCode = win32Exit;
    serviceStatus.dwServiceSpecificExitCode = serviceExit;
    serviceStatus.dwCheckPoint = 0;
    serviceStatus.dwWaitHint = waitHint;
    if (serviceHandle) SetServiceStatus(serviceHandle, &serviceStatus);
}

void WINAPI serviceControl(DWORD control) {
    if (control == SERVICE_CONTROL_STOP || control == SERVICE_CONTROL_SHUTDOWN) {
        setServiceState(SERVICE_STOP_PENDING, NO_ERROR, 3000);
        if (stopEvent) SetEvent(stopEvent);
    }
}

void WINAPI serviceMain(DWORD, LPWSTR*) {
    serviceHandle = RegisterServiceCtrlHandlerW(SERVICE_NAME, serviceControl);
    if (!serviceHandle) return;

    stopEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (!stopEvent) {
        setServiceState(SERVICE_STOPPED, GetLastError());
        return;
    }

    setServiceState(SERVICE_RUNNING);
    int result = watchDirectory(stopEvent, true);
    CloseHandle(stopEvent);
    stopEvent = nullptr;
    setServiceState(SERVICE_STOPPED, result == 0 ? NO_ERROR : ERROR_SERVICE_SPECIFIC_ERROR, 0, result == 0 ? 0 : static_cast<DWORD>(result));
}

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    int argc = 0;
    LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    bool serviceMode = false;
    for (int i = 1; argv && i < argc; ++i) {
        std::wstring arg = argv[i];
        if (arg == L"--service") serviceMode = true;
        if (arg == L"--app-dir" && i + 1 < argc) configuredAppDir = fs::path(argv[++i]);
    }
    if (argv) LocalFree(argv);

    if (serviceMode) {
        SERVICE_TABLE_ENTRYW table[] = {
            {const_cast<LPWSTR>(SERVICE_NAME), serviceMain},
            {nullptr, nullptr}
        };
        return StartServiceCtrlDispatcherW(table) ? 0 : static_cast<int>(GetLastError());
    }

    HANDLE mutex = CreateMutexW(nullptr, FALSE, L"Local\\Auto_Unzip-Save-Open-Watcher");
    if (mutex && GetLastError() == ERROR_ALREADY_EXISTS) {
        CloseHandle(mutex);
        return 0;
    }

    int result = watchDirectory(nullptr, false);
    if (mutex) CloseHandle(mutex);
    return result;
}

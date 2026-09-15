#define UNICODE
#define _UNICODE
#include "runtime.hpp"
#include <shellapi.h>
#include <map>
#include <vector>
#include <string>
#include <chrono>
#include <algorithm>

using Clock = std::chrono::steady_clock;
namespace fs = std::filesystem;

static const wchar_t* SERVICE_NAME = L"Auto_Unzip-Save-Open";
static const wchar_t* USER_STOP_EVENT = L"Local\\Auto_Unzip-Save-Open-Watcher-Stop";
static SERVICE_STATUS_HANDLE g_service_handle = nullptr;
static SERVICE_STATUS g_service_status{};
static HANDLE g_service_stop = nullptr;

struct Pending {
    unsigned long long size = 0;
    unsigned long long write_time = 0;
    Clock::time_point changed = Clock::now();
};

static void add_pending(std::map<std::wstring, Pending>& pending, const fs::path& path) {
    if (auso::temporary_name(path)) return;
    unsigned long long size = 0, write_time = 0;
    if (!auso::file_signature(path, size, write_time)) return;
    pending[path.wstring()] = {size, write_time, Clock::now()};
}

static void process_pending(std::map<std::wstring, Pending>& pending, int stable_ms, bool service_mode) {
    auto now = Clock::now();
    for (auto it = pending.begin(); it != pending.end();) {
        fs::path path(it->first);
        unsigned long long size = 0, write_time = 0;
        if (!auso::file_signature(path, size, write_time)) {
            it = pending.erase(it);
            continue;
        }
        if (size != it->second.size || write_time != it->second.write_time) {
            it->second.size = size;
            it->second.write_time = write_time;
            it->second.changed = now;
            ++it;
            continue;
        }
        auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(now - it->second.changed).count();
        if (elapsed < stable_ms) {
            ++it;
            continue;
        }
        auso::run_extractor(path, service_mode);
        it = pending.erase(it);
    }
}

static int watch_directory(HANDLE stop_event, bool service_mode) {
    auto downloads = auso::ini_value(L"settings", L"downloads_folder");
    if (downloads.empty()) return 2;

    int stable_ms = 2000;
    try {
        stable_ms = std::max(500, static_cast<int>(std::stod(auso::ini_value(L"settings", L"stable_seconds", L"2.0")) * 1000.0));
    } catch (...) {}

    HANDLE directory = CreateFileW(
        downloads.c_str(), FILE_LIST_DIRECTORY,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OVERLAPPED, nullptr);
    if (directory == INVALID_HANDLE_VALUE) return 3;

    HANDLE change_event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (!change_event) {
        CloseHandle(directory);
        return 4;
    }

    OVERLAPPED overlapped{};
    overlapped.hEvent = change_event;
    std::vector<unsigned char> buffer(65536);
    std::map<std::wstring, Pending> pending;

    auto issue_read = [&]() -> bool {
        ResetEvent(change_event);
        return ReadDirectoryChangesW(
            directory, buffer.data(), static_cast<DWORD>(buffer.size()), FALSE,
            FILE_NOTIFY_CHANGE_FILE_NAME | FILE_NOTIFY_CHANGE_SIZE | FILE_NOTIFY_CHANGE_LAST_WRITE,
            nullptr, &overlapped, nullptr) == TRUE;
    };

    if (!issue_read()) {
        CloseHandle(change_event);
        CloseHandle(directory);
        return 5;
    }

    while (true) {
        DWORD timeout = pending.empty() ? INFINITE : 250;
        DWORD wait_result;
        if (stop_event) {
            HANDLE handles[] = {change_event, stop_event};
            wait_result = WaitForMultipleObjects(2, handles, FALSE, timeout);
            if (wait_result == WAIT_OBJECT_0 + 1) break;
        } else {
            wait_result = WaitForSingleObject(change_event, timeout);
        }

        if (wait_result == WAIT_OBJECT_0) {
            DWORD bytes = 0;
            if (!GetOverlappedResult(directory, &overlapped, &bytes, FALSE)) break;
            size_t offset = 0;
            while (offset < bytes) {
                auto* info = reinterpret_cast<FILE_NOTIFY_INFORMATION*>(buffer.data() + offset);
                std::wstring name(info->FileName, info->FileNameLength / sizeof(wchar_t));
                if (info->Action == FILE_ACTION_ADDED ||
                    info->Action == FILE_ACTION_MODIFIED ||
                    info->Action == FILE_ACTION_RENAMED_NEW_NAME) {
                    add_pending(pending, fs::path(downloads) / name);
                }
                if (info->NextEntryOffset == 0) break;
                offset += info->NextEntryOffset;
            }
            if (!issue_read()) break;
        } else if (wait_result == WAIT_FAILED) {
            break;
        }

        if (!pending.empty()) process_pending(pending, stable_ms, service_mode);
    }

    CancelIoEx(directory, &overlapped);
    CloseHandle(change_event);
    CloseHandle(directory);
    return 0;
}

static void set_service_state(DWORD state, DWORD win32_exit = NO_ERROR, DWORD wait_hint = 0, DWORD service_exit = 0) {
    g_service_status.dwServiceType = SERVICE_WIN32_OWN_PROCESS;
    g_service_status.dwCurrentState = state;
    g_service_status.dwControlsAccepted = state == SERVICE_RUNNING ? SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_SHUTDOWN : 0;
    g_service_status.dwWin32ExitCode = win32_exit;
    g_service_status.dwServiceSpecificExitCode = service_exit;
    g_service_status.dwCheckPoint = 0;
    g_service_status.dwWaitHint = wait_hint;
    if (g_service_handle) SetServiceStatus(g_service_handle, &g_service_status);
}

static void WINAPI service_control(DWORD control) {
    if (control == SERVICE_CONTROL_STOP || control == SERVICE_CONTROL_SHUTDOWN) {
        set_service_state(SERVICE_STOP_PENDING, NO_ERROR, 3000);
        if (g_service_stop) SetEvent(g_service_stop);
    }
}

static void WINAPI service_main(DWORD, LPWSTR*) {
    g_service_handle = RegisterServiceCtrlHandlerW(SERVICE_NAME, service_control);
    if (!g_service_handle) return;
    g_service_stop = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (!g_service_stop) {
        set_service_state(SERVICE_STOPPED, GetLastError());
        return;
    }
    set_service_state(SERVICE_RUNNING);
    int result = watch_directory(g_service_stop, true);
    CloseHandle(g_service_stop);
    g_service_stop = nullptr;
    set_service_state(
        SERVICE_STOPPED,
        result == 0 ? NO_ERROR : ERROR_SERVICE_SPECIFIC_ERROR,
        0,
        result == 0 ? 0 : static_cast<DWORD>(result));
}

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    int argc = 0;
    LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    bool service_mode = false;
    for (int i = 1; argv && i < argc; ++i) {
        std::wstring arg = argv[i];
        if (arg == L"--service") service_mode = true;
        if (arg == L"--app-dir" && i + 1 < argc) auso::g_app_dir = fs::path(argv[++i]);
    }
    if (argv) LocalFree(argv);

    if (service_mode) {
        SERVICE_TABLE_ENTRYW table[] = {
            {const_cast<LPWSTR>(SERVICE_NAME), service_main},
            {nullptr, nullptr}
        };
        return StartServiceCtrlDispatcherW(table) ? 0 : static_cast<int>(GetLastError());
    }

    HANDLE mutex = CreateMutexW(nullptr, FALSE, L"Local\\Auto_Unzip-Save-Open-Watcher");
    if (mutex && GetLastError() == ERROR_ALREADY_EXISTS) {
        CloseHandle(mutex);
        return 0;
    }
    HANDLE stop_event = CreateEventW(nullptr, TRUE, FALSE, USER_STOP_EVENT);
    int result = watch_directory(stop_event, false);
    if (stop_event) CloseHandle(stop_event);
    if (mutex) CloseHandle(mutex);
    return result;
}

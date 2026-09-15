#define UNICODE
#define _UNICODE
#include "runtime.hpp"
#include <wtsapi32.h>
#include <userenv.h>
#include <algorithm>
#include <cwchar>
#include <cwctype>
#include <vector>

namespace auso {
fs::path g_app_dir;

fs::path app_dir() {
    if (!g_app_dir.empty()) return g_app_dir;
    std::vector<wchar_t> buffer(32768);
    DWORD length = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    return fs::path(std::wstring(buffer.data(), length)).parent_path();
}

static std::wstring expand_env(const std::wstring& value) {
    if (value.empty()) return value;
    DWORD length = ExpandEnvironmentStringsW(value.c_str(), nullptr, 0);
    if (!length) return value;
    std::vector<wchar_t> buffer(length);
    if (!ExpandEnvironmentStringsW(value.c_str(), buffer.data(), length)) return value;
    return buffer.data();
}

std::wstring ini_value(const wchar_t* section, const wchar_t* key, const std::wstring& fallback) {
    std::vector<wchar_t> buffer(32768);
    auto config = app_dir() / L"config.ini";
    GetPrivateProfileStringW(section, key, fallback.c_str(), buffer.data(), static_cast<DWORD>(buffer.size()), config.c_str());
    return expand_env(buffer.data());
}

bool ini_bool(const wchar_t* section, const wchar_t* key, bool fallback) {
    auto value = ini_value(section, key, fallback ? L"yes" : L"no");
    std::transform(value.begin(), value.end(), value.begin(), towlower);
    return value == L"yes" || value == L"true" || value == L"1" || value == L"y";
}

std::wstring quote(const std::wstring& value) {
    return L"\"" + value + L"\"";
}

bool temporary_name(const fs::path& path) {
    std::wstring name = path.filename().wstring();
    std::transform(name.begin(), name.end(), name.begin(), towlower);
    const wchar_t* suffixes[] = {L".crdownload", L".part", L".partial", L".download", L".tmp"};
    for (auto suffix : suffixes) {
        size_t length = wcslen(suffix);
        if (name.size() >= length && name.compare(name.size() - length, length, suffix) == 0) return true;
    }
    return false;
}

bool file_signature(const fs::path& path, unsigned long long& size, unsigned long long& write_time) {
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
    write_time = t.QuadPart;
    return true;
}

static DWORD run_local(std::wstring command) {
    STARTUPINFOW startup{};
    PROCESS_INFORMATION process{};
    startup.cb = sizeof(startup);
    std::vector<wchar_t> mutable_command(command.begin(), command.end());
    mutable_command.push_back(L'\0');
    if (!CreateProcessW(nullptr, mutable_command.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW,
                        nullptr, app_dir().c_str(), &startup, &process)) {
        return GetLastError();
    }
    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD code = 0;
    GetExitCodeProcess(process.hProcess, &code);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return code;
}

static HANDLE active_user_token() {
    DWORD session = WTSGetActiveConsoleSessionId();
    if (session == 0xFFFFFFFF) return nullptr;
    HANDLE token = nullptr;
    if (!WTSQueryUserToken(session, &token)) return nullptr;
    HANDLE primary = nullptr;
    if (!DuplicateTokenEx(token, TOKEN_ALL_ACCESS, nullptr, SecurityImpersonation, TokenPrimary, &primary)) {
        primary = nullptr;
    }
    CloseHandle(token);
    return primary;
}

static DWORD run_as_user(std::wstring command) {
    HANDLE token = active_user_token();
    if (!token) return ERROR_NO_TOKEN;

    void* environment = nullptr;
    CreateEnvironmentBlock(&environment, token, FALSE);
    STARTUPINFOW startup{};
    PROCESS_INFORMATION process{};
    startup.cb = sizeof(startup);
    startup.lpDesktop = const_cast<LPWSTR>(L"winsta0\\default");
    std::vector<wchar_t> mutable_command(command.begin(), command.end());
    mutable_command.push_back(L'\0');
    BOOL ok = CreateProcessAsUserW(
        token, nullptr, mutable_command.data(), nullptr, nullptr, FALSE,
        CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW,
        environment, app_dir().c_str(), &startup, &process);
    if (environment) DestroyEnvironmentBlock(environment);
    CloseHandle(token);
    if (!ok) return GetLastError();

    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD code = 0;
    GetExitCodeProcess(process.hProcess, &code);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return code;
}

DWORD run_extractor(const fs::path& source, bool service_mode) {
    auto python = ini_value(L"runtime", L"python");
    if (python.empty()) return ERROR_FILE_NOT_FOUND;
    auto script = app_dir() / L"Auto_Unzip-Save-Open.py";
    std::wstring command = quote(python) + L" " + quote(script.wstring()) + L" process " + quote(source.wstring());
    if (service_mode) {
        DWORD code = run_as_user(command);
        if (code != ERROR_NO_TOKEN) return code;
    }
    return run_local(command);
}
}

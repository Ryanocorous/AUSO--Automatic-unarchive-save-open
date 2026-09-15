#pragma once
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <filesystem>
#include <string>

namespace auso {
namespace fs = std::filesystem;

extern fs::path g_app_dir;

fs::path app_dir();
std::wstring ini_value(const wchar_t* section, const wchar_t* key, const std::wstring& fallback = L"");
bool ini_bool(const wchar_t* section, const wchar_t* key, bool fallback = false);
std::wstring quote(const std::wstring& value);
bool temporary_name(const fs::path& path);
bool file_signature(const fs::path& path, unsigned long long& size, unsigned long long& write_time);
DWORD run_extractor(const fs::path& source, bool service_mode);
}

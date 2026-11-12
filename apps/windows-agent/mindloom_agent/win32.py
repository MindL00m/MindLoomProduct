"""The Win32 calls the driver needs: windows, processes, the foreground, and typed keys.

Windows only (imported by :mod:`driver`). Every function that takes or returns a handle
declares its types, so handles are not cut to 32 bits.
"""

from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes
from typing import Callable

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
version = ctypes.WinDLL("version", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

ULONG_PTR = ctypes.c_size_t
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
for _name in ("IsWindowVisible", "IsWindowEnabled", "IsIconic", "SetForegroundWindow", "BringWindowToTop"):
    getattr(user32, _name).argtypes = [wintypes.HWND]
    getattr(user32, _name).restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = wintypes.LONG
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.VkKeyScanW.argtypes = [wintypes.WCHAR]
user32.VkKeyScanW.restype = ctypes.c_short
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.GetTickCount.argtypes = []
kernel32.GetTickCount.restype = wintypes.DWORD
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.GetProcessId.argtypes = [wintypes.HANDLE]
kernel32.GetProcessId.restype = wintypes.DWORD
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
version.GetFileVersionInfoW.restype = wintypes.BOOL
version.VerQueryValueW.argtypes = [
    wintypes.LPCVOID, wintypes.LPCWSTR, ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.UINT)]
version.VerQueryValueW.restype = wintypes.BOOL

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
SW_SHOWNOACTIVATE = 4
SW_RESTORE = 9
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


# --- Windows and processes ------------------------------------------------------------------


def window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def top_level_windows() -> list[tuple[int, int, str]]:
    """Visible, titled top-level windows as (hwnd, pid, title), front to back."""

    found: list[tuple[int, int, str]] = []

    def visit(hwnd: int, _: int) -> bool:
        if user32.IsWindowVisible(hwnd) and not (user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW):
            title = window_title(hwnd)
            if title:
                found.append((int(hwnd), window_pid(hwnd), title))
        return True

    user32.EnumWindows(WNDENUMPROC(visit), 0)
    return found


def is_enabled(hwnd: int) -> bool:
    return bool(user32.IsWindowEnabled(hwnd))


def is_minimized(hwnd: int) -> bool:
    return bool(user32.IsIconic(hwnd))


def show_without_activating(hwnd: int) -> None:
    user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)


def foreground_window() -> int:
    return int(user32.GetForegroundWindow() or 0)


def foreground_pid() -> int:
    hwnd = foreground_window()
    return window_pid(hwnd) if hwnd else 0


def process_path(pid: int) -> str:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def process_alive(pid: int) -> bool:
    return bool(process_path(pid))


def file_names(path: str) -> list[str]:
    """The FileDescription and ProductName a program carries, e.g. "Microsoft Excel"."""

    size = version.GetFileVersionInfoSizeW(path, None)
    if not size:
        return []
    data = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(path, 0, size, data):
        return []
    pointer, length = wintypes.LPVOID(), wintypes.UINT()
    languages: list[tuple[int, int]] = []
    if version.VerQueryValueW(data, "\\VarFileInfo\\Translation", ctypes.byref(pointer), ctypes.byref(length)):
        words = ctypes.cast(pointer, ctypes.POINTER(wintypes.WORD * (length.value // 2))).contents
        languages = [(words[i], words[i + 1]) for i in range(0, len(words) - 1, 2)]
    languages.append((0x0409, 0x04B0))
    for language, codepage in languages:
        names = []
        for key in ("FileDescription", "ProductName"):
            block = f"\\StringFileInfo\\{language:04x}{codepage:04x}\\{key}"
            if version.VerQueryValueW(data, block, ctypes.byref(pointer), ctypes.byref(length)) and length.value:
                text = ctypes.wstring_at(pointer.value, length.value).rstrip("\0").strip()
                if text:
                    names.append(text)
        if names:
            return names
    return []


def process_names(pid: int) -> tuple[str, ...]:
    """What a process goes by: its program name without ".exe", then its description and product."""

    path = process_path(pid)
    if not path:
        return ()
    stem = os.path.splitext(os.path.basename(path))[0]
    names = [stem]
    try:
        names += file_names(path)
    except OSError:
        pass
    return tuple(dict.fromkeys(n for n in names if n))


# --- Starting a program ----------------------------------------------------------------------


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", wintypes.LPVOID), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE),
    ]


shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
shell32.ShellExecuteExW.restype = wintypes.BOOL

SEE_MASK_NOCLOSEPROCESS = 0x00000040
SEE_MASK_FLAG_NO_UI = 0x00000400


def start(target: str) -> tuple[bool, int]:
    """Open a Start menu shortcut or a program name. Returns (started, pid or 0)."""

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_FLAG_NO_UI
    info.lpVerb = "open"
    info.lpFile = target
    info.nShow = SW_SHOWNOACTIVATE
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        return False, 0
    pid = 0
    if info.hProcess:
        pid = int(kernel32.GetProcessId(info.hProcess))
        kernel32.CloseHandle(info.hProcess)
    return True, pid


# --- The foreground --------------------------------------------------------------------------


def bring_to_front(hwnd: int) -> bool:
    """Make ``hwnd`` the foreground window. Windows only lets a background program do this
    while its input is attached to the thread that has the foreground."""

    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    if foreground_window() == hwnd:
        return True
    current = foreground_window()
    this_thread = kernel32.GetCurrentThreadId()
    front_thread = user32.GetWindowThreadProcessId(current, None) if current else 0
    attached = bool(front_thread and front_thread != this_thread
                    and user32.AttachThreadInput(this_thread, front_thread, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(this_thread, front_thread, False)
    for _ in range(10):
        if foreground_window() == hwnd:
            return True
        time.sleep(0.05)
    return False


# --- Keys --------------------------------------------------------------------------------------


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
user32.GetLastInputInfo.restype = wintypes.BOOL

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_RETURN, VK_TAB = 0x0D, 0x09

# When this app last sent keys itself, so the person's own input can be told apart.
_own_input_tick = 0


def _send(events: list[tuple[int, int, int]]) -> bool:
    """Send (virtual key, scan code, flags) events in one call."""

    global _own_input_tick
    array = (INPUT * len(events))()
    for slot, (vk, scan, flags) in zip(array, events):
        slot.type = INPUT_KEYBOARD
        slot.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    sent = user32.SendInput(len(events), array, ctypes.sizeof(INPUT))
    _own_input_tick = int(kernel32.GetTickCount())
    return sent == len(events)


def _key_events(vk: int, extended: bool = False) -> list[tuple[int, int, int]]:
    scan = user32.MapVirtualKeyW(vk, 0)
    flags = KEYEVENTF_EXTENDEDKEY if extended else 0
    return [(vk, scan, flags), (vk, scan, flags | KEYEVENTF_KEYUP)]


def press_combo(modifier_vks: list[int], vk: int, extended: bool = False) -> bool:
    down = [(m, user32.MapVirtualKeyW(m, 0), 0) for m in modifier_vks]
    up = [(m, user32.MapVirtualKeyW(m, 0), KEYEVENTF_KEYUP) for m in reversed(modifier_vks)]
    return _send(down + _key_events(vk, extended) + up)


def layout_vk(char: str) -> int | None:
    """The key that types ``char`` on the current keyboard layout, when it needs no Shift."""

    code = user32.VkKeyScanW(char)
    if code == -1 or (code >> 8) & 0xFF:
        return None
    return code & 0xFF


def type_text(text: str, keep_going: Callable[[], bool]) -> tuple[bool, int]:
    """Type ``text`` at the keyboard focus. Returns (finished, characters typed)."""

    typed = 0
    for index, char in enumerate(text.replace("\r\n", "\n").replace("\r", "\n")):
        if index % 10 == 0 and not keep_going():
            return False, typed
        if char == "\n":
            events = _key_events(VK_RETURN)
        elif char == "\t":
            events = _key_events(VK_TAB)
        else:
            events = []
            encoded = char.encode("utf-16-le")
            for offset in range(0, len(encoded), 2):
                unit = int.from_bytes(encoded[offset:offset + 2], "little")
                events += [(0, unit, KEYEVENTF_UNICODE), (0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)]
        if not _send(events):
            return False, typed
        typed += 1
        time.sleep(0.008)
    return True, typed


def person_active(within_ms: int = 1500) -> bool:
    """The person used the keyboard or mouse just now (after this app's own last keys)."""

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return False
    now = int(kernel32.GetTickCount())
    last = int(info.dwTime)
    since_last = (now - last) & 0xFFFFFFFF
    after_own = (last - _own_input_tick) & 0xFFFFFFFF
    return since_last < within_ms and 50 < after_own < 0x80000000


def reset_input() -> None:
    """Count everything up to now as this app's doing."""

    global _own_input_tick
    _own_input_tick = int(kernel32.GetTickCount())


# --- The stop hot key --------------------------------------------------------------------------

MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
VK_OEM_PERIOD = 0xBE
WM_HOTKEY = 0x0312


def start_stop_hotkey(on_stop: Callable[[], None]) -> threading.Thread:
    """Ctrl+Alt+. anywhere calls ``on_stop``. Runs on its own thread for the life of the app."""

    def listen() -> None:
        if not user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_OEM_PERIOD):
            return  # another program has it: Stop on the overlay still works
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message == WM_HOTKEY:
                on_stop()

    thread = threading.Thread(target=listen, name="mindloom-stop-hotkey", daemon=True)
    thread.start()
    return thread

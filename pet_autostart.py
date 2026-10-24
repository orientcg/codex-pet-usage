"""Per-user, opt-in companion startup when the Codex desktop window opens."""
import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime
from functools import lru_cache
import json
import os
from pathlib import Path, PureWindowsPath
import subprocess
import sys

BASE = Path(__file__).resolve().parent
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_VALUE = 'CodexPetUsageWatcher'
MUTEX = r'Local\CodexPetUsageWatcher'
STOP_EVENT = r'Local\CodexPetUsageWatcherStop'
POLL_SECONDS = 1


def is_codex_image(image):
    path = PureWindowsPath(image)
    if path.name.lower() not in ('chatgpt.exe', 'codex.exe') or path.parent.name.lower() != 'app':
        return False
    return (any(part.lower().startswith('openai.codex_') for part in path.parts)
            or str(path.parent).lower().endswith(r'\openai\codex\app'))


class Windows:
    def __init__(self):
        if os.name != 'nt':
            raise OSError('Follow Codex startup is available on Windows only.')
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        bindings = [
            (self.kernel, 'CreateMutexW', [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            (self.kernel, 'OpenMutexW', [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            (self.kernel, 'ReleaseMutex', [wintypes.HANDLE], wintypes.BOOL),
            (self.kernel, 'CloseHandle', [wintypes.HANDLE], wintypes.BOOL),
            (self.kernel, 'CreateEventW', [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            (self.kernel, 'OpenEventW', [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            (self.kernel, 'SetEvent', [wintypes.HANDLE], wintypes.BOOL),
            (self.kernel, 'WaitForSingleObject', [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            (self.kernel, 'OpenProcess', [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            (self.kernel, 'QueryFullProcessImageNameW', [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            (self.user, 'EnumWindows', [self.callback, wintypes.LPARAM], wintypes.BOOL),
            (self.user, 'IsWindowVisible', [wintypes.HWND], wintypes.BOOL),
            (self.user, 'GetWindow', [wintypes.HWND, wintypes.UINT], wintypes.HWND),
            (self.user, 'GetWindowThreadProcessId', [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
        ]
        for library, name, arguments, result in bindings:
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, result
        self.window_style = getattr(self.user, 'GetWindowLongPtrW', self.user.GetWindowLongW)
        self.window_style.argtypes = [wintypes.HWND, ctypes.c_int]
        self.window_style.restype = ctypes.c_ssize_t

    def process_image(self, pid):
        process = self.kernel.OpenProcess(0x1000, False, pid)
        if not process:
            return ''
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(buffer))
            return buffer.value if self.kernel.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(length)) else ''
        finally:
            self.kernel.CloseHandle(process)


@lru_cache(maxsize=1)
def windows():
    return Windows()


def visible_codex_processes():
    api = windows()
    found, checked = set(), {}

    @api.callback
    def visit(hwnd, unused):
        # Ignore owned dialogs, hidden background windows and pet/tool windows.
        if (api.user.IsWindowVisible(hwnd) and not api.user.GetWindow(hwnd, 4)
                and not api.window_style(hwnd, -20) & 0x80):
            pid = wintypes.DWORD()
            api.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in checked:
                checked[pid.value] = is_codex_image(api.process_image(pid.value))
            if checked[pid.value]:
                found.add(pid.value)
        return True

    if not api.user.EnumWindows(visit, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return found


def log_error(error):
    try:
        folder = BASE/'work'
        folder.mkdir(exist_ok=True)
        with (folder/'autostart.log').open('a', encoding='utf-8') as stream:
            stream.write(f'{datetime.now().isoformat(timespec="seconds")} {error}\n')
    except OSError:
        pass


def quiet_python():
    candidate = Path(sys.executable).with_name('pythonw.exe')
    return str(candidate if candidate.is_file() else Path(sys.executable))


def spawn(script, *arguments):
    return subprocess.Popen([quiet_python(), str(BASE/script), *arguments], cwd=BASE,
                            creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)


def follow_codex(read_windows, launch, wait, report=log_error):
    previous = set()
    while True:
        try:
            current = set(read_windows())
        except OSError as error:
            # A failed scan is not a close/reopen event.
            report(error)
        else:
            opened = current-previous
            previous = current
            if opened:
                try:
                    launch()
                except OSError as error:
                    report(error)
        if wait(POLL_SECONDS):
            return


def watch():
    api = windows()
    mutex = api.kernel.CreateMutexW(None, True, MUTEX)
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        api.kernel.CloseHandle(mutex)
        return
    stop = None
    try:
        stop = api.kernel.CreateEventW(None, True, False, STOP_EVENT)
        if not stop:
            raise ctypes.WinError(ctypes.get_last_error())

        def wait(seconds):
            result = api.kernel.WaitForSingleObject(stop, round(seconds*1000))
            if result == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
            return result == 0

        follow_codex(visible_codex_processes, lambda: spawn('pet_usage.py'), wait)
    finally:
        if stop:
            api.kernel.CloseHandle(stop)
        api.kernel.ReleaseMutex(mutex)
        api.kernel.CloseHandle(mutex)


def stop_watcher():
    api = windows()
    event = api.kernel.OpenEventW(0x0002, False, STOP_EVENT)
    if not event:
        return
    try:
        if not api.kernel.SetEvent(event):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        api.kernel.CloseHandle(event)
    mutex = api.kernel.OpenMutexW(0x00100001, False, MUTEX)
    if mutex:
        try:
            result = api.kernel.WaitForSingleObject(mutex, 5000)
            if result in (0, 0x80):
                api.kernel.ReleaseMutex(mutex)
            else:
                raise OSError('The previous Codex Pet watcher did not stop.')
        finally:
            api.kernel.CloseHandle(mutex)


def registered_command():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, RUN_VALUE)[0]
    except FileNotFoundError:
        return None


def enable():
    import winreg
    command = f'"{quiet_python()}" "{BASE / "pet_autostart.py"}" --watch'
    if len(command) > 260:
        raise OSError('The project path is too long for Windows startup. Move it to a shorter folder.')
    previous = registered_command()
    stop_watcher()
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command)
        try:
            spawn('pet_autostart.py', '--watch')
        except OSError:
            if previous is None:
                winreg.DeleteValue(key, RUN_VALUE)
            else:
                winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, previous)
            raise


def disable():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, RUN_VALUE)
    except FileNotFoundError:
        pass
    stop_watcher()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    for name in ('enable', 'disable', 'watch', 'status'):
        action.add_argument('--'+name, action='store_true')
    options = parser.parse_args()
    if options.enable:
        enable()
    elif options.disable:
        disable()
    elif options.watch:
        watch()
    elif sys.stdout is not None:
        print(json.dumps({'enabled': registered_command() is not None}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        log_error(error)
        if sys.stderr is not None:
            print(str(error), file=sys.stderr)
        raise SystemExit(1)

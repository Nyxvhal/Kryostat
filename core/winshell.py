"""Перезапуск Проводника (explorer.exe).

Почему раньше не работало: команда `taskkill … & start explorer.exe` выполнялась через
cmd с перехваченным выводом. Проводник наследовал каналы вывода, и Kryostat ждал их
закрытия (до таймаута), а запущенный из процесса администратора Проводник иногда
не становился оболочкой рабочего стола. Теперь:
1. до завершения берём токен пользователя у текущего Проводника;
2. завершаем все explorer.exe этого сеанса;
3. ждём — Windows часто сама поднимает оболочку (AutoRestartShell);
4. если не подняла — запускаем explorer.exe с токеном пользователя (без прав
   администратора), без наследования каналов; запасной вариант — обычный запуск."""
import os
import subprocess
import sys
import time

import psutil

IS_WINDOWS = sys.platform == "win32"
EXPLORER = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "explorer.exe")


def _session_pids():
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    me = wintypes.DWORD()
    k32.ProcessIdToSessionId(os.getpid(), ctypes.byref(me))
    out = []
    for p in psutil.process_iter(["pid", "name"]):
        if (p.info["name"] or "").lower() != "explorer.exe":
            continue
        s = wintypes.DWORD()
        if k32.ProcessIdToSessionId(p.info["pid"], ctypes.byref(s)) and s.value == me.value:
            out.append(p.info["pid"])
    return out


def _user_token(pid):
    """Первичный токен пользователя из процесса Проводника (или None)."""
    import ctypes
    from ctypes import wintypes
    k32, adv = ctypes.windll.kernel32, ctypes.windll.advapi32
    H = wintypes.HANDLE
    k32.OpenProcess.restype = H
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    adv.OpenProcessToken.argtypes = [H, wintypes.DWORD, ctypes.POINTER(H)]
    adv.DuplicateTokenEx.argtypes = [H, wintypes.DWORD, wintypes.LPVOID, ctypes.c_int,
                                     ctypes.c_int, ctypes.POINTER(H)]
    k32.CloseHandle.argtypes = [H]
    hp = k32.OpenProcess(0x0400, False, pid)
    if not hp:
        return None
    tok, prim = H(), H()
    try:
        if not adv.OpenProcessToken(hp, 0x0002 | 0x0008 | 0x0001, ctypes.byref(tok)):
            return None
    finally:
        k32.CloseHandle(hp)
    ok = adv.DuplicateTokenEx(tok, 0x02000000, None, 2, 1, ctypes.byref(prim))
    k32.CloseHandle(tok)
    return prim if ok else None


def _start_with_token(prim):
    import ctypes
    from ctypes import wintypes
    k32, adv, uenv = ctypes.windll.kernel32, ctypes.windll.advapi32, ctypes.windll.userenv
    H = wintypes.HANDLE

    class SI(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                    ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                    ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                    ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                    ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
                    ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                    ("lpReserved2", wintypes.LPVOID), ("hStdInput", H),
                    ("hStdOutput", H), ("hStdError", H)]

    class PI(ctypes.Structure):
        _fields_ = [("hProcess", H), ("hThread", H), ("dwProcessId", wintypes.DWORD),
                    ("dwThreadId", wintypes.DWORD)]

    adv.CreateProcessWithTokenW.argtypes = [H, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPWSTR,
                                            wintypes.DWORD, wintypes.LPVOID, wintypes.LPCWSTR,
                                            ctypes.POINTER(SI), ctypes.POINTER(PI)]
    uenv.CreateEnvironmentBlock.argtypes = [ctypes.POINTER(wintypes.LPVOID), H, wintypes.BOOL]
    uenv.DestroyEnvironmentBlock.argtypes = [wintypes.LPVOID]
    k32.CloseHandle.argtypes = [H]
    si = SI()
    si.cb = ctypes.sizeof(SI)
    pi = PI()
    env = wintypes.LPVOID()
    have_env = bool(uenv.CreateEnvironmentBlock(ctypes.byref(env), prim, False))
    buf = ctypes.create_unicode_buffer(f'"{EXPLORER}"')
    ok = adv.CreateProcessWithTokenW(prim, 0, EXPLORER, buf, 0x00000400,   # UNICODE_ENVIRONMENT
                                     env if have_env else None,
                                     os.path.dirname(EXPLORER), ctypes.byref(si), ctypes.byref(pi))
    if have_env:
        uenv.DestroyEnvironmentBlock(env)
    if ok:
        k32.CloseHandle(pi.hThread)
        k32.CloseHandle(pi.hProcess)
    return bool(ok)


def _start_plain():
    DETACHED, NEW_GROUP = 0x00000008, 0x00000200
    subprocess.Popen([EXPLORER], cwd=os.path.dirname(EXPLORER), close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, creationflags=DETACHED | NEW_GROUP)


def restart_explorer(between=None):
    """Перезапустить Проводник. between() — что сделать, пока он закрыт (например,
    удалить кэш значков). Возвращает (ok, текст)."""
    if not IS_WINDOWS:
        return False, "Доступно только в Windows"
    from . import cmdlog
    cid = cmdlog.command_start("перезапуск Проводника (explorer.exe)")
    pids = _session_pids()
    token = None
    for pid in pids:
        token = _user_token(pid)
        if token:
            break
    for pid in pids:
        try:
            psutil.Process(pid).kill()
        except Exception:
            pass
    deadline = time.time() + 5
    while time.time() < deadline and _session_pids():
        time.sleep(0.15)
    if between:
        try:
            between()
        except Exception:
            pass
    # Windows может поднять оболочку сам — даём ей 2,5 с
    deadline = time.time() + 2.5
    while time.time() < deadline:
        if _session_pids():
            break
        time.sleep(0.2)
    how = "Windows перезапустил его сам"
    if not _session_pids():
        started = False
        if token:
            try:
                started = _start_with_token(token)
                how = "запущен с правами пользователя"
            except Exception:
                started = False
        if not started:
            try:
                _start_plain()
                how = "запущен заново"
                started = True
            except Exception as e:
                cmdlog.command_end(cid, 1, str(e))
                return False, f"Не удалось запустить Проводник: {e}"
    if token:
        try:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(token)
        except Exception:
            pass
    deadline = time.time() + 6
    while time.time() < deadline and not _session_pids():
        time.sleep(0.2)
    ok = bool(_session_pids())
    msg = f"Проводник перезапущен ({how})" if ok else "Проводник не запустился — Win+R → explorer"
    cmdlog.command_end(cid, 0 if ok else 1, msg)
    return ok, msg


def clear_icon_cache():
    """Сбросить кэш значков: удаляем файлы кэша, пока Проводник закрыт."""
    import glob
    local = os.environ.get("LOCALAPPDATA") or ""

    def wipe():
        files = [os.path.join(local, "IconCache.db")]
        files += glob.glob(os.path.join(local, "Microsoft", "Windows", "Explorer", "iconcache*"))
        files += glob.glob(os.path.join(local, "Microsoft", "Windows", "Explorer", "thumbcache*"))
        for f in files:
            try:
                os.remove(f)
            except OSError:
                pass
    try:
        subprocess.run(["ie4uinit.exe", "-show"], capture_output=True, timeout=20,
                       creationflags=0x08000000)
    except Exception:
        pass
    ok, msg = restart_explorer(between=wipe)
    return ok, ("Кэш значков сброшен. " + msg) if ok else msg

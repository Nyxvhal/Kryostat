"""Фоновые твики Windows через sc/реестр от имени администратора."""
from .admin import IS_WINDOWS, run
from . import config

# id, название, описание, команды применения, команды отката
TWEAKS = [
    ("diagtrack", "Телеметрия (DiagTrack)",
     "Служба «Функции для подключённых пользователей и телеметрия»",
     ["sc config DiagTrack start= disabled", "sc stop DiagTrack"],
     ["sc config DiagTrack start= auto", "sc start DiagTrack"]),
    ("dmwappushservice", "WAP Push (dmwappushservice)",
     "Маршрутизация сообщений WAP Push, часть телеметрии",
     ["sc config dmwappushservice start= disabled", "sc stop dmwappushservice"],
     ["sc config dmwappushservice start= demand"]),
    ("diagsvc", "Diagnostic Execution Service",
     "Выполнение диагностических сценариев",
     ["sc config diagsvc start= disabled", "sc stop diagsvc"],
     ["sc config diagsvc start= demand"]),
    ("wersvc", "Отчёты об ошибках (WerSvc)",
     "Отправка отчётов о сбоях в Microsoft",
     ["sc config WerSvc start= disabled", "sc stop WerSvc"],
     ["sc config WerSvc start= demand"]),
    ("sysmain", "SysMain / Superfetch",
     "Частая причина 100% загрузки диска на HDD",
     ["sc config SysMain start= disabled", "sc stop SysMain"],
     ["sc config SysMain start= auto", "sc start SysMain"]),
    ("wsearch", "Windows Search (WSearch)",
     "Индексация файлов — грузит диск и CPU",
     ["sc config WSearch start= disabled", "sc stop WSearch"],
     ["sc config WSearch start= delayed-auto", "sc start WSearch"]),
    ("retaildemo", "Retail Demo (RetailDemo)",
     "Демо-режим для магазинов, не нужен дома",
     ["sc config RetailDemo start= disabled", "sc stop RetailDemo"],
     ["sc config RetailDemo start= demand"]),
    ("mapsbroker", "Загрузчик карт (MapsBroker)",
     "Фоновая загрузка офлайн-карт",
     ["sc config MapsBroker start= disabled", "sc stop MapsBroker"],
     ["sc config MapsBroker start= delayed-auto"]),
    ("remoteregistry", "Удалённый реестр",
     "Удалённое изменение реестра — риск безопасности",
     ["sc config RemoteRegistry start= disabled", "sc stop RemoteRegistry"],
     ["sc config RemoteRegistry start= demand"]),
    ("fax", "Служба факсов (Fax)", "Практически никогда не используется",
     ["sc config Fax start= disabled", "sc stop Fax"],
     ["sc config Fax start= demand"]),
    ("xbox", "Службы Xbox",
     "XblAuthManager, XblGameSave, XboxNetApiSvc, XboxGipSvc",
     ["sc config XblAuthManager start= disabled", "sc stop XblAuthManager",
      "sc config XblGameSave start= disabled", "sc stop XblGameSave",
      "sc config XboxNetApiSvc start= disabled", "sc stop XboxNetApiSvc",
      "sc config XboxGipSvc start= disabled", "sc stop XboxGipSvc"],
     ["sc config XblAuthManager start= demand", "sc config XblGameSave start= demand",
      "sc config XboxNetApiSvc start= demand", "sc config XboxGipSvc start= demand"]),
    ("printnotify", "Уведомления принтера (PrintNotify)",
     "Расширенные уведомления печати",
     ["sc config PrintNotify start= disabled", "sc stop PrintNotify"],
     ["sc config PrintNotify start= demand"]),
    ("telemetry_policy", "Политика телеметрии = 0",
     "AllowTelemetry=0 в политиках сбора данных",
     [r'reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\DataCollection" '
      r'/v AllowTelemetry /t REG_DWORD /d 0 /f',
      r'reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\DataCollection" '
      r'/v DoNotShowFeedbackNotifications /t REG_DWORD /d 1 /f'],
     [r'reg delete "HKLM\SOFTWARE\Policies\Microsoft\Windows\DataCollection" '
      r'/v AllowTelemetry /f',
      r'reg delete "HKLM\SOFTWARE\Policies\Microsoft\Windows\DataCollection" '
      r'/v DoNotShowFeedbackNotifications /f']),
    ("ads", "Реклама и «советы» Windows",
     "Отключает подсказки, рекламу в Пуске и на экране блокировки",
     [r'reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager" '
      r'/v SubscribedContent-338388Enabled /t REG_DWORD /d 0 /f',
      r'reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager" '
      r'/v SystemPaneSuggestionsEnabled /t REG_DWORD /d 0 /f',
      r'reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager" '
      r'/v SilentInstalledAppsEnabled /t REG_DWORD /d 0 /f'],
     [r'reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager" '
      r'/v SubscribedContent-338388Enabled /t REG_DWORD /d 1 /f',
      r'reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager" '
      r'/v SystemPaneSuggestionsEnabled /t REG_DWORD /d 1 /f',
      r'reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager" '
      r'/v SilentInstalledAppsEnabled /t REG_DWORD /d 1 /f']),
    ("gamebar", "Game DVR / Game Bar",
     "Фоновая запись игр отнимает FPS",
     [r'reg add "HKCU\System\GameConfigStore" /v GameDVR_Enabled /t REG_DWORD /d 0 /f',
      r'reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\GameDVR" '
      r'/v AllowGameDVR /t REG_DWORD /d 0 /f'],
     [r'reg add "HKCU\System\GameConfigStore" /v GameDVR_Enabled /t REG_DWORD /d 1 /f',
      r'reg delete "HKLM\SOFTWARE\Policies\Microsoft\Windows\GameDVR" /v AllowGameDVR /f']),
    ("hibernate", "Гибернация (освобождает ГБ на диске)",
     "powercfg -h off — удаляет hiberfil.sys",
     ["powercfg -h off"], ["powercfg -h on"]),
]

BY_ID = {t[0]: t for t in TWEAKS}

CATEGORIES = {
    "Телеметрия и приватность": ["diagtrack", "dmwappushservice", "diagsvc", "wersvc",
                                 "telemetry_policy", "ads", "remoteregistry"],
    "Производительность": ["sysmain", "wsearch", "gamebar", "hibernate"],
    "Лишние службы": ["retaildemo", "mapsbroker", "fax", "xbox", "printnotify"],
}

# Безопасный набор «по умолчанию» — не ломает повседневную работу
RECOMMENDED = ["diagtrack", "dmwappushservice", "diagsvc", "wersvc", "retaildemo",
               "mapsbroker", "fax", "printnotify", "telemetry_policy", "ads", "gamebar"]

RISKY = {"wsearch": "Перестанет работать поиск по файлам в Проводнике и в меню Пуск.",
         "sysmain": "На SSD прироста почти нет; на HDD программы могут запускаться медленнее.",
         "hibernate": "Пропадёт гибернация и быстрый запуск Windows.",
         "xbox": "Перестанут работать Game Pass и сохранения Xbox в облаке."}


def is_applied(tid: str) -> bool:
    return tid in (config.load().get("tweaks_applied") or [])


# Коды, которые не считаются ошибкой: 1056 — служба уже запущена, 1060 — службы нет в этой
# редакции Windows, 1062 — служба не запущена. Для reg delete код 1 = значения уже нет.
_OK_SC = {0, 1056, 1060, 1062}


def _cmd_ok(cmd: str, rc: int, enable: bool) -> bool:
    if rc == 0:
        return True
    head = cmd.strip().lower()
    if head.startswith("sc "):
        return rc in _OK_SC
    if head.startswith("reg delete"):
        return rc == 1 and not enable
    if head.startswith("schtasks"):
        return rc == 1        # задачи нет в этой сборке Windows
    return False


def apply(tid: str, enable: bool = True):
    t = BY_ID.get(tid)
    if not t:
        return False, "Неизвестный твик"
    if not IS_WINDOWS:
        return False, "Только Windows"
    cmds = t[3] if enable else t[4]
    results, failed = [], 0
    for c in cmds:
        rc, out = run(c)
        ok = _cmd_ok(c, rc, enable)
        failed += 0 if ok else 1
        results.append(f"{'✓' if ok else '✗'} {c}" + ("" if ok else f" -> код {rc}: {out[:120]}"))
        config.log(f"tweak {tid}: {c} rc={rc} {out[:200]}")
    if failed == len(cmds):
        # ни одна команда не сработала — состояние не меняем
        return False, "\n".join(results)

    def _mut(cfg):
        lst = set(cfg.get("tweaks_applied") or [])
        if enable:
            lst.add(tid)
        else:
            lst.discard(tid)
        cfg["tweaks_applied"] = sorted(lst)
    config.update(_mut)
    return failed == 0, "\n".join(results)


def apply_saved():
    """Повторно применить все включённые твики (на старте, в фоне)."""
    cfg = config.load()
    if not cfg.get("auto_apply_tweaks"):
        return 0
    n = 0
    for tid in cfg.get("tweaks_applied") or []:
        if tid in BY_ID:
            apply(tid, True)
            n += 1
    return n


# ---- дополнительные оптимизации ----
from .tweaks_extra import (EXTRA, EXTRA_CATEGORIES, EXTRA_RECOMMENDED, EXTRA_RISKY,  # noqa: E402
                           NEEDS_EXPLORER, NEEDS_REBOOT)

TWEAKS.extend(t for t in EXTRA if t[0] not in BY_ID)
BY_ID.update({t[0]: t for t in EXTRA})
CATEGORIES["Лишние службы"] = CATEGORIES.get("Лишние службы", [])
for _cat, _ids in EXTRA_CATEGORIES.items():
    CATEGORIES.setdefault(_cat, [])
    CATEGORIES[_cat] += [i for i in _ids if i not in CATEGORIES[_cat]]
RECOMMENDED += [i for i in EXTRA_RECOMMENDED if i not in RECOMMENDED]
RISKY.update(EXTRA_RISKY)


def restart_explorer():
    """Перезапуск Проводника — применяет твики интерфейса без перезагрузки."""
    from .winshell import restart_explorer as _restart
    return _restart()


# ---- разовые действия: очистка и обслуживание ----
ACTIONS = [
    ("temp", "Очистить временные файлы", "%TEMP% и C:\\Windows\\Temp",
     'del /f /s /q "%TEMP%\\*" & for /d %d in ("%TEMP%\\*") do rd /s /q "%d" & '
     'del /f /s /q "%SystemRoot%\\Temp\\*"'),
    ("prefetch", "Очистить Prefetch", "Кэш предзагрузки (пересоздаётся сам)",
     'del /f /q "%SystemRoot%\\Prefetch\\*"'),
    ("wu_cache", "Очистить кэш обновлений", "SoftwareDistribution\\Download",
     'net stop wuauserv & net stop bits & del /f /s /q "%SystemRoot%\\SoftwareDistribution\\Download\\*" '
     '& net start bits & net start wuauserv'),
    ("recycle", "Очистить корзину", "На всех дисках",
     'powershell -NoProfile -Command "Clear-RecycleBin -Force -ErrorAction SilentlyContinue"'),
    ("dns", "Сбросить DNS-кэш", "ipconfig /flushdns", "ipconfig /flushdns"),
    ("net_reset", "Сброс сети (Winsock + TCP/IP)", "Помогает при проблемах с интернетом; нужна перезагрузка",
     "netsh winsock reset & netsh int ip reset"),
    ("sfc", "Проверка системных файлов (SFC)", "sfc /scannow — 5–15 минут", "sfc /scannow"),
    ("dism", "Восстановление образа Windows (DISM)", "DISM /RestoreHealth — до 30 минут",
     "DISM /Online /Cleanup-Image /RestoreHealth"),
    ("component_cleanup", "Очистка хранилища компонентов", "Удаляет старые версии обновлений (WinSxS)",
     "DISM /Online /Cleanup-Image /StartComponentCleanup"),
    ("trim", "Оптимизировать диски (TRIM / дефрагментация)", "Для SSD — TRIM, для HDD — дефрагментация",
     "defrag /C /O"),
    ("restore_point", "Создать точку восстановления", "Рекомендуется перед твиками",
     'powershell -NoProfile -Command "Enable-ComputerRestore -Drive $env:SystemDrive; '
     'Checkpoint-Computer -Description Kryostat -RestorePointType MODIFY_SETTINGS"'),
    ("icon_cache", "Сбросить кэш значков", "Если значки отображаются неправильно",
     'ie4uinit.exe -show & taskkill /f /im explorer.exe & del /f /q /a "%LOCALAPPDATA%\\IconCache.db" '
     '& del /f /q /a "%LOCALAPPDATA%\\Microsoft\\Windows\\Explorer\\iconcache*" & start explorer.exe'),
    ("explorer", "Перезапустить Проводник", "Применяет изменения интерфейса",
     "taskkill /f /im explorer.exe & start explorer.exe"),
    ("ultimate", "Схема «Максимальная производительность»", "Добавляет скрытую схему питания и включает её",
     "powercfg -duplicatescheme e9a42b02-d5df-448d-aa00-03f14749eb61"),
]
ACTIONS_BY_ID = {a[0]: a for a in ACTIONS}


def run_action(aid):
    a = ACTIONS_BY_ID[aid]
    if aid == "explorer":
        return restart_explorer()
    if aid == "icon_cache":
        from .winshell import clear_icon_cache
        return clear_icon_cache()
    timeout = 3600 if aid in ("sfc", "dism", "component_cleanup", "trim") else 300
    rc, out = run(a[3], timeout=timeout)
    ok = rc == 0 or aid in ("temp", "prefetch", "recycle", "icon_cache", "explorer")
    return ok, out[-1500:] or "Готово"

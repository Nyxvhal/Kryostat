"""Ежедневный автоматический перенос обновлений Windows на +N дней."""
import datetime
from .admin import IS_WINDOWS, is_admin, run
from . import config

WU_POLICY = r"HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate"
WU_UX = r"HKLM\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings"
MAX_DAYS = 35  # Windows не позволяет приостановить больше, чем на 35 дней


def _iso(dt: datetime.datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _query(key: str, value: str):
    rc, out = run(["reg", "query", key, "/v", value])
    if rc != 0:
        return None
    for line in out.splitlines():
        if value in line:
            return line.split()[-1]
    return None


def status() -> dict:
    cfg = config.load()["update_defer"]
    paused_until = (_query(WU_UX, "PauseUpdatesExpiryTime")
                    or _query(WU_POLICY, "PauseQualityUpdatesEndTime"))
    return {"enabled": cfg.get("enabled"), "last_run": cfg.get("last_run"),
            "paused_until": paused_until, "days_ahead": cfg.get("days_ahead", 1)}


def _reg_add(key, name, kind, data):
    return run(["reg", "add", key, "/v", name, "/t", kind, "/d", str(data), "/f"])


def extend(days: int | None = None):
    """Сдвинуть окно паузы обновлений вперёд. Вызывается раз в сутки."""
    if not IS_WINDOWS:
        return False, "Только Windows"
    cfg = config.load()
    days = max(1, min(int(days or cfg["update_defer"].get("days_ahead", 1)), MAX_DAYS))
    now = _utcnow()
    end = now + datetime.timedelta(days=days)
    s, e = _iso(now), _iso(end)
    # Политики (GPO) и параметры, которые читает приложение «Параметры → Центр обновления»
    values = [
        (WU_POLICY, "PauseFeatureUpdatesStartTime", "REG_SZ", s),
        (WU_POLICY, "PauseFeatureUpdatesEndTime", "REG_SZ", e),
        (WU_POLICY, "PauseQualityUpdatesStartTime", "REG_SZ", s),
        (WU_POLICY, "PauseQualityUpdatesEndTime", "REG_SZ", e),
        (WU_UX, "PauseFeatureUpdatesStartTime", "REG_SZ", s),
        (WU_UX, "PauseFeatureUpdatesEndTime", "REG_SZ", e),
        (WU_UX, "PauseQualityUpdatesStartTime", "REG_SZ", s),
        (WU_UX, "PauseQualityUpdatesEndTime", "REG_SZ", e),
        (WU_UX, "PauseUpdatesStartTime", "REG_SZ", s),
        (WU_UX, "PauseUpdatesExpiryTime", "REG_SZ", e),
        (WU_UX, "FlightSettingsMaxPauseDays", "REG_DWORD", MAX_DAYS),
    ]
    errs = []
    for key, name, kind, data in values:
        rc, out = _reg_add(key, name, kind, data)
        if rc != 0:
            errs.append(f"{name}: {out}")
    # «Последний запуск» запоминаем только при успехе — иначе после ошибки повтор был бы только завтра
    if not errs:
        def _mut(c):
            c["update_defer"]["last_run"] = datetime.date.today().isoformat()
        config.update(_mut)
    config.log(f"Обновления отложены до {e} (ошибок: {len(errs)})")
    if errs:
        return False, "\n".join(errs[:3])
    return True, f"Обновления отложены до {end:%d.%m.%Y %H:%M} UTC"


def reset():
    """Снять паузу — Windows снова обновляется как обычно."""
    names = ("PauseFeatureUpdatesStartTime", "PauseFeatureUpdatesEndTime",
             "PauseQualityUpdatesStartTime", "PauseQualityUpdatesEndTime")
    for n in names:
        run(["reg", "delete", WU_POLICY, "/v", n, "/f"])
        run(["reg", "delete", WU_UX, "/v", n, "/f"])
    for n in ("PauseUpdatesStartTime", "PauseUpdatesExpiryTime"):
        run(["reg", "delete", WU_UX, "/v", n, "/f"])
    # значения, которые 1.0/1.1 оставляли в политике, тоже убираем
    for n in ("DeferQualityUpdates", "DeferQualityUpdatesPeriodInDays"):
        run(["reg", "delete", WU_POLICY, "/v", n, "/f"])

    def _mut(c):
        c["update_defer"]["enabled"] = False
    config.update(_mut)
    return True, "Пауза обновлений снята"


def tick():
    """Ежедневная проверка: если сегодня ещё не продлевали — продлить."""
    cfg = config.load()
    d = cfg["update_defer"]
    if not d.get("enabled") or not is_admin():
        return False
    today = datetime.date.today().isoformat()
    if d.get("last_run") == today:
        return False
    ok, _ = extend()
    return ok

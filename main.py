"""Kryostat — точка входа."""
import os
import sys
import threading
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import admin, autorun, config  # noqa: E402


def _install_excepthooks():
    """Приложение «оконное» (без консоли) — необработанные ошибки раньше пропадали бесследно.
    Теперь они попадают в журнал."""
    def hook(exc_type, exc, tb):
        config.log("Необработанная ошибка:\n" + "".join(traceback.format_exception(exc_type, exc, tb)))
    sys.excepthook = hook

    def thook(args):
        hook(args.exc_type, args.exc_value, args.exc_traceback)
    threading.excepthook = thook


def main():
    import multiprocessing
    multiprocessing.freeze_support()        # дочерний процесс сбора данных в сборке PyInstaller
    if "--shell-host" in sys.argv:          # вкладка терминала от имени администратора
        from core import shellproc
        shellproc.shell_host_main(sys.argv)
        return 0
    args = {a.lower() for a in sys.argv[1:]}
    boot = "--boot" in args
    _install_excepthooks()

    # служебные режимы для установщика: без окна
    if "--register-task" in args:
        ok, _ = autorun.register_task("--minimized" in args)
        return 0 if ok else 1
    if "--unregister" in args:
        autorun.unregister_all()
        return 0

    # просим повышение прав один раз
    if admin.IS_WINDOWS and not admin.is_admin() and "--no-elevate" not in args:
        extra = ["--no-elevate"]
        if admin.relaunch_as_admin(extra):
            return 0

    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator, Qt
    from PySide6.QtGui import QFont, QIcon
    from PySide6.QtWidgets import QApplication
    from core import i18n

    if admin.IS_WINDOWS:
        try:   # отдельная группа в панели задач и корректная иконка
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Kryostat.App")
        except Exception:
            pass

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)

    # язык интерфейса: как в системе (или выбранный в настройках); если перевода
    # для языка системы нет — английский. Перехват текстов ставится ДО импорта страниц.
    lang = i18n.init()
    from app import i18n_qt
    i18n_qt.install()
    qt_tr = QTranslator(app)       # стандартные кнопки Qt (Yes/No/Cancel) на том же языке
    try:
        if qt_tr.load(QLocale(lang), "qtbase", "_",
                      QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
            app.installTranslator(qt_tr)
    except Exception:
        pass

    from app.instance import SingleInstance
    from app.main_window import MainWindow, asset
    from app.theme import UI_FONT, qss

    # только один экземпляр (задача планировщика + запись Run больше не плодят копии)
    instance = SingleInstance()
    wait = 6.0 if "--restarted" in args else 0.0
    if not instance.acquire(wait):
        instance.notify_existing()
        return 0

    app.setApplicationName("Kryostat")
    app.setApplicationDisplayName("Kryostat")
    app.setOrganizationName("Kryostat")
    app.setApplicationVersion(config.VERSION)
    app.setWindowIcon(QIcon(asset("kryostat.ico")))
    app.setQuitOnLastWindowClosed(False)
    f = QFont()
    f.setFamilies(UI_FONT)
    f.setPixelSize(13)
    f.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    app.setFont(f)
    app.setStyleSheet(qss(os.path.dirname(asset("kryostat.ico"))))

    win = MainWindow(boot_mode=boot)
    win.release_instance = instance.release
    win.reacquire_instance = lambda: instance.acquire(0)
    instance.activated.connect(win.show_normal)

    cfg = config.load()
    if "--minimized" in args or (boot and cfg.get("start_minimized", True)):
        win.hide()
    else:
        win.show()
    config.log(f"Kryostat {config.VERSION} запущен · язык {lang}" + (" (автозапуск)" if boot else "")
               + (" · администратор" if admin.is_admin() else " · без прав администратора"))
    code = app.exec()
    instance.release()
    config.log("Kryostat закрыт")
    # фоновые потоки (сканирование автозапуска, schtasks) могут ещё работать —
    # без жёсткого выхода Qt ругается «QThread: Destroyed while thread is still running»
    sys.stdout.flush()
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main())

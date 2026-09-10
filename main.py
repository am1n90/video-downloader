"""Video Downloader — точка входа.

Использование:
    python main.py           # обычный запуск
    python main.py -selftest # сборочный тест: запуск и авто-выход
"""

import sys

from gui import run


def _selftest_requested():
    return any(arg.lower() in ("-selftest", "--selftest", "/selftest")
               for arg in sys.argv[1:])


if __name__ == "__main__":
    if _selftest_requested():
        # Режим для build.bat (шаг тестового запуска): окно открывается,
        # через 8 секунд закрывается само. Успех = процесс жил и завершился
        # с кодом 0; закрывать вручную не нужно (раньше start /wait висел).
        from PySide6.QtCore import QTimer

        from PySide6.QtWidgets import QApplication

        app = QApplication(sys.argv)
        import gui as _gui
        import config as _config

        window = _gui.MainWindow(_config.load())
        window.show()

        result = {"ok": False}

        def _finish():
            result["ok"] = True
            window.close()
            app.quit()

        QTimer.singleShot(8000, _finish)
        app.exec()
        sys.exit(0 if result["ok"] else 1)

    run()

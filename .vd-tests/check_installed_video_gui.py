"""Живая проверка режима Video Downloader в УСТАНОВЛЕННОЙ копии через окно
(релиз 1.1.0: первый релиз с переключателем режимов).

check_installed_yt / _fragment проверяют вшитые yt-dlp и ffmpeg, но не
окно. Здесь — настоящее окно установленного exe через UI Automation:

    1. режим Video Downloader открывается на странице «Загрузка»;
    2. ссылка -> «Анализ» -> «Загрузить» -> карточка «Готово», файл на
       диске, запись в истории settings.json;
    3. «Библиотека» режима показывает скачанное;
    4. переключатель режимов: Torrent и обратно, каждый режим
       возвращается на свою последнюю страницу.

Ролик — Big Buck Bunny (Blender), режим «аудио». Нужен интернет.
settings.json установленной копии сохраняется до проверки и
возвращается после неё побайтно, загрузки — во временной папке.

    build-venv\\Scripts\\python.exe .vd-tests\\check_installed_video_gui.py
"""
import glob
import hashlib
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_installed_torrent as ct                         # noqa: E402
import installed_ui as ui                                    # noqa: E402

# Тестовый 10-секундный ролик yt-dlp (BaW-jenozKc) на YouTube теперь
# недоступен — берём Big Buck Bunny (открытый фильм Blender) в режиме
# «аудио»: файл небольшой, заодно конвертация вшитым ffmpeg
VIDEO = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
BASE = os.path.join(tempfile.gettempdir(), "vd-110-video")
DL = os.path.join(BASE, "downloads")
SNAP = os.path.join(BASE, "shots")
VIDEO_NAV = ("Загрузка", "Библиотека")                       # сверху вниз

check, report, element, center, grab = (ct.check, ct.report, ct.element,
                                        ct.center, ct.grab)


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def title_is(pid, name, timeout=15):
    return ui.wait_element(
        pid, lambda e: e.get("cls") == "TitleLabel"
        and e.get("name") == name and e.get("w"), timeout) is not None


def open_nav(pid, name):
    """Как ct.open_nav, но для пунктов режима Video Downloader."""
    items = sorted((e for e in ui.elements(pid)
                    if e.get("cls") == "NavigationTreeItem" and e.get("w")),
                   key=lambda e: e["y"])
    top = items[:-1]
    if len(top) != len(VIDEO_NAV):
        print(f"  навигация: видно {len(top)} пункта сверху")
        return False
    ui.click(pid, *center(top[VIDEO_NAV.index(name)]))
    return title_is(pid, name)


def click_segment(pid, name):
    e = element(pid, name, "ControlType.Button")
    if e is None:
        return False
    ui.click(pid, *center(e))
    time.sleep(1.0)
    return True


def main():
    print("== video gui: режим Video Downloader в окне установленной копии ==")
    backup = ui.SETTINGS + time.strftime(".bak-%Y%m%d-%H%M%S")
    shutil.copy2(ui.SETTINGS, backup)
    original = sha(ui.SETTINGS)
    print(f"  backup: {backup}")
    shutil.rmtree(BASE, ignore_errors=True)
    os.makedirs(DL)
    os.makedirs(SNAP)
    history_before = len(ui.read_settings().get("history") or [])
    ui.patch_settings(app_mode="video", default_folder=DL,
                      default_mode="audio", check_updates=False)
    for pid, _parent, _cmd in ui.processes("VideoDownloader.exe"):
        ui.kill(pid)
    proc = ui.launch()
    hwnd = ui.main_window(proc.pid, 60)
    try:
        if not check("окно установленной копии открылось", hwnd is not None):
            return report()
        ui.focus(hwnd)
        check("режим Video Downloader открылся на «Загрузке»",
              title_is(proc.pid, "Загрузка", 30))

        # 2. анализ и загрузка
        ui.set_value(proc.pid, VIDEO, ctl_type="ControlType.Edit")
        ui.invoke(proc.pid, "Анализ")
        ready = ui.wait_element(
            proc.pid, lambda e: e.get("name") == "Загрузить"
            and e.get("enabled") and e.get("w"), 120)
        check("анализ прошёл, «Загрузить» активна", ready is not None)
        grab(hwnd, os.path.join(SNAP, "video-analyzed.png"))
        if ready is None:
            return report()
        ui.click(proc.pid, *center(ready))
        done = ct.wait_card(proc.pid, "Готово", 240)
        check("карточка загрузки дошла до «Готово»", bool(done), done)
        files = [p for p in glob.glob(os.path.join(DL, "*"))
                 if os.path.isfile(p) and not p.endswith((".part", ".ytdl"))]
        check("файл скачан в выбранную папку",
              len(files) == 1 and os.path.getsize(files[0]) > 0,
              ", ".join(f"{os.path.basename(p)} {os.path.getsize(p)} Б"
                        for p in files))
        grab(hwnd, os.path.join(SNAP, "video-done.png"))
        history = ui.read_settings().get("history") or []
        check("запись в истории settings.json",
              len(history) == history_before + 1 and files
              and os.path.normcase(history[0].get("path") or "")
              == os.path.normcase(files[0]),
              f"{history_before} -> {len(history)}")

        # 3. Библиотека режима
        check("«Библиотека» режима Video Downloader открылась",
              open_nav(proc.pid, "Библиотека"))
        stem = os.path.splitext(os.path.basename(files[0]))[0] if files else "?"
        row = ui.wait_element(
            proc.pid, lambda e: (e.get("name") or "") and
            (e["name"] in stem or stem.startswith(e["name"][:20]))
            and e.get("cls") == "StrongBodyLabel", 15)
        check("скачанное видео есть в Библиотеке", row is not None,
              (row or {}).get("name", ""))
        grab(hwnd, os.path.join(SNAP, "video-library.png"))

        # 4. переключатель режимов и последние страницы
        check("переключение в Torrent -> «Торренты»",
              click_segment(proc.pid, "Torrent")
              and title_is(proc.pid, "Торренты"))
        check("обратно в Video Downloader -> последняя страница «Библиотека»",
              click_segment(proc.pid, "Video Downloader")
              and title_is(proc.pid, "Библиотека"))
        secs = ct.close_app(proc, hwnd)
        check("окно закрылось быстрее 1.5 с", secs < 1.5, f"{secs:.2f} с")
    finally:
        for pid, _parent, _cmd in ui.processes("VideoDownloader.exe"):
            ui.kill(pid)
        shutil.copy2(backup, ui.SETTINGS)
        check("settings.json возвращён побайтно", sha(ui.SETTINGS) == original)
        shutil.rmtree(DL, ignore_errors=True)
        print(f"  снимки: {SNAP}")
    return report()


if __name__ == "__main__":
    sys.exit(main())

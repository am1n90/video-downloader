"""Проверки УСТАНОВЛЕННОЙ копии — сессия 2.4, часть B (офисная машина).

Часть A шла разовыми скриптами; здесь собраны шаги, оставшиеся на
установленной копии, каждый отдельной командой (приложение запускается
и закрывается внутри шага — состояние между шагами не делится):

    nometa   — закрытие окна с раздачей БЕЗ метаданных (находка 42:
               было 3.28 с, ожидаем < 1 с) + контроль: fastresume
               магнита всё равно записан;
    dialog   — добавление .torrent через СИСТЕМНЫЙ диалог «Выберите
               .torrent» (настоящее окно выбора файла, ввод пути с
               клавиатуры);
    click    — выбор серии НАСТОЯЩИМ кликом мыши в «Что смотреть»
               (до сих пор этот выбор звали методом select());
    subs     — что делает VLC с субтитрами по http (только запись
               наблюдения, правило «та же папка» не чиним).

Сид поднимается и гасится внутри шагов dialog/click/subs
(`seed_local_tracker.py`, трекер 127.0.0.1:7788, интернет не нужен).
Настройки установленной копии перед первым шагом сохраняются в
settings.json.bak-<дата-время>, `cleanup` возвращает их обратно.
"""
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import installed_ui as ui                                    # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VENV_PY = os.path.join(ROOT, "build-venv", "Scripts", "python.exe")
SEEDER = os.path.join(HERE, "seed_local_tracker.py")
VLC = r"C:\Program Files\VideoLAN\VLC\vlc.exe"

BASE = os.path.join(tempfile.gettempdir(), "vd-24b")
DL = os.path.join(BASE, "downloads")
SEED = os.path.join(BASE, "seed")
SNAP = os.path.join(BASE, "shots")
STATE = os.path.join(BASE, "state.json")
RESUME = os.path.join(ui.TORRENT_DATA, "resume")

VK_RETURN = 0x0D
DEAD_MAGNET = "magnet:?xt=urn:btih:" + "7a1b" * 10        # 40 hex, ничей

_checks = []


def check(name, ok, detail=""):
    _checks.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))
    return ok


def report():
    bad = [n for n, ok in _checks if not ok]
    print(f"\n  ИТОГ: {len(_checks) - len(bad)} PASS, {len(bad)} FAIL")
    for n in bad:
        print(f"        FAIL: {n}")
    return 1 if bad else 0


# ------------------------------------------------------------ настройки

def state():
    try:
        with open(STATE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(**changes):
    data = state()
    data.update(changes)
    os.makedirs(BASE, exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def ensure_backup():
    """Резервная копия settings.json — ровно одна на всю часть B."""
    st = state()
    if st.get("backup") and os.path.isfile(st["backup"]):
        return st["backup"]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = ui.SETTINGS + f".bak-{stamp}"
    shutil.copy2(ui.SETTINGS, dst)
    save_state(backup=dst)
    print(f"  backup: {dst}")
    return dst


def prepare(**extra):
    """Установленную копию — в режим Torrent со своей папкой загрузок."""
    ensure_backup()
    os.makedirs(DL, exist_ok=True)
    os.makedirs(SNAP, exist_ok=True)
    changes = {"app_mode": "torrent", "torrent_folder": DL}
    changes.update(extra)
    ui.patch_settings(**changes)


# ------------------------------------------------------------ сид

def seed_start(limit_kb=1500):
    os.makedirs(SEED, exist_ok=True)
    stop = os.path.join(SEED, "stop")
    if os.path.exists(stop):
        os.remove(stop)
    shutil.copy2(os.path.join(ROOT, "dist", "VideoDownloader", "ffmpeg.exe"),
                 os.path.join(SEED, "ffmpeg.exe"))
    proc = subprocess.Popen([VENV_PY, SEEDER, SEED, str(limit_kb)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    info = os.path.join(SEED, "seed.json")
    end = time.monotonic() + 180
    while time.monotonic() < end:
        if os.path.isfile(info):
            time.sleep(0.5)
            with open(info, "r", encoding="utf-8") as f:
                return proc, json.load(f)
        if proc.poll() is not None:
            out = proc.stdout.read().decode("utf-8", "replace")
            raise RuntimeError(f"сид не поднялся:\n{out}")
        time.sleep(1.0)
    raise RuntimeError("сид не написал seed.json за 180 с")


def seed_stop(proc):
    open(os.path.join(SEED, "stop"), "w").close()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()


# ------------------------------------------------------------ окно

def start_app(timeout=60):
    # Оставшаяся от прошлого шага копия держит мьютекс single_instance:
    # новый экземпляр молча передаёт ей «покажись» и выходит, а шаг ждёт
    # окно, которого не будет
    for pid, _parent, _cmd in ui.processes("VideoDownloader.exe"):
        ui.kill(pid)
    proc = ui.launch()
    hwnd = ui.main_window(proc.pid, timeout)
    if hwnd is None:
        ui.kill(proc.pid)
        raise RuntimeError("окно установленной копии не появилось")
    ui.wait_element(proc.pid, lambda e: e.get("name") == "Добавить", 30)
    return proc, hwnd


def close_app(proc, hwnd, timeout=30):
    """Закрыть окно и вернуть, сколько секунд процесс жил после этого."""
    t0 = time.monotonic()
    ui.close_window(hwnd)
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        ui.kill(proc.pid)
        return float("inf")
    return time.monotonic() - t0


def card_text(pid, must_have):
    for e in ui.elements(pid):
        name = e.get("name") or ""
        if must_have in name:
            return name
    return ""


def wait_card(pid, must_have, timeout):
    e = ui.wait_element(pid, lambda x: must_have in (x.get("name") or ""),
                        timeout)
    return (e or {}).get("name", "")


def add_magnet(pid, uri):
    ui.set_value(pid, uri, ctl_type="ControlType.Edit")
    ui.invoke(pid, "Добавить")


def resume_files():
    try:
        return sorted(os.listdir(RESUME))
    except OSError:
        return []


# ------------------------------------------------------------ шаг nometa

def step_nometa(runs=3):
    """Находка 42 на установленной копии: раздача БЕЗ метаданных не
    должна держать закрытие окна.

    Голый порог «меньше секунды» тут не годится: на разных машинах своя
    цена закрытия (домашняя — 0.65 с с готовой раздачей, офисная около
    секунды). Сравниваем с ПУСТЫМ прогоном на той же машине и в тех же
    условиях — именно эту разницу и создавал дефект (3.28 с против
    0.65 с в части A). Прогоны чередуются, папка данных чистится, иначе
    магнит восстанавливается из fastresume и «пустой» прогон не пустой.
    """
    print("== nometa: закрытие окна с раздачей БЕЗ метаданных ==")
    prepare()
    empty, magnet = [], []
    resume_seen = False
    for i in range(runs):
        for kind in ("пусто", "магнит"):
            shutil.rmtree(ui.TORRENT_DATA, ignore_errors=True)
            proc, hwnd = start_app()
            if kind == "магнит":
                add_magnet(proc.pid, DEAD_MAGNET)
                label = wait_card(proc.pid, "Получение сведений о раздаче", 30)
                if i == 0:
                    check("карточка магнита без метаданных появилась",
                          bool(label), label)
                    grab(hwnd, os.path.join(SNAP, "nometa-before-close.png"))
            time.sleep(3.0)
            secs = close_app(proc, hwnd)
            (magnet if kind == "магнит" else empty).append(secs)
            if kind == "магнит" and resume_files():
                resume_seen = True
            print(f"  {kind:7} {secs:.2f} с", flush=True)
    em, mm = statistics.median(empty), statistics.median(magnet)
    print(f"  медианы: пусто {em:.2f} с, магнит {mm:.2f} с "
          f"(часть A: 0.65 с с метаданными против 3.28 с без)")
    check("раздача без метаданных не добавляет к закрытию больше секунды",
          mm - em < 1.0, f"разница {mm - em:.2f} с")
    check("fastresume магнита записан (сохранение не потеряно)", resume_seen)
    shutil.rmtree(ui.TORRENT_DATA, ignore_errors=True)
    return report()


# ------------------------------------------------------------ шаг dialog

def dialog_window(pid, timeout=30):
    """Системный диалог выбора файла — отдельное окно того же процесса."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for hwnd, title in ui.windows_of(pid):
            if "Video Downloader" not in title and title.strip():
                return hwnd, title
        time.sleep(0.3)
    return None, ""


def step_dialog():
    """Добавление .torrent через СИСТЕМНЫЙ диалог (в части A мешал
    висевший запрос брандмауэра)."""
    print("== dialog: .torrent через системный диалог ==")
    prepare()
    seed, info = seed_start()
    try:
        torrent = info["torrent"]
        print(f"  .torrent: {torrent}")
        proc, hwnd = start_app()
        ui.focus(hwnd)

        job = ui.invoke_async(proc.pid, "Открыть .torrent…")
        dlg, title = dialog_window(proc.pid)
        check("системный диалог открылся", dlg is not None, title)
        if dlg is None:
            close_app(proc, hwnd)
            return report()

        ui.focus(dlg)
        ui.shot(dlg, os.path.join(SNAP, "dialog-open.png"))
        ui.type_text(dlg, torrent)
        time.sleep(0.3)
        ui.press(dlg, VK_RETURN)
        job.wait(timeout=60)

        label = wait_card(proc.pid, "Сериал про котиков", 60)
        check("раздача из .torrent появилась в списке", bool(label), label)
        meta = wait_card(proc.pid, "Скачивается", 90) or \
            wait_card(proc.pid, "Раздаётся", 1)
        check("метаданные получены, пошла закачка", bool(meta), meta)
        ui.shot(hwnd, os.path.join(SNAP, "dialog-added.png"))

        secs = close_app(proc, hwnd)
        check("закрытие с готовой раздачей быстрее 1 с", secs < 1.0,
              f"{secs:.2f} с")
    finally:
        seed_stop(seed)
    return report()


# ------------------------------------------------------------ шаг click

def grab(hwnd, path):
    """Снимок БЕЗ смены переднего плана: ui.shot зовёт
    SetForegroundWindow, и открытый модальный диалог уходит за главное
    окно — следующий клик попадает мимо."""
    from PIL import ImageGrab
    ImageGrab.grab(bbox=ui.rect_of(hwnd), all_screens=True).save(path)
    return path


def center(e):
    """Середина элемента: uia.ps1 отдаёт прямоугольник полями x/y/w/h."""
    return int(e["x"] + e["w"] / 2), int(e["y"] + e["h"] / 2)


def dialog_button(pid, name, anchor="Что смотреть"):
    """Кнопка ИЗ ДИАЛОГА, а не одноимённая с карточки: «Смотреть» есть и
    там и там, а карточка лежит под модальным окном — клик по ней не
    делает ничего. Кнопки диалога стоят ПОД его деревом, у самого низа,
    поэтому из одноимённых берём самую нижнюю (и обязательно ниже
    заголовка диалога)."""
    head = element(pid, anchor)
    if head is None:
        return None
    best = None
    for e in ui.elements(pid):
        if (e.get("name") or "") != name or not e.get("w"):
            continue
        if e["y"] <= head["y"]:
            continue
        if best is None or e["y"] > best["y"]:
            best = e
    return best


def element(pid, name, ctl_type=None):
    for e in ui.elements(pid):
        if (e.get("name") or "") == name and \
                (ctl_type is None or e.get("type") == ctl_type) and \
                e.get("w") and e.get("h"):
            return e
    return None


def vlc_lines():
    return [cmd for _pid, _parent, cmd in ui.processes("vlc.exe")]


def step_click(player=VLC):
    """Выбор серии настоящим кликом мыши в «Что смотреть»."""
    print("== click: выбор серии кликом мыши ==")
    if not os.path.isfile(player):
        print(f"  НЕТ ПЛЕЕРА: {player}")
        return 1
    prepare(torrent_player=player)
    # Папку загрузок чистим, а сид придерживаем: иначе 150 МБ с
    # локального сида скачиваются за секунды, файл открывается прямо с
    # диска и путь через наш сервер вообще не проверяется
    shutil.rmtree(DL, ignore_errors=True)
    os.makedirs(DL, exist_ok=True)
    for pid, _parent, cmd in ui.processes("vlc.exe"):
        ui.kill(pid)
    seed, info = seed_start(limit_kb=300)
    try:
        proc, hwnd = start_app()
        ui.focus(hwnd)
        add_magnet(proc.pid, info["magnet"])
        label = wait_card(proc.pid, "Сериал про котиков", 90)
        check("раздача добавлена по magnet", bool(label), label)
        wait_card(proc.pid, "Скачивается", 90)

        job = ui.invoke_async(proc.pid, "Смотреть")
        node = ui.wait_element(
            proc.pid,
            lambda e: "s01e02" in (e.get("name") or "")
            and "TreeItem" in (e.get("type") or ""), 30)
        check("диалог «Что смотреть» открылся", node is not None,
              (node or {}).get("name", ""))
        if node is None:
            close_app(proc, hwnd)
            return report()

        x, y = center(node)
        print(f"  клик по строке второй серии: {x},{y}")
        ui.click(proc.pid, x, y)
        time.sleep(0.5)
        grab(hwnd, os.path.join(SNAP, "click-selected.png"))

        watch = dialog_button(proc.pid, "Смотреть")
        check("кнопка «Смотреть» в диалоге найдена", watch is not None,
              "" if watch is None else "x=%d y=%d" % (watch["x"], watch["y"]))
        if watch:
            ui.click(proc.pid, *center(watch))
        job.wait(timeout=60)

        end = time.monotonic() + 60
        cmd = ""
        while time.monotonic() < end:
            # В http-ссылке кириллица percent-кодирована, по имени
            # «Котики» её не найти — ищем ASCII-часть имени серии
            lines = [c for c in vlc_lines() if "s01e0" in c]
            if lines:
                cmd = lines[0]
                break
            time.sleep(1.0)
        print(f"  командная строка плеера: {cmd[:300]}")
        check("плеер запущен", bool(cmd))
        check("плеер открыл ВТОРУЮ серию (ту, по которой кликнули)",
              "s01e02" in cmd and "s01e01.mkv" not in cmd)
        # Недокачанный файл идёт через наш сервер, скачанный целиком —
        # прямо с диска (это поведение движка, а не ошибка выбора)
        check("недокачанный файл открыт через наш http-сервер",
              "http://" in cmd, "локальный путь" if cmd and
              "http://" not in cmd else "")
        save_state(vlc_cmd=cmd)

        for pid, _parent, c in ui.processes("vlc.exe"):
            if "s01e0" in c or "http://" in c:
                ui.kill(pid)
        secs = close_app(proc, hwnd)
        check("закрытие после просмотра быстрее 1 с", secs < 1.0,
              f"{secs:.2f} с")
    finally:
        seed_stop(seed)
    return report()


# ------------------------------------------------------------ шаг subs

def step_subs(player=VLC):
    """ТОЛЬКО наблюдение: что VLC делает с субтитрами-спутниками по http
    (правило «та же папка» по решению владельца не чиним)."""
    print("== subs: VLC и субтитры по http (наблюдение) ==")
    prepare(torrent_player=player)
    shutil.rmtree(DL, ignore_errors=True)
    os.makedirs(DL, exist_ok=True)
    for pid, _parent, cmd in ui.processes("vlc.exe"):
        ui.kill(pid)
    seed, info = seed_start(limit_kb=300)
    try:
        proc, hwnd = start_app()
        ui.focus(hwnd)
        add_magnet(proc.pid, info["magnet"])
        wait_card(proc.pid, "Сериал про котиков", 90)
        wait_card(proc.pid, "Скачивается", 90)

        job = ui.invoke_async(proc.pid, "Смотреть")
        node = ui.wait_element(
            proc.pid,
            lambda e: "s01e01" in (e.get("name") or "")
            and "TreeItem" in (e.get("type") or ""), 30)
        if node is not None:
            ui.click(proc.pid, *center(node))
            time.sleep(0.4)
            watch = dialog_button(proc.pid, "Смотреть")
            if watch:
                ui.click(proc.pid, *center(watch))
        job.wait(timeout=60)

        end = time.monotonic() + 60
        cmd = ""
        while time.monotonic() < end:
            lines = [c for c in vlc_lines() if "s01e0" in c]
            if lines:
                cmd = lines[0]
                break
            time.sleep(1.0)
        print("  командная строка VLC:")
        print(f"  {cmd}")
        print(f"  --sub-file передан: {'--sub-file' in cmd}")
        print(f"  subs в папке Subs: {os.path.isdir(os.path.join(DL, 'Subs'))}")
        save_state(subs_cmd=cmd)
        time.sleep(15)
        grab(hwnd, os.path.join(SNAP, "subs-watching.png"))
        for pid, _parent, c in ui.processes("vlc.exe"):
            if "s01e0" in c or "http://" in c:
                ui.kill(pid)
        close_app(proc, hwnd)
    finally:
        seed_stop(seed)
    return 0


# ------------------------------------------------------------ уборка

def step_cleanup():
    """Вернуть машину в исходное: настройки, тестовые данные, сид."""
    print("== cleanup ==")
    st = state()
    backup = st.get("backup")
    if backup and os.path.isfile(backup):
        shutil.copy2(backup, ui.SETTINGS)
        print(f"  settings.json возвращён из {backup}")
    for pid, _parent, cmd in ui.processes("VideoDownloader.exe"):
        ui.kill(pid)
    for pid, _parent, cmd in ui.processes("vlc.exe"):
        if "http://" in cmd:
            ui.kill(pid)
    shutil.rmtree(ui.TORRENT_DATA, ignore_errors=True)
    if os.path.isfile(ui.TORRENT_LOG):
        os.remove(ui.TORRENT_LOG)
    print(f"  тестовые torrents\\ и torrent.log удалены")
    print(f"  данные шагов остались в {BASE} (снимки: {SNAP})")
    return 0


STEPS = {
    "nometa": step_nometa,
    "dialog": step_dialog,
    "click": step_click,
    "subs": step_subs,
    "cleanup": step_cleanup,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in STEPS:
        print(f"Использование: {os.path.basename(__file__)} "
              f"<{'|'.join(STEPS)}>")
        sys.exit(2)
    sys.exit(STEPS[sys.argv[1]]())

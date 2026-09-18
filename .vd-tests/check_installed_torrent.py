"""Проверки УСТАНОВЛЕННОЙ копии (сессия 2.4 часть B; с релиза 1.1.0 —
поток добавления 2.6 и Библиотека торрентов).

Каждый шаг — отдельной командой (приложение запускается и закрывается
внутри шага — состояние между шагами не делится):

    nometa   — закрытие окна с раздачей БЕЗ метаданных (находка 42:
               было 3.28 с, ожидаем < 1 с) + контроль: fastresume
               магнита всё равно записан;
    dialog   — добавление .torrent через СИСТЕМНЫЙ диалог «Выберите
               .torrent», затем «Скачать» в окне выбора (2.6);
    cancel   — «Отмена» в окне выбора: раздача убрана вместе с
               fastresume, в Библиотеку не попала (2.6, долг 1.4);
    readd    — повторное добавление раздачи, которая уже в списке: окно
               выбора НЕ открывается, файлы целы (до исправления его
               «Отмена» стирала скачанное — найдено перед релизом 1.1.0);
    click    — выбор серии НАСТОЯЩИМ кликом мыши в окне выбора и
               «Посмотреть»: плеер открывает именно её (2.6), а раздача
               уходит во ВРЕМЕННУЮ папку, не в загрузки (2.7);
    tempwatch— весь путь временного просмотра на НАСТОЯЩЕМ mpv (2.7):
               файлы под %TEMP%\\VideoDownloader\\torrent-watch, в
               Библиотеку не пишется, закрытие плеера раньше конца ->
               пауза + сохранённая позиция, повторный просмотр -> mpv
               получает --start=<позиция-5> и закачка продолжается;
    finish    — досмотр до конца (короткая вторая серия, mpv доигрывает
               сам): файл удалён, раздача и временная папка убраны;
    space     — окно «На диске мало места»: TEMP установленной копии
               направлен на маленький том (путь в переменной
               VD_SPACE_TEMP), проверяется текст, кнопки и что «Отмена»
               не запускает плеер;
    reboot-arm/reboot-check — уборка временных раздач после НАСТОЯЩЕЙ
               перезагрузки: arm оставляет временную и постоянную
               раздачу и печатает, что делать владельцу; check после
               включения машины сверяет, что временной нет, а
               постоянная цела;
    subs     — что делает VLC с субтитрами по http (только запись
               наблюдения, правило «та же папка» не чиним);
    library  — Библиотека торрентов на установленной копии: запись в
               settings.json, «Очистить данные библиотеки» не трогает
               раздачу, удаление записи с галочкой снимает раздачу с
               «Торрентов» и удаляет файлы (долг 1.4).

С 2.6 окно выбора открывается САМО, как только пришёл список файлов, и
оно модальное: вызов «Добавить»/«Открыть .torrent…» у .torrent не
вернётся, пока окно не закрыто, — такие вызовы идут через invoke_async.

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
# Плееры: на домашней машине в системе нет ни того, ни другого —
# берём портативные копии прототипа Этапа 0 (те же, что в живых
# проверках 2.3 и 2.7)
VLC = ui.VLC if os.path.isfile(ui.VLC) else \
    r"C:\Program Files\VideoLAN\VLC\vlc.exe"
MPV = ui.MPV

# Временные раздачи 2.7: у собранной копии корень без приставки -dev
WATCH_ROOT = os.path.join(tempfile.gettempdir(), "VideoDownloader",
                          "torrent-watch")
TEMP_TEXT = "просмотр во временной папке"

BASE = os.path.join(tempfile.gettempdir(), "vd-110")
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
    """Резервная копия settings.json — ровно одна на весь прогон шагов
    (до cleanup)."""
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
    """Установленную копию — в режим Torrent со своей папкой загрузок.

    Каждый шаг начинает с ЧИСТОГО состояния: без раздач (fastresume), без
    загрузок и без записей Библиотеки. Иначе раздача, оставленная прошлым
    шагом, поднимается из fastresume, и следующий шаг проверяет не то:
    так «cancel» на первой сборке 1.1.0 прошёл лишь потому, что стёр
    раздачу шага «dialog» — это и был найденный дефект.
    """
    ensure_backup()
    for pid, _parent, _cmd in ui.processes("VideoDownloader.exe"):
        ui.kill(pid)
    shutil.rmtree(ui.TORRENT_DATA, ignore_errors=True)
    shutil.rmtree(DL, ignore_errors=True)
    # Временные раздачи 2.7: без чистки корня прошлая раздача поднялась
    # бы из fastresume вместе со своей папкой
    shutil.rmtree(WATCH_ROOT, ignore_errors=True)
    os.makedirs(DL, exist_ok=True)
    os.makedirs(SNAP, exist_ok=True)
    changes = {"app_mode": "torrent", "torrent_folder": DL,
               "torrent_history": []}
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


def _plain(text):
    """Неразрывные пробелы -> обычные: подписи Библиотеки торрентов не
    рвут «+ 1 файл» и дату переносом (NBSP внутри частей)."""
    return (text or "").replace(" ", " ")


def card_text(pid, must_have):
    for e in ui.elements(pid):
        name = _plain(e.get("name"))
        if must_have in name:
            return name
    return ""


def wait_card(pid, must_have, timeout):
    e = ui.wait_element(pid, lambda x: must_have in _plain(x.get("name")),
                        timeout)
    return _plain((e or {}).get("name", ""))


def add_magnet(pid, uri, tries=5):
    """Вставить magnet и нажать «Добавить».

    Значение ОБЯЗАТЕЛЬНО читается обратно: SetValue у Qt-поля изредка не
    доходит (поймано четырьмя сбоями подряд — «Добавить» нажималось по
    пустому полю, карточка не появлялась, и шаг падал в непонятном
    месте). Тот же урок, что в находке 29: прочитать обратно, а не
    считать, что записалось.

    Нажатие — асинхронно: с 2.6 окно выбора открывается, как только
    пришёл список файлов, и если это случится, пока Qt ещё обрабатывает
    нажатие, синхронный Invoke ждал бы закрытия модального окна.
    """
    for attempt in range(tries):
        # Поле берём ВИДИМОЕ: после посещения Библиотеки в дереве UIA
        # остаются и её поля, а set_value без выбора берёт первое по
        # обходу — ссылка ложилась в скрытое поле, «Добавить» читала
        # пустое, и раздача молча не добавлялась
        edits = [e for e in ui.elements(pid)
                 if e.get("type") == "ControlType.Edit"]
        nth = next((i for i, e in enumerate(edits)
                    if e.get("w") and e.get("h")), 0)
        if attempt == 0 and len(edits) > 1:
            print(f"  полей Edit на экране {len(edits)}, берём "
                  f"видимое №{nth}: "
                  f"{[(e.get('cls'), e.get('w'), e.get('h')) for e in edits]}")
        res = ui.set_value(pid, uri, ctl_type="ControlType.Edit", nth=nth)
        back = (res or {}).get("value") or ""
        if back.strip() != uri.strip():
            print(f"  ссылка не легла в поле (попытка {attempt + 1}/{tries}): "
                  f"ok={(res or {}).get('ok')}, в поле {back[:40]!r}")
            time.sleep(1.0)
            continue
        job = ui.invoke_async(pid, "Добавить")
        # Ждём ПОСЛЕДСТВИЯ нажатия, а не сам факт вызова: асинхронное
        # Invoke изредка не доходит до кнопки, и раздача молча не
        # добавлялась (шаг падал позже и в другом месте). Повторное
        # нажатие безопасно: тот же magnet даёт «Эта раздача уже в
        # списке» и ничего не удаляет (находка 48)
        end = time.monotonic() + 25
        while time.monotonic() < end:
            names = [_plain(n) for n in ui.names(pid)]
            if any(CHOICE_TITLE in n or "уже в списке" in n
                   or "Получаем список файлов" in n
                   or "Ожидает выбора файлов" in n for n in names):
                return job
            time.sleep(0.5)
        print(f"  нажатие «Добавить» не дало ничего за 25 с "
              f"(попытка {attempt + 1}/{tries}) — жмём снова")
    raise RuntimeError("magnet не добавился: «Добавить» не отвечает")


def click_button(pid, name):
    """Клик мышью по ВИДИМОЙ кнопке (у кнопок скрытых страниц нет
    прямоугольника). Клик не ждёт модального окна, в отличие от Invoke."""
    e = element(pid, name, "ControlType.Button")
    if e is None:
        return False
    ui.click(pid, *center(e))
    return True


CHOICE_TITLE = "Что скачать"       # заголовок окна выбора при добавлении
MANAGE_TITLE = "Файлы раздачи"     # то же окно от кнопок «Файлы»/«Смотреть»


def wait_choice(pid, timeout=90):
    """Окно выбора (2.6) открылось само — ждём его заголовок."""
    return ui.wait_element(
        pid, lambda e: (e.get("name") or "") == CHOICE_TITLE
        and e.get("w"), timeout) is not None


def answer_choice(pid, button):
    """Нажать кнопку ОКНА ВЫБОРА мышью («Отмена»/«Скачать»/«Посмотреть»).
    «Отмена» и «Скачать» бывают и вне окна — берём кнопку под деревом."""
    btn = dialog_button(pid, button, anchor=CHOICE_TITLE)
    if btn is None:
        return False
    ui.click(pid, *center(btn))
    return True


def wait_gone(pid, name, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if element(pid, name) is None:
            return True
        time.sleep(0.5)
    return False


def resume_files():
    try:
        return sorted(os.listdir(RESUME))
    except OSError:
        return []


# ------------------------------------------------- временные раздачи (2.7)

def watch_marks():
    """Метки .watch: они и означают «раздача временная»."""
    return [n for n in resume_files() if n.endswith(".watch")]


def read_watch(infohash):
    path = os.path.join(RESUME, infohash + ".watch")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def watch_dirs():
    try:
        return sorted(n for n in os.listdir(WATCH_ROOT)
                      if os.path.isdir(os.path.join(WATCH_ROOT, n)))
    except OSError:
        return []


def tree_size(root):
    """Сколько байт занято НАСТОЯЩИМИ данными.

    libtorrent создаёт файлы сразу полного размера, поэтому размер файла
    ничего не говорит о скачанном — берём размер на диске (sparse-файл
    занимает столько, сколько в него записано).
    """
    total = 0
    for base, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(base, name)
            try:
                total += _on_disk(path)
            except OSError:
                pass
    return total


def _on_disk(path):
    import ctypes
    high = ctypes.c_ulong(0)
    low = ctypes.windll.kernel32.GetCompressedFileSizeW(
        ctypes.c_wchar_p(path), ctypes.byref(high))
    if low == 0xFFFFFFFF and ctypes.get_last_error():
        return os.path.getsize(path)
    return (high.value << 32) + low


def player_lines(exe):
    """Командные строки живых процессов этого плеера."""
    image = os.path.basename(exe)
    return [cmd for _pid, _parent, cmd in ui.processes(image)]


def wait_player_cmd(exe, must_have, timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for cmd in player_lines(exe):
            if must_have in cmd:
                return cmd
        time.sleep(1.0)
    return ""


def kill_players(*exes):
    for exe in exes or (VLC, MPV):
        for pid, _parent, _cmd in ui.processes(os.path.basename(exe)):
            ui.kill(pid)


def tree_item(pid, part, timeout=30):
    """Строка файла в окне выбора."""
    return ui.wait_element(
        pid, lambda e: part in (e.get("name") or "")
        and "TreeItem" in (e.get("type") or ""), timeout)


def pick_file(pid, part):
    """Выделить строку файла НАСТОЯЩИМ кликом мыши (UIA выделение в
    Qt-дереве не работает — находка 43). Клик по названию, а не по
    квадратику галочки: галочка строку не выделяет (2.6)."""
    node = tree_item(pid, part)
    if node is None:
        return False
    ui.click(pid, node["x"] + node["w"] - 20, center(node)[1])
    time.sleep(0.5)
    return True


def visible_buttons(pid):
    return sorted({e.get("name") for e in ui.elements(pid)
                   if e.get("type") == "ControlType.Button" and e.get("w")})


def dl_files():
    """Видеофайлы, появившиеся в ПАПКЕ ЗАГРУЗОК (у временной раздачи их
    там быть не должно)."""
    out = []
    for base, _dirs, files in os.walk(DL):
        out += [os.path.join(base, n) for n in files]
    return out


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
                label = wait_card(proc.pid, "Получаем список файлов", 30)
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

        # У .torrent список файлов есть сразу: окно выбора открывается
        # внутри того же вызова, и job не вернётся, пока окно не закрыто
        check("окно выбора открылось само", wait_choice(proc.pid, 60))
        grab(hwnd, os.path.join(SNAP, "dialog-choice.png"))
        check("«Скачать» в окне выбора нажата",
              answer_choice(proc.pid, "Скачать"))
        job.wait(timeout=60)

        label = wait_card(proc.pid, "Сериал про котиков", 60)
        check("раздача из .torrent появилась в списке", bool(label), label)
        meta = wait_card(proc.pid, "Скачивается", 90) or \
            wait_card(proc.pid, "Раздаётся", 1)
        check("метаданные получены, пошла закачка", bool(meta), meta)
        ui.shot(hwnd, os.path.join(SNAP, "dialog-added.png"))

        secs = close_app(proc, hwnd)
        # Раздача к этому моменту только начала качаться, а закрытие при
        # активной закачке — открытая находка 50 (2.2-2.6 с). Меряем
        # бюджет shutdown, а не «меньше секунды»
        print(f"  закрытие окна при начавшейся закачке: {secs:.2f} с")
        check("закрытие укладывается в бюджет shutdown (3.0 с + запас)",
              secs < 4.0, f"{secs:.2f} с")
        check("fastresume раздачи записан",
              any(n.endswith(".fastresume") for n in resume_files()),
              str(resume_files()))
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
    """Выбор серии настоящим кликом мыши в окне выбора и «Посмотреть»."""
    print("== click: выбор серии кликом мыши ==")
    if not os.path.isfile(player):
        print(f"  НЕТ ПЛЕЕРА: {player}")
        return 1
    prepare(torrent_player=player)
    # Сид придерживаем (папку загрузок чистит prepare): иначе 150 МБ с
    # локального сида скачиваются за секунды, файл открывается прямо с
    # диска и путь через наш сервер вообще не проверяется
    for pid, _parent, cmd in ui.processes("vlc.exe"):
        ui.kill(pid)
    seed, info = seed_start(limit_kb=300)
    try:
        proc, hwnd = start_app()
        ui.focus(hwnd)
        add_magnet(proc.pid, info["magnet"])
        label = wait_card(proc.pid, "Сериал про котиков", 90)
        check("раздача добавлена по magnet", bool(label), label)
        check("окно выбора открылось, когда пришёл список файлов",
              wait_choice(proc.pid, 90))
        check("до ответа раздача ничего не качает",
              bool(wait_card(proc.pid, "Ожидает выбора файлов", 10)))

        node = ui.wait_element(
            proc.pid,
            lambda e: "s01e02" in (e.get("name") or "")
            and "TreeItem" in (e.get("type") or ""), 30)
        check("в окне выбора видна вторая серия", node is not None,
              (node or {}).get("name", ""))
        if node is None:
            close_app(proc, hwnd)
            return report()

        # По названию, а не по квадратику галочки: клик по галочке строку
        # не выделяет (2.6, сценарий 13 офлайн-теста)
        x = node["x"] + node["w"] - 20
        y = center(node)[1]
        print(f"  клик по строке второй серии: {x},{y}")
        ui.click(proc.pid, x, y)
        time.sleep(0.5)
        grab(hwnd, os.path.join(SNAP, "click-selected.png"))
        before_dl = dl_files()

        check("«Посмотреть» в окне выбора нажата",
              answer_choice(proc.pid, "Посмотреть"))

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

        # 2.7: «Посмотреть» делает раздачу ВРЕМЕННОЙ
        dirs = watch_dirs()
        check("раздача уехала во временную папку", bool(dirs),
              f"{WATCH_ROOT}: {dirs}")
        check("метка .watch создана", bool(watch_marks()),
              str(watch_marks()))
        check("в папке загрузок ничего не появилось",
              dl_files() == before_dl, str(dl_files()[:3]))
        check("в Библиотеку временная раздача не пишется",
              not torrent_history(), str(len(torrent_history())))
        label = wait_card(proc.pid, TEMP_TEXT, 20)
        check("на карточке подпись «просмотр во временной папке»",
              bool(label), label)
        buttons = visible_buttons(proc.pid)
        print(f"  кнопки карточки: {buttons}")
        check("у временной раздачи нет «Пауза»/«Продолжить»",
              "Пауза" not in buttons and "Продолжить" not in buttons)
        check("есть «Остановить просмотр»",
              "Остановить просмотр" in buttons)

        for pid, _parent, c in ui.processes("vlc.exe"):
            if "s01e0" in c or "http://" in c:
                ui.kill(pid)
        secs = close_app(proc, hwnd)
        # Порог «< 1 с» был снят с ГОТОВОЙ раздачи (2.4). Здесь раздача
        # активно качается, а это открытая находка 50: у собранной копии
        # закрытие при закачке 2.2-2.6 с. Поэтому проверяем не скорость,
        # а что бюджет shutdown соблюдён и данные не потеряны
        print(f"  закрытие окна при активной закачке: {secs:.2f} с")
        check("закрытие укладывается в бюджет shutdown (3.0 с + запас)",
              secs < 4.0, f"{secs:.2f} с")
        check("метка .watch после закрытия на месте (прогресс не потерян)",
              bool(watch_marks()), str(watch_marks()))
    finally:
        kill_players()
        seed_stop(seed)
    return report()


# ------------------------------------------------------------ шаг subs

def step_subs(player=VLC):
    """ТОЛЬКО наблюдение: что VLC делает с субтитрами-спутниками по http
    (правило «та же папка» по решению владельца не чиним)."""
    print("== subs: VLC и субтитры по http (наблюдение) ==")
    prepare(torrent_player=player)
    for pid, _parent, cmd in ui.processes("vlc.exe"):
        ui.kill(pid)
    seed, info = seed_start(limit_kb=300)
    try:
        proc, hwnd = start_app()
        ui.focus(hwnd)
        add_magnet(proc.pid, info["magnet"])
        wait_card(proc.pid, "Сериал про котиков", 90)
        wait_choice(proc.pid, 90)
        node = ui.wait_element(
            proc.pid,
            lambda e: "s01e01.mkv" in (e.get("name") or "")
            and "TreeItem" in (e.get("type") or ""), 30)
        if node is not None:
            ui.click(proc.pid, node["x"] + node["w"] - 20, center(node)[1])
            time.sleep(0.4)
            answer_choice(proc.pid, "Посмотреть")

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


# ------------------------------------------------------------ шаг cancel

def infohash_files(infohash):
    return [n for n in resume_files() if n.startswith(infohash)]


def torrent_history():
    return (ui.read_settings().get("torrent_history") or [])


def step_cancel():
    """«Отмена» в окне выбора: раздача убрана целиком, в Библиотеку не
    попала (запись появляется только после «Скачать»/«Посмотреть»)."""
    print("== cancel: «Отмена» в окне выбора ==")
    prepare()
    seed, info = seed_start(limit_kb=300)
    try:
        proc, hwnd = start_app()
        ui.focus(hwnd)
        add_magnet(proc.pid, info["magnet"])
        check("окно выбора открылось", wait_choice(proc.pid, 90))
        grab(hwnd, os.path.join(SNAP, "cancel-choice.png"))
        check("«Отмена» нажата", answer_choice(proc.pid, "Отмена"))
        check("окно выбора закрылось", wait_gone(proc.pid, CHOICE_TITLE, 15))
        check("карточка раздачи убрана",
              wait_gone(proc.pid, "Сериал про котиков", 15))
        time.sleep(1.0)
        check("fastresume/.pending раздачи удалены",
              not infohash_files(info["infohash"]),
              str(infohash_files(info["infohash"])))
        close_app(proc, hwnd)
        check("в Библиотеке записи нет",
              not any(e.get("id") == info["infohash"]
                      for e in torrent_history()))
    finally:
        seed_stop(seed)
    return report()


# ------------------------------------------------------------ шаг readd

def step_readd():
    """Повторный magnet уже скачанной раздачи не открывает окно выбора
    (его «Отмена» удаляла файлы) и ничего не удаляет."""
    print("== readd: повторное добавление раздачи, которая уже в списке ==")
    prepare()
    seed, info = seed_start(limit_kb=20000)
    video = os.path.join(DL, "Сериал про котиков", "Котики s01e01.mkv")
    try:
        proc, hwnd = start_app()
        ui.focus(hwnd)
        check("«Скачать» в окне выбора", download_all(proc.pid, info["magnet"]))
        seeding = wait_card(proc.pid, "Раздаётся", 180)
        check("раздача скачана и раздаётся", bool(seeding), seeding)
        size_before = os.path.getsize(video) if os.path.isfile(video) else -1

        add_magnet(proc.pid, info["magnet"])
        note = wait_card(proc.pid, "Эта раздача уже в списке", 15)
        check("сообщение «Эта раздача уже в списке»", bool(note), note)
        opened = wait_choice(proc.pid, 5)
        grab(hwnd, os.path.join(SNAP, "readd-note.png"))
        check("окно выбора НЕ открылось", not opened)
        if opened:                       # не дать «Отмене» случиться
            ui.close_window(hwnd)
        time.sleep(2.0)
        check("раздача по-прежнему в списке и раздаётся",
              bool(wait_card(proc.pid, "Раздаётся", 10)))
        check("скачанный файл на месте",
              os.path.isfile(video) and os.path.getsize(video) == size_before,
              f"{size_before} байт")
        check("fastresume раздачи на месте",
              any(n.endswith(".fastresume")
                  for n in infohash_files(info["infohash"])),
              str(infohash_files(info["infohash"])))
        close_app(proc, hwnd)
    finally:
        seed_stop(seed)
    return report()


# ------------------------------------------------------------ шаг library

TORRENT_NAV = ("Торренты", "Библиотека")    # порядок пунктов режима сверху


def open_nav(pid, name):
    """Перейти на страницу режима Torrent через навигацию.

    У пунктов навигации qfluentwidgets в UI Automation НЕТ имени
    (NavigationTreeItem с пустым name, панель свёрнута до иконок), а
    пункты чужого режима скрыты и в дамп не попадают. Поэтому пункт
    ищем по месту: видимые пункты сверху вниз — «Торренты»,
    «Библиотека», последний (внизу) — «Настройки». Переход
    подтверждаем заголовком страницы (TitleLabel).
    """
    items = sorted((e for e in ui.elements(pid)
                    if e.get("cls") == "NavigationTreeItem" and e.get("w")),
                   key=lambda e: e["y"])
    top = items[:-1]                       # последний — «Настройки»
    idx = TORRENT_NAV.index(name)
    if len(top) != len(TORRENT_NAV):
        print(f"  навигация: ожидалось {len(TORRENT_NAV)} пункта сверху, "
              f"видно {len(top)}")
        return False
    ui.click(pid, *center(top[idx]))
    title = ui.wait_element(
        pid, lambda e: e.get("cls") == "TitleLabel"
        and e.get("name") == name and e.get("w"), 10)
    return title is not None


def page_text(pid, part, timeout=30):
    return wait_card(pid, part, timeout)


def download_all(pid, magnet):
    add_magnet(pid, magnet)
    if not wait_choice(pid, 90):
        return False
    return answer_choice(pid, "Скачать")


def step_library():
    """Библиотека торрентов на установленной копии (долг 1.4)."""
    print("== library: Библиотека торрентов ==")
    prepare()
    seed, info = seed_start(limit_kb=20000)
    ih = info["infohash"]
    root = os.path.join(DL, "Сериал про котиков")
    try:
        proc, hwnd = start_app()
        ui.focus(hwnd)

        # а) «Скачать» -> запись в Библиотеке и в settings.json
        check("«Скачать» в окне выбора", download_all(proc.pid, info["magnet"]))
        seeding = wait_card(proc.pid, "Раздаётся", 180)
        check("раздача скачана и раздаётся", bool(seeding), seeding)
        check("запись в settings.json установленной копии",
              any(e.get("id") == ih for e in torrent_history()))
        if not check("пункт «Библиотека» режима Torrent виден и открывается",
                     open_nav(proc.pid, "Библиотека")):
            close_app(proc, hwnd)          # дальше жали бы кнопки не той
            return report()                # страницы
        row = page_text(proc.pid, "Скачано 2 из 2 видеофайлов", 20)
        check("строка раздачи: «Скачано 2 из 2 видеофайлов», «+ 1 файл»",
              bool(row) and "файл" in row, row)
        grab(hwnd, os.path.join(SNAP, "library-row.png"))

        # б) «Очистить данные библиотеки»: список пуст, раздача и файлы целы
        clear = element(proc.pid, "Очистить данные библиотеки",
                        "ControlType.Button")
        check("кнопка «Очистить данные библиотеки» найдена", clear is not None)
        click_button(proc.pid, "Очистить данные библиотеки")
        time.sleep(1.5)
        grab(hwnd, os.path.join(SNAP, "library-clear-confirm.png"))
        buttons = sorted({e.get("name") for e in ui.elements(proc.pid)
                          if e.get("type") == "ControlType.Button"})
        print(f"  кнопки при подтверждении очистки: {buttons}")
        save_state(clear_confirm_buttons=buttons)
        confirm = None
        for label in ("Очистить", "OK", "ОК"):
            confirm = dialog_button(proc.pid, label,
                                    anchor="Список раздач будет очищен. "
                                    "Раздачи на странице «Торренты» и "
                                    "файлы на диске останутся.")
            if confirm:
                break
        check("подтверждение очистки найдено", confirm is not None,
              "" if confirm is None else confirm["name"])
        if confirm:
            ui.click(proc.pid, *center(confirm))
        empty = page_text(proc.pid, "Здесь появятся раздачи", 15)
        check("после очистки список пуст", bool(empty))
        check("очистка: записей в settings.json нет",
              not torrent_history(), str(len(torrent_history())))
        check("очистка: файлы на диске целы",
              os.path.isfile(os.path.join(root, "Котики s01e01.mkv")))
        check("очистка: раздача осталась на «Торрентах»",
              open_nav(proc.pid, "Торренты")
              and bool(wait_card(proc.pid, "Раздаётся", 10)))

        # в) снова в Библиотеку: убрать с карточки без файлов и добавить
        # заново — запись появится снова (файлы уже есть, докачки нет)
        click_button(proc.pid, "Удалить")
        time.sleep(1.0)
        remove = dialog_button(proc.pid, "Убрать", anchor="Убрать раздачу?")
        check("карточка: подтверждение «Убрать» найдено", remove is not None)
        if remove:
            ui.click(proc.pid, *center(remove))
        check("карточка убрана без файлов",
              wait_gone(proc.pid, "Сериал про котиков", 15)
              and os.path.isfile(os.path.join(root, "Котики s01e01.mkv")))
        log_at = ui.log_size()
        add_magnet(proc.pid, info["magnet"])
        # Ответ приложения ловим СРАЗУ: InfoBar живёт несколько секунд, и
        # взгляд на экран через 90 с его уже не видит
        seen = []
        watch_end = time.monotonic() + 20
        while time.monotonic() < watch_end:
            for n in ui.names(proc.pid):
                plain = _plain(n)
                if plain not in seen:
                    seen.append(plain)
            if any("Что скачать" in s for s in seen):
                break
            time.sleep(0.5)
        print(f"  что показало окно за 20 с: {seen}")
        opened = wait_choice(proc.pid, 70)
        grab(hwnd, os.path.join(SNAP, "library-readd-after-remove.png"))
        if not opened:
            print(f"  torrent.log с момента нажатия: "
                  f"{ui.log_since(log_at)[:800]!r}")
            print("  карточки на экране: "
                  f"{card_text(proc.pid, 'Сериал про котиков')!r}")
            print(f"  подписи окна: {ui.names(proc.pid)[:40]}")
            print(f"  resume: {resume_files()}")
        check("снова «Скачать»",
              opened and answer_choice(proc.pid, "Скачать"))
        check("раздача снова раздаётся",
              bool(wait_card(proc.pid, "Раздаётся", 120)))
        check("запись вернулась в settings.json",
              any(e.get("id") == ih for e in torrent_history()))

        # г) удаление записи с галочкой «удалить файлы»
        if not check("снова открыта Библиотека",
                     open_nav(proc.pid, "Библиотека")):
            close_app(proc, hwnd)
            return report()
        page_text(proc.pid, "Сериал про котиков", 15)
        boxes = [e for e in ui.elements(proc.pid)
                 if e.get("type") == "ControlType.CheckBox" and e.get("w")]
        check("галочка строки найдена", bool(boxes), str(len(boxes)))
        if boxes:
            ui.click(proc.pid, *center(boxes[0]))
            time.sleep(0.5)
        click_button(proc.pid, "Удалить")
        time.sleep(1.5)
        files_box = element(proc.pid, "Удалить также файлы с диска")
        check("в подтверждении есть галочка «Удалить также файлы с диска»",
              files_box is not None)
        if files_box:
            ui.click(proc.pid, *center(files_box))
            time.sleep(0.5)
        grab(hwnd, os.path.join(SNAP, "library-delete-confirm.png"))
        yes = dialog_button(proc.pid, "Удалить", anchor="Удалить 1 запись?")
        check("кнопка «Удалить» в подтверждении найдена", yes is not None)
        if yes:
            ui.click(proc.pid, *center(yes))
        check("после удаления список пуст",
              bool(page_text(proc.pid, "Здесь появятся раздачи", 15)))
        end = time.monotonic() + 20
        while time.monotonic() < end and os.path.exists(
                os.path.join(root, "Котики s01e01.mkv")):
            time.sleep(0.5)
        check("файлы раздачи удалены с диска",
              not os.path.exists(os.path.join(root, "Котики s01e01.mkv")))
        check("fastresume/.chosen/.pending раздачи удалены",
              not infohash_files(ih), str(infohash_files(ih)))
        check("раздача снята и с «Торрентов»",
              open_nav(proc.pid, "Торренты")
              and wait_gone(proc.pid, "Сериал про котиков", 10))
        check("записи в settings.json нет",
              not any(e.get("id") == ih for e in torrent_history()))

        secs = close_app(proc, hwnd)
        check("закрытие быстрее 1.5 с", secs < 1.5, f"{secs:.2f} с")
    finally:
        seed_stop(seed)
    return report()


# ------------------------------------------------- шаг tempwatch (2.7)

def step_tempwatch(player=MPV):
    """Весь путь временного просмотра на НАСТОЯЩЕМ mpv.

    Почему mpv, а не VLC: позицию показа отдаёт только он (JSON IPC), и
    именно по ней движок решает «досмотрел» и «докуда доиграли». VLC на
    этом шаге проверять нечем — у него на установленной копии нет
    журнала (2.4, часть B).

    Скорость сида держим чуть выше битрейта первой серии (6 Мбит/с ≈
    750 КБ/с): играть mpv должен без подвисаний, но файл (135 МБ) за
    время шага докачаться НЕ должен — иначе просмотр пойдёт с диска, а
    не через наш сервер, и повторное открытие проверит не то.
    """
    print("== tempwatch: временный просмотр на настоящем mpv ==")
    if not os.path.isfile(player):
        print(f"  НЕТ ПЛЕЕРА: {player}")
        return 1
    prepare(torrent_player=player)
    kill_players()
    seed, info = seed_start(limit_kb=1200)
    ih = info["infohash"]
    try:
        proc, hwnd = start_app()
        ui.focus(hwnd)
        add_magnet(proc.pid, info["magnet"])
        check("раздача добавлена по magnet",
              bool(wait_card(proc.pid, "Сериал про котиков", 90)))
        check("окно выбора открылось само", wait_choice(proc.pid, 90))
        check("первая серия выделена кликом мыши",
              pick_file(proc.pid, "s01e01.mkv"))
        check("«Посмотреть» нажата", answer_choice(proc.pid, "Посмотреть"))

        cmd = wait_player_cmd(player, "s01e0", 90)
        print(f"  командная строка mpv: {cmd[:300]}")
        check("mpv запущен", bool(cmd))
        check("mpv играет наш http-поток", "http://" in cmd)
        check("mpv запущен под наблюдением (канал IPC)",
              "--input-ipc-server=" in cmd)
        check("при первом открытии позиции нет (без --start)",
              "--start=" not in cmd)

        dirs = watch_dirs()
        check("временная папка раздачи создана", bool(dirs),
              f"{WATCH_ROOT}: {dirs}")
        check("метка .watch создана", ih + ".watch" in watch_marks(),
              str(watch_marks()))
        check("в папке загрузок ничего не появилось", not dl_files(),
              str(dl_files()[:3]))
        check("в Библиотеку временная раздача не пишется",
              not torrent_history())
        check("карточка: «просмотр во временной папке»",
              bool(wait_card(proc.pid, TEMP_TEXT, 20)))
        check("карточка: «идёт просмотр» (плеер дошёл до сервера)",
              bool(wait_card(proc.pid, "идёт просмотр", 90)))

        # Даём mpv поиграть: позиция должна уйти дальше RESUME_MIN_S=10 с
        print("  играем 40 с…", flush=True)
        time.sleep(40)
        grab(hwnd, os.path.join(SNAP, "tempwatch-playing.png"))
        wdir = os.path.join(WATCH_ROOT, dirs[0]) if dirs else WATCH_ROOT
        size1 = tree_size(wdir)
        card1 = card_text(proc.pid, TEMP_TEXT)
        print(f"  скачано во временной папке: {size1 / 1048576:.1f} МБ")
        print(f"  карточка: {card1}")

        # Закрываем плеер РАНЬШЕ конца — как пользователь крестиком
        kill_players(player)
        paused = wait_card(proc.pid, "Пауза", 30)
        check("после закрытия плеера раздача на паузе", bool(paused), paused)
        check("кнопка «Смотреть» вернулась",
              "Смотреть" in visible_buttons(proc.pid),
              str(visible_buttons(proc.pid)))
        mark = read_watch(ih)
        positions = mark.get("positions") or {}
        pos = None
        for value in positions.values():
            pos = float(value)
        print(f"  .watch: {json.dumps(mark, ensure_ascii=False)[:300]}")
        check("позиция просмотра сохранена в .watch", pos is not None,
              str(positions))
        check("позиция похожа на настоящую (10 с и больше)",
              bool(pos and pos >= 10), f"{pos} с")
        save_state(tempwatch_position=pos, tempwatch_mb=size1 / 1048576)

        # Повторный просмотр: у сериала два видео, поэтому «Смотреть»
        # снова показывает окно выбора — выбираем ту же серию
        check("«Смотреть» на карточке нажата",
              click_button(proc.pid, "Смотреть"))
        # У сериала два видео, поэтому «Смотреть» показывает то же окно в
        # контексте MANAGE — его заголовок «Файлы раздачи»
        opened = ui.wait_element(
            proc.pid, lambda e: (e.get("name") or "") == MANAGE_TITLE
            and e.get("w"), 30) is not None
        if not check("окно выбора открылось на «Смотреть»", opened):
            kill_players(player)
            close_app(proc, hwnd)
            return report()
        check("серия выделена во втором окне",
              pick_file(proc.pid, "s01e01.mkv"))
        btn = dialog_button(proc.pid, "Посмотреть", anchor=MANAGE_TITLE)
        if btn is not None:
            ui.click(proc.pid, *center(btn))
        check("«Посмотреть» во втором окне нажата", btn is not None)
        cmd2 = wait_player_cmd(player, "--start=", 90)
        print(f"  вторая командная строка mpv: {cmd2[:300]}")
        check("mpv получил ключ начальной позиции", "--start=" in cmd2)
        want = int((pos or 0) - 5)
        got = -1
        for part in cmd2.split():
            if part.startswith("--start="):
                try:
                    got = int(part.split("=", 1)[1])
                except ValueError:
                    got = -1
        check("позиция = сохранённая минус 5 с", abs(got - want) <= 1,
              f"--start={got}, сохранено {pos} с, ожидали {want}")

        # Докачка продолжилась именно этой серии
        end = time.monotonic() + 60
        size2 = size1
        while time.monotonic() < end:
            size2 = tree_size(wdir)
            if size2 > size1 + 2 * 1048576:
                break
            time.sleep(2.0)
        check("закачка продолжилась с того же места",
              size2 > size1 + 2 * 1048576,
              f"{size1 / 1048576:.1f} -> {size2 / 1048576:.1f} МБ")
        check("файлы по-прежнему только во временной папке",
              not dl_files(), str(dl_files()[:3]))
        grab(hwnd, os.path.join(SNAP, "tempwatch-resumed.png"))

        kill_players(player)
        time.sleep(2.0)
        secs = close_app(proc, hwnd)
        print(f"  закрытие окна: {secs:.2f} с")
        check("временная папка после закрытия на месте (до перезагрузки)",
              bool(watch_dirs()), str(watch_dirs()))
    finally:
        kill_players()
        seed_stop(seed)
    return report()


# ------------------------------------------------- шаг finish (2.7)

def step_finish(player=MPV):
    """Досмотр до конца: файл удалён, раздача убрана.

    Смотрим ВТОРУЮ серию — она 40 с (первая 180 с), и mpv доигрывает её
    сам и сам закрывается. Скорость сида не ограничиваем: файл должен
    успеть скачаться раньше, чем кончится показ, иначе mpv доиграет до
    места обрыва и «досмотрел» (позиция >= 95%) не наступит.
    Ускорять показ нельзя: при опросе раз в секунду конец короткого
    файла на скорости выше обычной между опросами теряется (находка 56).
    """
    print("== finish: досмотр до конца ==")
    if not os.path.isfile(player):
        print(f"  НЕТ ПЛЕЕРА: {player}")
        return 1
    prepare(torrent_player=player)
    kill_players()
    seed, info = seed_start(limit_kb=20000)
    ih = info["infohash"]
    try:
        proc, hwnd = start_app()
        ui.focus(hwnd)
        add_magnet(proc.pid, info["magnet"])
        check("окно выбора открылось", wait_choice(proc.pid, 90))
        check("вторая (короткая) серия выделена",
              pick_file(proc.pid, "s01e02.mkv"))
        check("«Посмотреть» нажата", answer_choice(proc.pid, "Посмотреть"))
        cmd = wait_player_cmd(player, "s01e02", 90)
        check("mpv запущен на второй серии", bool(cmd), cmd[:200])
        dirs = watch_dirs()
        check("временная папка создана", bool(dirs), str(dirs))
        wdir = os.path.join(WATCH_ROOT, dirs[0]) if dirs else WATCH_ROOT

        # Ждём, пока mpv сам закончит и выйдет (40 с показа + запас)
        print("  ждём конца показа (40 с файла)…", flush=True)
        end = time.monotonic() + 180
        while time.monotonic() < end:
            if not player_lines(player):
                break
            time.sleep(2.0)
        check("mpv доиграл файл и закрылся сам", not player_lines(player))
        time.sleep(8.0)                  # движку нужно время на перепроверку

        video = None
        for base, _dirs, files in os.walk(wdir):
            for name in files:
                if "s01e02" in name:
                    video = os.path.join(base, name)
        check("досмотренный файл удалён", video is None,
              str(video))
        # Своих данных у соседних файлов нет -> раздача уходит целиком
        end = time.monotonic() + 30
        while time.monotonic() < end and watch_dirs():
            time.sleep(1.0)
        check("временная папка раздачи убрана", not watch_dirs(),
              str(watch_dirs()))
        check("метка .watch убрана", ih + ".watch" not in watch_marks(),
              str(watch_marks()))
        check("карточка раздачи убрана",
              wait_gone(proc.pid, "Сериал про котиков", 20))
        check("в Библиотеку так и не попала", not torrent_history())
        grab(hwnd, os.path.join(SNAP, "finish-done.png"))
        close_app(proc, hwnd)
    finally:
        kill_players()
        seed_stop(seed)
    return report()


# ------------------------------------------------- шаг space (2.7)

def step_space(player=MPV):
    """Окно «На диске мало места» на установленной копии.

    Порог в коде — 110% НЕДОКАЧАННОГО, свободного места на C: сотни ГБ,
    поэтому запускаем установленную копию с TEMP на маленьком томе:
    временные раздачи живут под %TEMP%, и место движок спрашивает
    именно там. Путь к такому тому — в переменной окружения
    VD_SPACE_TEMP (например, смонтированный VHD на 200 МБ).
    """
    print("== space: окно «мало места» ==")
    small = os.environ.get("VD_SPACE_TEMP")
    if not small or not os.path.isdir(small):
        print("  НЕТ МАЛЕНЬКОГО ТОМА: задайте VD_SPACE_TEMP")
        return 1
    free = shutil.disk_usage(small).free
    print(f"  TEMP приложения: {small} (свободно "
          f"{free / 1048576:.0f} МБ)")
    if not os.path.isfile(player):
        print(f"  НЕТ ПЛЕЕРА: {player}")
        return 1
    prepare(torrent_player=player)
    kill_players()
    seed, info = seed_start(limit_kb=300)
    try:
        for pid, _parent, _cmd in ui.processes("VideoDownloader.exe"):
            ui.kill(pid)
        proc = ui.launch(temp_dir=small)
        hwnd = ui.main_window(proc.pid, 60)
        check("окно с подменённым TEMP открылось", hwnd is not None)
        if hwnd is None:
            ui.kill(proc.pid)
            return report()
        ui.wait_element(proc.pid, lambda e: e.get("name") == "Добавить", 30)
        ui.focus(hwnd)
        add_magnet(proc.pid, info["magnet"])
        edits = [e.get("value") for e in ui.elements(proc.pid)
                 if e.get("type") == "ControlType.Edit"]
        print(f"  в поле ссылки: {edits}")
        opened = wait_choice(proc.pid, 90)
        if not opened:
            print(f"  карточки: {card_text(proc.pid, 'Сериал')!r}")
            print(f"  подписи окна: {ui.names(proc.pid)[:40]}")
            grab(hwnd, os.path.join(SNAP, "space-no-choice.png"))
        if not check("окно выбора открылось", opened):
            close_app(proc, hwnd)
            return report()
        check("первая серия (135 МБ) выделена",
              pick_file(proc.pid, "s01e01.mkv"))
        check("«Посмотреть» нажата", answer_choice(proc.pid, "Посмотреть"))

        head = ui.wait_element(
            proc.pid, lambda e: (e.get("name") or "") == "На диске мало места"
            and e.get("w"), 30)
        check("показано окно «На диске мало места»", head is not None)
        grab(hwnd, os.path.join(SNAP, "space-dialog.png"))
        texts = [(e.get("name") or "") for e in ui.elements(proc.pid)
                 if "свободно" in (e.get("name") or "")]
        print(f"  текст окна: {texts}")
        save_state(space_text=texts)
        buttons = visible_buttons(proc.pid)
        print(f"  кнопки: {buttons}")
        check("кнопки «Всё равно смотреть» и «Отмена»",
              "Всё равно смотреть" in buttons and "Отмена" in buttons)

        cancel = dialog_button(proc.pid, "Отмена",
                               anchor="На диске мало места")
        check("«Отмена» найдена", cancel is not None)
        if cancel:
            ui.click(proc.pid, *center(cancel))
        time.sleep(5.0)
        check("после «Отмена» плеер не запущен", not player_lines(player),
              str(player_lines(player))[:200])
        close_app(proc, hwnd)
    finally:
        kill_players()
        seed_stop(seed)
    return report()


# ------------------------------------------- перезагрузка компьютера (2.7)

def step_reboot_arm(player=MPV):
    """Оставить временную и постоянную раздачу перед НАСТОЯЩЕЙ
    перезагрузкой (уборка идёт при старте движка, см. _cleanup_temp)."""
    print("== reboot-arm: готовим состояние к перезагрузке ==")
    if not os.path.isfile(player):
        print(f"  НЕТ ПЛЕЕРА: {player}")
        return 1
    prepare(torrent_player=player)
    kill_players()
    seed, info = seed_start(limit_kb=1200)
    ih = info["infohash"]
    try:
        proc, hwnd = start_app()
        ui.focus(hwnd)
        # временная: «Посмотреть» на первой серии
        add_magnet(proc.pid, info["magnet"])
        opened = wait_choice(proc.pid, 90)
        if not opened:
            print(f"  карточки: {card_text(proc.pid, 'Сериал')!r}")
            print(f"  подписи окна: {ui.names(proc.pid)[:40]}")
            grab(hwnd, os.path.join(SNAP, "reboot-arm-no-choice.png"))
        if not check("окно выбора открылось", opened):
            close_app(proc, hwnd)
            return report()
        check("первая серия выделена", pick_file(proc.pid, "s01e01.mkv"))
        check("«Посмотреть» нажата", answer_choice(proc.pid, "Посмотреть"))
        check("mpv запущен", bool(wait_player_cmd(player, "s01e0", 90)))
        time.sleep(25)
        kill_players(player)
        check("раздача на паузе", bool(wait_card(proc.pid, "Пауза", 30)))
        # Контроль: ПОСТОЯННАЯ раздача (мёртвый магнит — она остаётся
        # ждать список файлов) должна перезагрузку пережить. Добавляем
        # ПОСЛЕ временной: пока окно выбора не закрыто, оно модальное
        add_magnet(proc.pid, DEAD_MAGNET)
        wait_card(proc.pid, "Получаем список файлов", 30)
        time.sleep(2.0)
        # Контроль — только файлы МЁРТВОГО магнита. Файлы временной
        # раздачи сюда попадать не должны: её fastresume уборка удаляет
        # вместе с ней, и это правильно
        dead = DEAD_MAGNET.split(":")[-1]
        before = [n for n in resume_files() if n.startswith(dead)]
        check("контрольная постоянная раздача добавлена", bool(before),
              str(before))
        dirs = watch_dirs()
        check("временная папка с данными есть", bool(dirs), str(dirs))
        wdir = os.path.join(WATCH_ROOT, dirs[0]) if dirs else ""
        size = tree_size(wdir) if wdir else 0
        mark = read_watch(ih)
        secs = close_app(proc, hwnd)
        save_state(reboot={"watch_dir": wdir, "watch_mb": size / 1048576,
                           "infohash": ih, "last_active":
                           mark.get("last_active"),
                           "permanent": before,
                           "armed_at": time.time()})
        print(f"  временная папка: {wdir} ({size / 1048576:.1f} МБ)")
        print(f"  .watch last_active: {mark.get('last_active')}")
        print(f"  закрытие окна: {secs:.2f} с")
        print("\n  ЧТО СДЕЛАТЬ ВЛАДЕЛЬЦУ:")
        print("  1) выключить компьютер (Пуск -> Завершение работы) и "
              "включить снова;")
        print("  2) запустить Video Downloader и перейти на страницу "
              "«Торренты» (уборка идёт при старте движка);")
        print("  3) сказать мне — я запущу шаг reboot-check.")
    finally:
        kill_players()
        seed_stop(seed)
    return report()


def step_reboot_check():
    """После НАСТОЯЩЕЙ перезагрузки: временной раздачи нет."""
    print("== reboot-check: состояние после перезагрузки ==")
    st = state().get("reboot") or {}
    if not st:
        print("  сначала reboot-arm")
        return 1
    boot = ui.last_boot_events(3)
    print(f"  события загрузки Kernel-Boot 27: {boot}")
    newest = max([b["time"] for b in boot], default=0)
    check("после reboot-arm была настоящая загрузка системы",
          newest > (st.get("armed_at") or 0),
          f"загрузка {newest}, arm {st.get('armed_at')}")
    # До запуска программы уборки быть не должно: она идёт в start()
    check("до запуска программы данные ещё на месте (уборка не раньше)",
          os.path.isdir(st.get("watch_dir") or ""),
          st.get("watch_dir") or "")

    proc, hwnd = start_app()
    print("  программа запущена, ждём уборку…")
    end = time.monotonic() + 60
    while time.monotonic() < end and watch_dirs():
        time.sleep(1.0)
    time.sleep(2.0)
    check("временная папка раздачи удалена",
          not os.path.isdir(st.get("watch_dir") or ""),
          st.get("watch_dir") or "")
    check("корень временных раздач пуст", not watch_dirs(),
          str(watch_dirs()))
    check("метка .watch удалена",
          (st.get("infohash") or "") + ".watch" not in watch_marks(),
          str(watch_marks()))
    now = resume_files()
    check("ПОСТОЯННАЯ раздача перезагрузку пережила",
          bool(st.get("permanent"))
          and all(n in now for n in st["permanent"]),
          f"было {st.get('permanent')}, стало {now}")
    check("fastresume ВРЕМЕННОЙ раздачи убран вместе с ней",
          not any(n.startswith(st.get("infohash") or "нет") for n in now),
          str(now))
    check("карточки временной раздачи в окне нет",
          wait_gone(proc.pid, TEMP_TEXT, 10))
    grab(hwnd, os.path.join(SNAP, "reboot-after.png"))
    close_app(proc, hwnd)
    save_state(reboot_checked=True)
    return report()


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
    shutil.rmtree(WATCH_ROOT, ignore_errors=True)
    if os.path.isfile(ui.TORRENT_LOG):
        os.remove(ui.TORRENT_LOG)
    print(f"  тестовые torrents\\ и torrent.log удалены")
    print(f"  данные шагов остались в {BASE} (снимки: {SNAP})")
    return 0


STEPS = {
    "nometa": step_nometa,
    "dialog": step_dialog,
    "cancel": step_cancel,
    "readd": step_readd,
    "click": step_click,
    "tempwatch": step_tempwatch,
    "finish": step_finish,
    "space": step_space,
    "reboot-arm": step_reboot_arm,
    "reboot-check": step_reboot_check,
    "subs": step_subs,
    "library": step_library,
    "cleanup": step_cleanup,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in STEPS:
        print(f"Использование: {os.path.basename(__file__)} "
              f"<{'|'.join(STEPS)}>")
        sys.exit(2)
    sys.exit(STEPS[sys.argv[1]]())

# -*- coding: utf-8 -*-
"""Офлайн-тест торрент-движка (торрент-стриминг, сессия 1.2). Сеть —
только 127.0.0.1.

Своя маленькая раздача (3 файла, кириллица и пробелы в путях) раздаётся
вторым экземпляром libtorrent в этом же процессе; движок качает её по
magnet-ссылке (x.pe=127.0.0.1:порт) или по .torrent-файлу. Каждый сценарий
— отдельный экземпляр движка со своей папкой данных (удаление раздачи в
libtorrent асинхронно — повторное добавление той же раздачи в ту же
сессию давало бы гонки).
"""
import ctypes
import ctypes.wintypes as wt
import hashlib
import os
import shutil
import sys
import tempfile
import threading
import time
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import libtorrent as lt

import torrent_engine as te

PASS, FAIL = [], []


def check(name, ok, details=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS" if ok else "FAIL"), "|", name, "|", details, flush=True)


MB = 1024 * 1024
BASE = tempfile.mkdtemp(prefix="vd-engine-test-")
SRC_ROOT = os.path.join(BASE, "источник")
CONTENT = os.path.join(SRC_ROOT, "Тестовая раздача")
os.makedirs(CONTENT)
# Размеры НЕ кратны куску (256 КБ): иначе ни один кусок не пересекает
# границу выбранного/невыбранного файла и .parts при частичном выборе не
# появляется (проверено 16.09.2026: кратный — нет .parts, +12345 байт — есть)
SIZES = {"видео 1.bin": 6 * MB + 12345, "видео 2.bin": 3 * MB + 54321,
         "описание.txt": 20000}
for file_name, size in SIZES.items():
    with open(os.path.join(CONTENT, file_name), "wb") as f:
        f.write(os.urandom(size))

TORRENT = os.path.join(BASE, "test.torrent")
fs = lt.file_storage()
lt.add_files(fs, CONTENT)
ct = lt.create_torrent(fs, 256 * 1024, lt.create_torrent.v1_only)
lt.set_piece_hashes(ct, SRC_ROOT)
with open(TORRENT, "wb") as f:
    f.write(lt.bencode(ct.generate()))
TI = lt.torrent_info(TORRENT)
IH = te._hex(TI.info_hashes().v1)
FILE_ORDER = [TI.files().file_path(i) for i in range(TI.num_files())]

LOCAL = {"enable_dht": False, "enable_lsd": False, "enable_upnp": False,
         "enable_natpmp": False}


class Seed:
    def __init__(self):
        self.ses = lt.session(dict(LOCAL, listen_interfaces="127.0.0.1:0"))
        atp = lt.add_torrent_params()
        atp.ti = lt.torrent_info(TORRENT)
        atp.save_path = SRC_ROOT
        atp.flags = atp.flags | lt.torrent_flags.seed_mode
        self.h = self.ses.add_torrent(atp)
        end = time.monotonic() + 15
        while str(self.h.status().state) != "seeding" and time.monotonic() < end:
            time.sleep(0.1)
        self.port = self.ses.listen_port()

    def limit(self, bytes_per_s):
        self.h.set_upload_limit(bytes_per_s)


class Events:
    def __init__(self):
        self.lock = threading.Lock()
        self.changes = []
        self.list_changes = 0

    def on_change(self, item):
        with self.lock:
            self.changes.append((time.monotonic(), item.id, item.state))

    def on_list(self):
        with self.lock:
            self.list_changes += 1


def wait_for(pred, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(0.05)
    return pred()


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def same_as_source(save_path, rel):
    got = os.path.join(save_path, rel)
    return os.path.isfile(got) and sha(got) == sha(os.path.join(SRC_ROOT, rel))


def wait_same_as_source(save_path, rel, timeout=15):
    """Сверка с источником через опрос: «seeding» libtorrent означает, что
    все куски ПРОВЕРЕНЫ, а не что они уже дописаны на диск (запись идёт
    отдельными потоками; по той же причине shutdown() сбрасывает кэш перед
    fastresume). Мгновенная сверка ловила хвост последнего куска: на машине
    с 32 ядрами дисковых потоков больше и окно шире — 16.09.2026 около
    половины прогонов падали в проверках 1/3/5/6 (файлы догонялись за доли
    секунды). Для проверки «файл НЕ скачан» нужен same_as_source без опроса."""
    return wait_for(lambda: same_as_source(save_path, rel), timeout)


# min_reconnect_time только в тесте: после разрыва (пауза; обе стороны стали
# раздающими) libtorrent ждёт 60 с перед повторным подключением к ТОМУ ЖЕ
# пиру. У теста пир один и нет трекеров/DHT — пауза и set_files упирались
# в эти 60 с (проверено 16.09.2026: по умолчанию докачка на 58-60 с, с 1 с —
# за 1 с). В движке — значение по умолчанию: в реальной сети есть другие пиры.
ENGINE_SETTINGS = dict(LOCAL, min_reconnect_time=1)


def engine(name, events=None, **kwargs):
    events = events or Events()
    eng = te.TorrentEngine(
        data_dir=os.path.join(BASE, "данные", name),
        on_change=events.on_change, on_list_change=events.on_list,
        listen_interfaces="127.0.0.1:0", extra_settings=ENGINE_SETTINGS,
        **kwargs)
    return eng, events


def magnet():
    return f"magnet:?xt=urn:btih:{IH}&dn=test&x.pe=127.0.0.1:{seed.port}"


def state(eng, tid):
    item = eng.get(tid)
    return None if item is None else item.state


seed = Seed()
check("подготовка: сид раздаёт", str(seed.h.status().state) == "seeding",
      f"port {seed.port}")
ENGINES = []

# ---- 0: вспомогательное ----


class BrokenAlert:
    def message(self):
        raise UnicodeDecodeError("utf-8", b"\xd0", 0, 1, "unexpected end of data")


check("0 _alert_text: нечитаемый текст алерта не бросает исключение",
      te._alert_text(BrokenAlert()).startswith("<"))
not_started, _ = engine("not-started")
try:
    not_started.add_magnet(magnet(), BASE)
    raised = False
except RuntimeError:
    raised = True
check("0 добавление до start() — понятная ошибка RuntimeError", raised)

# ---- 1: magnet — метаданные, файлы, полное скачивание, fastresume ----
eng, ev = engine("s1")
ENGINES.append(eng)
eng.start()
save1 = os.path.join(BASE, "Загрузки с пробелом", "s1")
tid = eng.add_magnet(magnet(), save1)
check("1 id раздачи = v1 info-hash", tid == IH, tid)
check("1 добавление сообщает об изменении списка", ev.list_changes >= 1,
      str(ev.list_changes))
ok = wait_for(lambda: eng.get(tid).has_metadata, 20)
item = eng.get(tid)
check("1 метаданные получены", ok)
check("1 файлы раздачи: 3, с кириллицей, размеры верные",
      len(item.files) == 3
      and {os.path.basename(x.path): x.size for x in item.files} == SIZES,
      str([(x.path, x.size) for x in item.files]))
check("1 total_size = сумма файлов", item.total_size == sum(SIZES.values()),
      str(item.total_size))
ok = wait_for(lambda: state(eng, tid) == te.STATE_SEEDING, 30)
item = eng.get(tid)
check("1 скачано и раздаётся (раздача после скачивания по умолчанию)",
      ok and item.progress >= 1.0, f"{item.state} {item.progress:.3f}")
check("1 все файлы совпадают с источником",
      all(wait_same_as_source(save1, rel) for rel in FILE_ORDER))
check("1 fastresume записан",
      wait_for(lambda: os.path.isfile(eng._resume_path(tid)), 5))
check("1 колбэк on_change получил состояние seeding",
      any(t == tid and s == te.STATE_SEEDING for _, t, s in ev.changes),
      f"{len(ev.changes)} событий")

# ---- 2: повторное добавление не создаёт дубль ----
tid_again = eng.add_magnet(magnet(), save1)
check("2 повторное добавление той же раздачи — тот же id, без дубля",
      tid_again == tid and len(eng.items()) == 1, str(len(eng.items())))

# ---- 3: выбор файлов при добавлении, затем set_files ----
eng3, ev3 = engine("s3")
ENGINES.append(eng3)
eng3.start()
save3 = os.path.join(BASE, "Загрузки с пробелом", "s3")
tid3 = eng3.add_torrent_file(TORRENT, save3, file_priorities=[4, 0, 0][:len(FILE_ORDER)],
                             peers=[("127.0.0.1", seed.port)])
first, second = FILE_ORDER[0], FILE_ORDER[1]
ok = wait_for(lambda: state(eng3, tid3) == te.STATE_SEEDING, 30)
item3 = eng3.get(tid3)
first_size = SIZES[os.path.basename(first)]
check("3 выбран один файл: скачан только он",
      ok and wait_same_as_source(save3, first)
      and not same_as_source(save3, second),
      item3.state)
check("3 selected_size = размер выбранного файла; wanted_size — по целым кускам",
      item3.selected_size == first_size
      and first_size <= item3.wanted_size < first_size + TI.piece_length(),
      f"selected={item3.selected_size} wanted={item3.wanted_size} файл={first_size}")
check("3 приоритеты видны в списке файлов",
      [x.priority for x in item3.files][:2] == [4, 0], str([x.priority for x in item3.files]))
eng3.set_files(tid3, [4, 4, 0])
ok = wait_for(lambda: same_as_source(save3, second)
              and state(eng3, tid3) == te.STATE_SEEDING, 30)
check("3 set_files добавил второй файл — докачан и совпадает", ok)
parts = os.path.join(save3, f".{tid3}.parts")
check("3 при частичном выборе появился служебный .parts", os.path.isfile(parts))

# ---- 4: удаление вместе с файлами ----
changes_before = ev3.list_changes
eng3.remove(tid3, delete_files=True)
check("4 раздача убрана из списка, изменение списка сообщено",
      eng3.get(tid3) is None and ev3.list_changes > changes_before)
ok = wait_for(lambda: not os.path.exists(os.path.join(save3, first))
              and not os.path.exists(os.path.join(save3, second))
              and not os.path.exists(parts), 8)
check("4 файлы и .parts удалены", ok,
      str([p for p in (first, second) if os.path.exists(os.path.join(save3, p))]
          + ([".parts"] if os.path.exists(parts) else [])))
check("4 fastresume удалён", not os.path.exists(eng3._resume_path(tid3)))

# ---- 5: пауза посреди скачивания ----
seed.limit(int(1.5 * MB))
eng5, ev5 = engine("s5")
ENGINES.append(eng5)
eng5.start()
save5 = os.path.join(BASE, "Загрузки с пробелом", "s5")
tid5 = eng5.add_magnet(magnet(), save5)
ok = wait_for(lambda: eng5.get(tid5).progress >= 0.15, 30)
eng5.pause(tid5)
paused = wait_for(lambda: state(eng5, tid5) == te.STATE_PAUSED, 3)
done_at_pause = eng5.get(tid5).wanted_done
time.sleep(2.0)
done_later = eng5.get(tid5).wanted_done
check("5 пауза: состояние paused", ok and paused, state(eng5, tid5))
check("5 пауза: скачивание стоит (не больше одного куска в пути)",
      done_later - done_at_pause <= 256 * 1024, f"+{done_later - done_at_pause} байт")
eng5.resume(tid5)
check("5 после продолжения — снова downloading",
      wait_for(lambda: state(eng5, tid5) == te.STATE_DOWNLOADING, 5), state(eng5, tid5))
seed.limit(0)
ok = wait_for(lambda: state(eng5, tid5) == te.STATE_SEEDING, 40)
check("5 докачано, файлы совпадают",
      ok and all(wait_same_as_source(save5, rel) for rel in FILE_ORDER))

# ---- 6: перезапуск через fastresume ----
seed.limit(int(1.5 * MB))
data6_events = Events()
eng6, _ = engine("s6", events=data6_events)
eng6.start()
save6 = os.path.join(BASE, "Загрузки с пробелом", "s6")
tid6 = eng6.add_magnet(magnet(), save6)
ok = wait_for(lambda: eng6.get(tid6).progress >= 0.35, 30)
pieces_before = eng6._handles[tid6].status().num_pieces
result = eng6.shutdown(timeout=3.0)
check("6 shutdown: кэш сброшен, fastresume сохранён, уложился во время",
      ok and result["unsaved"] == 0 and result["unflushed"] == 0
      and result["saved"] == 1 and result["seconds"] < 4.0, str(result))
with open(eng6._resume_path(tid6), "rb") as f:
    pieces_saved = sum(1 for x in lt.read_resume_data(f.read()).have_pieces if x)
check("6 fastresume содержит все готовые куски (не устаревший)",
      pieces_saved >= pieces_before, f"до shutdown {pieces_before}, в fastresume {pieces_saved}")
count_after_shutdown = len(data6_events.changes)
time.sleep(1.0)
check("6 после shutdown колбэки не вызываются",
      len(data6_events.changes) == count_after_shutdown)
eng6b, ev6b = engine("s6")
ENGINES.append(eng6b)
eng6b.start()
check("6 после перезапуска раздача восстановлена (тот же id)",
      [x.id for x in eng6b.items()] == [tid6], str([x.id for x in eng6b.items()]))
ok = wait_for(lambda: state(eng6b, tid6) not in (te.STATE_CHECKING, te.STATE_METADATA), 10)
pieces_after = eng6b._handles[tid6].status().num_pieces
check("6 после перезапуска готовых кусков ровно столько, сколько сохранено",
      ok and pieces_after == pieces_saved and pieces_after >= pieces_before,
      f"до shutdown {pieces_before}, сохранено {pieces_saved}, после {pieces_after}")
eng6b.remove(tid6)                     # сид знает нового пира — докачка
eng6b.shutdown(timeout=1.0)
ENGINES.remove(eng6b)
seed.limit(0)
eng6c, _ = engine("s6")
ENGINES.append(eng6c)
eng6c.start()
tid6c = eng6c.add_magnet(magnet(), save6)
ok = wait_for(lambda: state(eng6c, tid6c) == te.STATE_SEEDING, 40)
check("6 докачка тех же файлов после перезапуска — совпадают с источником",
      ok and all(wait_same_as_source(save6, rel) for rel in FILE_ORDER))

# ---- 7: magnet без пиров ----
eng7, _ = engine("s7")
ENGINES.append(eng7)
eng7.start()
dead = hashlib.sha1(b"vd engine dead magnet").hexdigest()
tid7 = eng7.add_magnet(f"magnet:?xt=urn:btih:{dead}", os.path.join(BASE, "dead"))
time.sleep(3.0)
check("7 magnet без пиров ждёт метаданные", state(eng7, tid7) == te.STATE_METADATA,
      state(eng7, tid7))
check("7 fastresume записан и без метаданных",
      wait_for(lambda: os.path.isfile(eng7._resume_path(tid7)), 5))
eng7.remove(tid7)
check("7 удаление: из списка и fastresume",
      eng7.get(tid7) is None and not os.path.exists(eng7._resume_path(tid7)))

# ---- 8: раздача после скачивания выключена ----
eng8, _ = engine("s8", seed_after_download=False)
ENGINES.append(eng8)
eng8.start()
save8 = os.path.join(BASE, "Загрузки с пробелом", "s8")
tid8 = eng8.add_magnet(magnet(), save8)
ok = wait_for(lambda: state(eng8, tid8) == te.STATE_FINISHED, 40)
check("8 без раздачи: скачано и остановлено (finished)", ok, state(eng8, tid8))
eng8.set_seed_after_download(True)
check("8 включение раздачи возвращает seeding",
      wait_for(lambda: state(eng8, tid8) == te.STATE_SEEDING, 5), state(eng8, tid8))

# ---- 9: занятый файл -> ошибка -> retry ----
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID,
                            wt.DWORD, wt.DWORD, wt.HANDLE]
k32.CreateFileW.restype = wt.HANDLE
k32.CloseHandle.argtypes = [wt.HANDLE]
save9 = os.path.join(BASE, "Загрузки с пробелом", "s9")
target = os.path.join(save9, FILE_ORDER[0])
os.makedirs(os.path.dirname(target), exist_ok=True)
open(target, "wb").close()
lock = k32.CreateFileW(target, 0xC0000000, 0, None, 3, 0x80, None)
check("9 подготовка: файл занят (share=0)", lock not in (None, wt.HANDLE(-1).value))
eng9, _ = engine("s9")
ENGINES.append(eng9)
eng9.start()
tid9 = eng9.add_torrent_file(TORRENT, save9, peers=[("127.0.0.1", seed.port)])
ok = wait_for(lambda: state(eng9, tid9) == te.STATE_ERROR, 15)
item9 = eng9.get(tid9)
check("9 занятый файл — состояние error с текстом ошибки",
      ok and bool(item9.error), f"{item9.state}: {item9.error[:80]}")
k32.CloseHandle(lock)
eng9.retry(tid9)
ok = wait_for(lambda: state(eng9, tid9) == te.STATE_SEEDING, 40)
check("9 после освобождения retry() докачивает, файлы совпадают",
      ok and all(wait_same_as_source(save9, rel) for rel in FILE_ORDER),
      state(eng9, tid9))
# file_error_alert приходит пачкой (16-19 на один занятый файл) и часть
# разбирается уже ПОСЛЕ retry(): сохранённый текст сам по себе не должен
# держать состояние error, иначе докачанная раздача навсегда остаётся
# «сломанной» (реально ловилось на 32 ядрах, 16.09.2026). Признак ошибки —
# upload_mode у libtorrent, а не наличие текста.
with eng9._lock:
    eng9._errors[tid9] = ("file_open (отголосок уже исправленной)", first)
echo = eng9.get(tid9)
check("9 просроченный file_error после retry() не возвращает в error",
      echo.state == te.STATE_SEEDING and not echo.error,
      f"{echo.state}: {echo.error[:40]}")

# ---- 10: битый fastresume не ломает старт ----
bad_dir = os.path.join(BASE, "данные", "s10", "resume")
os.makedirs(bad_dir)
with open(os.path.join(bad_dir, "0" * 40 + te.RESUME_EXT), "wb") as f:
    f.write(b"this is not bencoded resume data")
eng10, _ = engine("s10")
ENGINES.append(eng10)
try:
    eng10.start()
    started = True
except Exception as exc:
    started = repr(exc)
check("10 битый fastresume: движок стартует, файл переименован в .corrupt",
      started is True and eng10.items() == []
      and os.path.isfile(os.path.join(bad_dir, "0" * 40 + te.RESUME_EXT + ".corrupt")),
      str(started))

# ---- 11: просмотр во время закачки (2.1) ----
# Главное здесь — побайтовая сверка с источником: в прототипе первая
# версия сервера отдавала плееру НУЛИ (находка 7 — have_piece() истинно
# сразу после проверки хэша, а запись ещё в очереди дискового потока).
# Поэтому файл берём недокачанным и сверяем именно те куски, которых
# на диске ещё нет.
eng11, ev11 = engine("s11")
ENGINES.append(eng11)
eng11.start()
save11 = os.path.join(BASE, "просмотр")
seed.limit(256 * 1024)               # чтобы файл не скачался мгновенно
VIDEO = "видео 1.bin"                # самый большой файл раздачи
VIDEO_INDEX = next(i for i, p in enumerate(FILE_ORDER)
                   if os.path.basename(p) == VIDEO)
VIDEO_SIZE = SIZES[VIDEO]
VIDEO_REL = FILE_ORDER[VIDEO_INDEX]
with open(os.path.join(SRC_ROOT, VIDEO_REL), "rb") as f:
    VIDEO_BYTES = f.read()

te.PIECE_WAIT_TIMEOUT = 30           # в тесте не ждём боевые 120 с
tid11 = eng11.add_torrent_file(TORRENT, save11,
                               peers=[("127.0.0.1", seed.port)])
check("11 метаданные раздачи получены",
      wait_for(lambda: eng11.get(tid11).has_metadata, 20))

# Смотреть можно и файл, с которого снята галочка: просмотр его включает.
# Остальные файлы оставляем выбранными — раздача со ВСЕМИ снятыми сразу
# становится завершённой, и сид отключается как от такого же раздающего
# (реконнект занял 15 с в диагностике; из GUI это состояние недостижимо —
# «Применить» с пустым выбором заблокировано, находка сессии 1.3)
off = [4] * len(FILE_ORDER)
off[VIDEO_INDEX] = 0
eng11.set_files(tid11, off)
wait_for(lambda: eng11.get(tid11).files[VIDEO_INDEX].priority == 0, 10)
try:
    eng11.open_stream(tid11, 99)
    bad_index = "нет ошибки"
except IndexError as exc:
    bad_index = ""
except Exception as exc:
    bad_index = repr(exc)
check("11 open_stream с несуществующим файлом -> IndexError", not bad_index,
      bad_index)

stream = eng11.open_stream(tid11, VIDEO_INDEX)
check("11 поток открыт: имя, размер, окно чтения",
      stream.name == VIDEO and stream.size == VIDEO_SIZE
      and te.READAHEAD_MIN_PIECES <= stream.readahead <= te.READAHEAD_MAX_PIECES,
      f"{stream.name}, {stream.size} байт, окно {stream.readahead} кусков")
check("11 файл со снятой галочкой включается в закачку",
      wait_for(lambda: eng11.get(tid11).files[VIDEO_INDEX].priority > 0, 10),
      str(eng11.get(tid11).files[VIDEO_INDEX].priority))

# Начало файла: плеер всегда просит его первым
rid = stream.open_request()
head_bytes = b"".join(bytes(chunk)
                      for chunk in stream.iter_range(rid, 0, 300000))
stream.close_request(rid)
check("11 начало файла совпадает с источником байт в байт",
      head_bytes == VIDEO_BYTES[:300001],
      f"{len(head_bytes)} байт, нулей {head_bytes.count(0)}")

# Голова и хвост — срочные, и ОСТАЮТСЯ такими: prioritize_files
# применяется отложенно и переписывает приоритеты кусков приоритетом
# файла (диагностика 16.09.2026), поэтому проверяем и через секунду
raised_ok = wait_for(lambda: bool(stream._raised), 10)


def urgent_pending():
    """Приоритеты ещё НЕ скачанных кусков головы и хвоста. У скачанного
    куска приоритет уже ничего не значит — libtorrent убирает его из
    очереди и оставляет там что угодно."""
    handle = eng11._handle(tid11)
    return [int(handle.piece_priority(piece)) for piece in stream._raised
            if not handle.have_piece(piece)]


urgent_now = urgent_pending()
time.sleep(1.0)
urgent_later = urgent_pending()
check("11 голова и хвост файла — срочные (moov/cues) и не перетираются",
      raised_ok and urgent_now
      and all(p == te.PIECE_PRIORITY_URGENT for p in urgent_now)
      and all(p == te.PIECE_PRIORITY_URGENT for p in urgent_later),
      f"кусков поднято {len(stream._raised)}, ждут {len(urgent_now)}: "
      f"{urgent_now[:4]}, через 1 с {urgent_later[:4]}")

# После закрытия запроса куски окна НЕ должны падать в приоритет 1:
# reset_piece_deadline ставит именно 1, то есть самые нужные куски
# оказались бы в конце очереди (диагностика 16.09.2026)
rid = stream.open_request()
stream._request_window(rid, 0)
window_pieces = sorted(stream._windows[rid])[:8]
stream.close_request(rid)
after_reset = [int(eng11._handle(tid11).piece_priority(piece))
               for piece in window_pieces
               if not eng11._handle(tid11).have_piece(piece)]
check("11 после снятия дедлайна приоритет куска не падает ниже обычного",
      all(p >= te.STREAM_PRIORITY for p in after_reset),
      f"{after_reset} (обычный {te.STREAM_PRIORITY})")

# Кусок из середины, которого на диске ещё нет — сервер обязан ЖДАТЬ
middle = VIDEO_SIZE // 2
done_before = eng11.file_progress(tid11)[VIDEO_INDEX]
rid = stream.open_request()
t_wait = time.monotonic()
mid_bytes = b"".join(bytes(chunk)
                     for chunk in stream.iter_range(rid, middle,
                                                    middle + 200000))
wait_s = time.monotonic() - t_wait
stream.close_request(rid)
check("11 середина недокачанного файла: дождались и совпало",
      mid_bytes == VIDEO_BYTES[middle:middle + 200001],
      f"{len(mid_bytes)} байт за {wait_s:.2f} с, скачано было "
      f"{done_before} из {VIDEO_SIZE}, ожиданий {stream.waits}")
check("11 данные брались через read_piece, а не с диска",
      stream.piece_reads > 0, f"read_piece: {stream.piece_reads}")

# Хвост файла (там moov у MP4) — и дедлайны снимаются вместе с запросом
rid = stream.open_request()
tail_bytes = b"".join(bytes(chunk)
                      for chunk in stream.iter_range(rid, VIDEO_SIZE - 50000,
                                                     VIDEO_SIZE - 1))
stream.close_request(rid)
check("11 хвост файла совпадает с источником",
      tail_bytes == VIDEO_BYTES[-50000:], f"{len(tail_bytes)} байт")
check("11 после close_request окон не осталось", not stream._windows,
      str(stream._windows))

# Закрытие потока: приоритеты кусков возвращаются к приоритету файла
raised_pieces = list(stream._raised)
eng11.close_stream(tid11, VIDEO_INDEX)
check("11 close_stream: поток закрыт и убран из движка",
      stream.closed and not eng11.streams(), str(eng11.streams()))
check("11 приоритеты головы/хвоста вернулись к обычным",
      all(int(eng11._handle(tid11).piece_priority(piece))
          == te.STREAM_PRIORITY for piece in raised_pieces),
      str([int(eng11._handle(tid11).piece_priority(p))
           for p in raised_pieces[:4]]))

# Ожидающий чтение запрос обязан выйти сразу — от этого зависит закрытие
# окна во время просмотра (иначе оно ждало бы PIECE_WAIT_TIMEOUT)
stream2 = eng11.open_stream(tid11, VIDEO_INDEX)
eng11.pause(tid11)                   # куски больше не придут
far = max(0, VIDEO_SIZE - 300000)
holder = {}


def read_far():
    rid2 = stream2.open_request()
    try:
        holder["bytes"] = sum(len(chunk) for chunk in
                              stream2.iter_range(rid2, far, VIDEO_SIZE - 1))
    finally:
        stream2.close_request(rid2)
        holder["done"] = True


reader = threading.Thread(target=read_far, daemon=True)
reader.start()
time.sleep(1.0)
t_close = time.monotonic()
eng11.close_stream(tid11)
reader.join(5)
close_s = time.monotonic() - t_close
check("11 close_stream будит ожидающее чтение (закрытие окна)",
      holder.get("done") and close_s < 2.0,
      f"{close_s:.2f} с, прочитано {holder.get('bytes')} байт")

# Выбор файлов важнее просмотра: сняли галочку — поток закрылся
eng11.resume(tid11)
stream3 = eng11.open_stream(tid11, VIDEO_INDEX)
priorities = [0] * len(FILE_ORDER)
priorities[(VIDEO_INDEX + 1) % len(FILE_ORDER)] = 4
eng11.set_files(tid11, priorities)
check("11 set_files со снятой галочкой закрывает просмотр",
      stream3.closed and not eng11.streams(), str(eng11.streams()))

# Удаление раздачи во время просмотра
eng11.set_files(tid11, [4] * len(FILE_ORDER))
stream4 = eng11.open_stream(tid11, VIDEO_INDEX)
eng11.remove(tid11)
check("11 remove закрывает просмотр (handle становится невалидным)",
      stream4.closed, "")

# shutdown при активном просмотре укладывается в бюджет
eng12, _ = engine("s12")
eng12.start()
save12 = os.path.join(BASE, "просмотр-2")
tid12 = eng12.add_torrent_file(TORRENT, save12,
                               peers=[("127.0.0.1", seed.port)])
wait_for(lambda: eng12.get(tid12).has_metadata, 20)
stream5 = eng12.open_stream(tid12, VIDEO_INDEX)
holder2 = {}


def read_tail():
    rid3 = stream5.open_request()
    try:
        holder2["bytes"] = sum(len(chunk) for chunk in
                               stream5.iter_range(rid3, far, VIDEO_SIZE - 1))
    finally:
        holder2["done"] = True


reader2 = threading.Thread(target=read_tail, daemon=True)
reader2.start()
time.sleep(0.5)
result12 = eng12.shutdown(timeout=3.0)
reader2.join(5)
check("11 shutdown во время просмотра: в бюджете и fastresume сохранён",
      result12["seconds"] < 3.0 and result12["unsaved"] == 0
      and stream5.closed and holder2.get("done"),
      f"{result12}, чтение завершилось={holder2.get('done')}")
seed.limit(0)

# ---- завершение ----
for eng_ in ENGINES:
    try:
        eng_.shutdown(timeout=2.0)
    except Exception as exc:
        check("завершение движка без исключений", False, repr(exc))
del seed
shutil.rmtree(BASE, ignore_errors=True)

print()
print(f"ИТОГ: {len(PASS)} PASS, {len(FAIL)} FAIL")
sys.exit(1 if FAIL else 0)

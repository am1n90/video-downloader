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
import json
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

# ---- 7b: shutdown при раздаче БЕЗ метаданных рядом с обычной (2.4) ----
# Находка 42: flush_cache() у раздачи без метаданных не отвечает никогда,
# ожидание съедало весь timeout (закрытие окна 3 с на установленной копии),
# и save_resume_data соседней качающейся раздачи уже не дожидались.
seed.limit(int(1.5 * MB))
eng7b, _ = engine("s7b")
eng7b.start()
tid7b_dead = eng7b.add_magnet(f"magnet:?xt=urn:btih:{dead}",
                              os.path.join(BASE, "dead"))
tid7b = eng7b.add_magnet(magnet(), os.path.join(BASE, "Загрузки с пробелом", "s7b"))
ok = wait_for(lambda: eng7b.get(tid7b).progress >= 0.2, 30)
result7b = eng7b.shutdown(timeout=3.0)
check("7b shutdown с раздачей без метаданных: обе сохранены, без упора в timeout",
      ok and result7b["unflushed"] == 0 and result7b["unsaved"] == 0
      and result7b["saved"] == 2 and result7b["seconds"] < 2.5, str(result7b))
seed.limit(0)

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

# ---- 12: focus_file — качаем только то, что смотрят (2.5) ----
# Замер 17.09.2026 на сериале из 5 серий: «Смотреть» поднимал лишь куски
# окна просмотра, а качались ВСЕ серии сразу (серия 2 дошла до 100%
# раньше, чем досмотрели серию 3). Правило владельца: остальным — 0,
# кроме заказанных галочками в диалоге «Файлы».
seed.limit(200 * 1024)
eng12b, _ = engine("s12b")
ENGINES.append(eng12b)
eng12b.start()
save12b = os.path.join(BASE, "Загрузки с пробелом", "s12b")
tid12b = eng12b.add_torrent_file(TORRENT, save12b,
                                 peers=[("127.0.0.1", seed.port)])
wait_for(lambda: eng12b.get(tid12b).has_metadata, 20)
I_BIG = FILE_ORDER.index("Тестовая раздача\видео 1.bin")
I_SMALL = FILE_ORDER.index("Тестовая раздача\видео 2.bin")
I_TXT = FILE_ORDER.index("Тестовая раздача\описание.txt")


def prios12():
    return [int(p) for p in eng12b._handles[tid12b].get_file_priorities()]


check("12 по умолчанию качаются ВСЕ файлы раздачи",
      prios12() == [4] * len(FILE_ORDER), str(prios12()))

changed = eng12b.focus_file(tid12b, I_SMALL)
applied = wait_for(lambda: prios12()[I_BIG] == 0, 5)   # prioritize_files асинхронный
check("12 focus_file: приоритет остался только у выбранного файла",
      changed and applied and prios12()[I_SMALL] == te.STREAM_PRIORITY
      and all(p == 0 for i, p in enumerate(prios12()) if i != I_SMALL),
      str(prios12()))
check("12 повторный focus_file на тот же файл ничего не меняет",
      eng12b.focus_file(tid12b, I_SMALL) is False)
check("12 снимок для GUI показывает те же приоритеты",
      [f.priority for f in eng12b.get(tid12b).files] == prios12(),
      str([f.priority for f in eng12b.get(tid12b).files]))

# Файл с открытым потоком (так приходят субтитры-спутники) не занижаем
eng12b.set_files(tid12b, [4] * len(FILE_ORDER))
wait_for(lambda: prios12() == [4] * len(FILE_ORDER), 5)
stream12 = eng12b.open_stream(tid12b, I_BIG)
eng12b.focus_file(tid12b, I_SMALL)
wait_for(lambda: prios12()[I_TXT] == 0, 5)
check("12 файл с открытым потоком (субтитры) сохраняет приоритет",
      prios12()[I_BIG] > 0 and prios12()[I_SMALL] == te.STREAM_PRIORITY
      and prios12()[I_TXT] == 0, str(prios12()))
eng12b.close_stream(tid12b)

# Явный заказ: снятая галочка в диалоге «Файлы» переживает просмотр
order = [4] * len(FILE_ORDER)
order[I_TXT] = 0
eng12b.set_files(tid12b, order)
wait_for(lambda: prios12()[I_TXT] == 0, 5)
check("12 снятая галочка запомнена как явный заказ",
      eng12b._chosen.get(tid12b) == frozenset({I_BIG, I_SMALL})
      and os.path.isfile(eng12b._chosen_path(tid12b)),
      str(eng12b._chosen.get(tid12b)))
eng12b.focus_file(tid12b, I_SMALL)
wait_for(lambda: prios12()[I_SMALL] == te.STREAM_PRIORITY, 5)
check("12 заказанный галочкой файл focus_file не занижает",
      prios12()[I_BIG] == 4 and prios12()[I_TXT] == 0, str(prios12()))

# «Применить» без единой снятой галочки заказом не считается
eng12b.set_files(tid12b, [4] * len(FILE_ORDER))
check("12 «Применить» со всеми галочками заказ отменяет",
      tid12b not in eng12b._chosen
      and not os.path.exists(eng12b._chosen_path(tid12b)))
wait_for(lambda: prios12() == [4] * len(FILE_ORDER), 5)
eng12b.focus_file(tid12b, I_SMALL)
applied = wait_for(lambda: prios12()[I_BIG] == 0, 5)
check("12 после отмены заказа просмотр снова занижает всё остальное",
      applied and prios12()[I_SMALL] == te.STREAM_PRIORITY, str(prios12()))

# Заказ должен пережить перезапуск программы: libtorrent его не хранит
eng12b.set_files(tid12b, order)
wait_for(lambda: prios12()[I_TXT] == 0, 5)
eng12b.shutdown(timeout=3.0)
ENGINES.remove(eng12b)
eng12c, _ = engine("s12b")
ENGINES.append(eng12c)
eng12c.start()
check("12 явный заказ восстановлен из <id>.chosen после перезапуска",
      eng12c._chosen.get(tid12b) == frozenset({I_BIG, I_SMALL}),
      str(eng12c._chosen.get(tid12b)))
wait_for(lambda: eng12c.get(tid12b) is not None
         and eng12c.get(tid12b).has_metadata, 20)
eng12c.focus_file(tid12b, I_SMALL)
after12 = [int(p) for p in eng12c._handles[tid12b].get_file_priorities()]
check("12 после перезапуска заказ по-прежнему бережётся",
      after12[I_BIG] == 4 and after12[I_TXT] == 0, str(after12))
eng12c.remove(tid12b)
check("12 удаление раздачи убирает и файл заказа",
      not os.path.exists(eng12c._chosen_path(tid12b)))
seed.limit(0)

# ---- 13: отложенное добавление — выбор ДО закачки (2.6) ----
# До 2.6 раздача начинала качать всё сразу после «Добавить». Теперь
# add_*(defer=True) держит её флагом upload_mode — «данных не просить»:
# рой собирается, метаданные приходят, но не качается ни байта (замер
# трёх способов — в docstring _add). Сид без лимита: не сработай
# удержание, раздача (9 МБ) скачалась бы за секунды.
seed.limit(0)
PR_ON = 4              # обычный приоритет файла (галочка «скачать»)
eng13, ev13 = engine("s13")
ENGINES.append(eng13)
eng13.start()
save13 = os.path.join(BASE, "Загрузки с пробелом", "s13")
tid13 = eng13.add_torrent_file(TORRENT, save13,
                               peers=[("127.0.0.1", seed.port)], defer=True)


def prios13(eng=None):
    eng = eng or eng13
    return [int(p) for p in eng._handles[tid13].get_file_priorities()]


def done13(eng=None):
    eng = eng or eng13
    return [int(b) for b in eng.file_progress(tid13)]


check("13 .torrent с defer: раздача добавлена с «данных не просить»",
      bool(int(eng13._handles[tid13].status().flags)
           & int(lt.torrent_flags.upload_mode)))
check("13 приоритеты файлов остались настоящими (не нули)",
      prios13() == [4] * len(FILE_ORDER), str(prios13()))
check("13 раздача помечена ждущей выбора (память и файл рядом с resume)",
      eng13.is_pending(tid13) and os.path.isfile(eng13._pending_path(tid13)))
check("13 снимок для GUI — состояние «ожидает выбора», а не ошибка",
      state(eng13, tid13) == te.STATE_PENDING, str(state(eng13, tid13)))
time.sleep(3)
st13 = eng13._handles[tid13].status()
check("13 за 3 с ожидания не скачано ни байта",
      int(st13.total_done) == 0 and int(st13.all_time_download) == 0,
      f"total_done={int(st13.total_done)}, "
      f"all_time={int(st13.all_time_download)}, пиров {st13.num_peers}")
check("13 файлов раздачи на диске не появилось",
      not os.path.isdir(os.path.join(save13, "Тестовая раздача")),
      str(os.listdir(save13) if os.path.isdir(save13) else []))

# Ожидание должно пережить перезапуск программы: иначе после запуска
# раздача выглядела бы просто паузой со снятыми галочками
eng13.shutdown(timeout=3.0)
ENGINES.remove(eng13)
eng13b, _ = engine("s13")
ENGINES.append(eng13b)
eng13b.start()
check("13 после перезапуска раздача по-прежнему ждёт выбора",
      eng13b.is_pending(tid13)
      and state(eng13b, tid13) == te.STATE_PENDING,
      str(state(eng13b, tid13)))

# «Посмотреть»: begin_download без приоритетов + focus_file — качается
# СТРОГО выбранный файл, галочки в явный заказ не попадают
# Пир задаётся заново: в новом сеансе движка соединений ещё нет, в
# fastresume подключённых пиров не оказалось, а трекеров и DHT в тесте
# нет (в жизни адреса дают трекер, magnet-ссылка и DHT)
eng13b._handles[tid13].connect_peer(("127.0.0.1", seed.port))
eng13b.begin_download(tid13, focus=I_SMALL)
check("13 begin_download снял метку ожидания и «данных не просить»",
      not eng13b.is_pending(tid13)
      and not os.path.exists(eng13b._pending_path(tid13))
      and not (int(eng13b._handles[tid13].status().flags)
               & int(lt.torrent_flags.upload_mode)))
check("13 «Посмотреть» не записывает явный заказ файлов",
      tid13 not in eng13b._chosen
      and not os.path.exists(eng13b._chosen_path(tid13)))
wait_for(lambda: prios13(eng13b)[I_SMALL] == te.STREAM_PRIORITY, 5)
check("13 после «Посмотреть» приоритет только у выбранного файла",
      prios13(eng13b)[I_SMALL] == te.STREAM_PRIORITY
      and all(p == 0 for i, p in enumerate(prios13(eng13b))
              if i != I_SMALL), str(prios13(eng13b)))

# Вопрос владельца 17.09.2026: когда выбранная серия докачана и раздача
# перешла в «Раздаётся», не потянет ли она соседние файлы. Замер, а не
# вывод из кода: ждём 100%, потом 10 с наблюдаем за соседями.
finished13 = wait_for(
    lambda: done13(eng13b)[I_SMALL] >= SIZES["видео 2.bin"], 60)
seeding13 = wait_for(lambda: state(eng13b, tid13) == te.STATE_SEEDING, 10)
before13 = done13(eng13b)
time.sleep(10)
after13 = done13(eng13b)
check("13 выбранный файл докачан целиком",
      finished13 and after13[I_SMALL] >= SIZES["видео 2.bin"],
      f"{after13[I_SMALL]} из {SIZES['видео 2.bin']}")
check("13 после 100% раздача перешла в «Раздаётся»",
      seeding13, str(state(eng13b, tid13)))
check("13 за 10 с раздачи соседние файлы не сдвинулись ни на байт",
      [b for i, b in enumerate(after13) if i != I_SMALL]
      == [b for i, b in enumerate(before13) if i != I_SMALL],
      f"было {before13} -> стало {after13}")
check("13 приоритеты соседей так и остались нулевыми",
      all(p == 0 for i, p in enumerate(prios13(eng13b)) if i != I_SMALL),
      str(prios13(eng13b)))

# «Скачать»: то же окно, но с галочками — качается всё отмеченное
eng13b.begin_download(tid13, [PR_ON if i != I_TXT else 0
                              for i in range(len(FILE_ORDER))])
got13 = wait_same_as_source(save13, "Тестовая раздача\\видео 1.bin", 60)
check("13 «Скачать» качает отмеченное галочками",
      got13 and prios13(eng13b)[I_BIG] == PR_ON, str(prios13(eng13b)))
check("13 снятая галочка и здесь остаётся явным заказом",
      eng13b._chosen.get(tid13) == frozenset({I_BIG, I_SMALL}),
      str(eng13b._chosen.get(tid13)))

# «Отмена»: раздачи не остаётся ни в движке, ни на диске — вместе с
# кусками, успевшими прийти, пока шли метаданные magnet-ссылки
eng13c, _ = engine("s13c")
ENGINES.append(eng13c)
eng13c.start()
save13c = os.path.join(BASE, "Загрузки с пробелом", "s13c")
tid13c = eng13c.add_magnet(magnet(), save13c, defer=True)
meta13 = wait_for(lambda: eng13c.get(tid13c).has_metadata, 30)
check("13 magnet: метаданные пришли, раздача ждёт выбора",
      meta13 and eng13c.is_pending(tid13c)
      and state(eng13c, tid13c) == te.STATE_PENDING,
      str(state(eng13c, tid13c)))
grabbed = int(eng13c._handles[tid13c].status().total_done)
time.sleep(3)
check("13 magnet: за 3 с после метаданных ничего не прибавилось",
      int(eng13c._handles[tid13c].status().total_done) == grabbed,
      f"{grabbed} байт успело прийти в момент метаданных "
      f"(проскок, см. _add)")
eng13c.remove(tid13c, delete_files=True)
gone13 = wait_for(
    lambda: not os.path.isdir(os.path.join(save13c, "Тестовая раздача")), 10)
check("13 «Отмена» убирает раздачу вместе с тем, что успело скачаться",
      gone13 and eng13c.get(tid13c) is None
      and not os.path.exists(eng13c._pending_path(tid13c)),
      str(os.listdir(save13c) if os.path.isdir(save13c) else []))
seed.limit(0)

# ---- 14: временная раздача «Посмотреть» ----
# Режим задаётся при первом ответе (begin_download(temporary=True)):
# своя папка под watch_root, после закрытия плеера — пауза, не раздаётся,
# досмотренный файл удаляется (finish_file), после перезагрузки
# компьютера раздача удаляется целиком (_cleanup_temp). Время загрузки
# подменяем: настоящая перезагрузка в тесте невозможна.
BOOT = {"at": 1.0}          # «компьютер загрузился» давно — ничего не чистим


def watch_root(name):
    """Свой корень временных папок на каждый движок теста.

    Общий корень на всех — ловушка именно теста: раздача одна и та же,
    временная папка у неё одна и та же, а уборку удалённой раздачи
    движок отменяет только у себя (_claim_dir). В программе движок один
    на процесс, так что столкнуться там нечему; в тесте же движки
    живут одновременно, и уборка одного стирала файлы другого.
    """
    return os.path.join(BASE, "временные просмотры", name)


def temp_engine(name):
    return engine(name, watch_root=watch_root(name),
                  boot_time=lambda: BOOT["at"])


def upload_mode(eng, tid):
    return bool(int(eng._handles[tid].status().flags)
                & int(lt.torrent_flags.upload_mode))


eng14, _ = temp_engine("s14")
ENGINES.append(eng14)
eng14.start()
downloads14 = os.path.join(BASE, "Загрузки с пробелом", "s14")
tid14 = eng14.add_torrent_file(TORRENT, downloads14,
                               peers=[("127.0.0.1", seed.port)], defer=True)
seed.limit(400 * 1024)
eng14.begin_download(tid14, focus=I_SMALL, temporary=True)
dir14 = eng14.watch_dir(tid14)
check("14 «Посмотреть»: раздача временная, метка рядом с fastresume",
      eng14.is_temp(tid14) and eng14.get(tid14).temp
      and os.path.isfile(eng14._watch_path(tid14)))
moved14 = wait_for(lambda: os.path.normcase(eng14.get(tid14).save_path)
                   == os.path.normcase(dir14), 10)
check("14 путь сохранения — своя папка во временном корне",
      moved14 and dir14.startswith(watch_root("s14")),
      eng14.get(tid14).save_path)
got14 = wait_for(lambda: eng14.file_progress(tid14)[I_SMALL] > 256 * 1024, 30)
check("14 качается во временную папку, в загрузки — ни файла",
      got14 and os.path.isdir(os.path.join(dir14, "Тестовая раздача"))
      and not os.path.isdir(os.path.join(downloads14, "Тестовая раздача")),
      str(os.listdir(downloads14) if os.path.isdir(downloads14) else []))

# Плеер закрыли на середине: пауза, данные остаются
eng14.set_watch_position(tid14, I_SMALL, 42.5)
eng14.stop_temp(tid14)
wait_for(lambda: state(eng14, tid14) == te.STATE_PAUSED, 5)
done_at_stop = int(eng14._handles[tid14].status().all_time_download)
time.sleep(2)
check("14 stop_temp: пауза, за 2 с не скачано ни байта",
      state(eng14, tid14) == te.STATE_PAUSED
      and int(eng14._handles[tid14].status().all_time_download)
      == done_at_stop, str(state(eng14, tid14)))
check("14 позиция просмотра запомнена", eng14.watch_position(tid14, I_SMALL)
      == 42.5, str(eng14.watch_position(tid14, I_SMALL)))

# Перезапуск программы без перезагрузки: на паузе, позиция на месте
eng14.shutdown(timeout=3.0)
ENGINES.remove(eng14)
eng14b, _ = temp_engine("s14")
ENGINES.append(eng14b)
eng14b.start()
check("14 после перезапуска: временная, на паузе, позиция на месте",
      eng14b.is_temp(tid14)
      and wait_for(lambda: state(eng14b, tid14) == te.STATE_PAUSED, 10)
      and eng14b.watch_position(tid14, I_SMALL) == 42.5,
      f"{state(eng14b, tid14)}, {eng14b.watch_position(tid14, I_SMALL)}")

# «Смотреть» снова: open_stream снимает паузу, докачка с того же места
seed.limit(0)
eng14b._handles[tid14].connect_peer(("127.0.0.1", seed.port))
before14 = eng14b.file_progress(tid14)[I_SMALL]
stream14 = eng14b.open_stream(tid14, I_SMALL)
full14 = wait_for(lambda: eng14b.file_progress(tid14)[I_SMALL]
                  >= SIZES["видео 2.bin"], 60)
check("14 повторный просмотр — докачка, а не с нуля",
      full14 and before14 > 0, f"было {before14} байт до повторного просмотра")
time.sleep(1.5)
check("14 докачано, пока смотрят: раздача не на паузе (отдаёт плееру)",
      state(eng14b, tid14) == te.STATE_SEEDING, str(state(eng14b, tid14)))
eng14b.set_seed_after_download(False)
eng14b.set_seed_after_download(True)
eng14b.stop_temp(tid14)
wait_for(lambda: state(eng14b, tid14) == te.STATE_FINISHED, 5)
eng14b.set_seed_after_download(True)
time.sleep(1)
check("14 закрыли плеер — «Скачано», не «Раздаётся», "
      "даже при включённой раздаче",
      state(eng14b, tid14) == te.STATE_FINISHED and stream14.closed,
      str(state(eng14b, tid14)))

# Досмотрели фильм (других видео с данными нет) — раздача целиком прочь
res14 = eng14b.finish_file(tid14, I_SMALL, keep_alive=[I_BIG])
gone14 = wait_for(lambda: not os.path.exists(dir14), 10)
check("14 досмотрели единственное видео: раздача и папка удалены",
      res14 == "removed" and eng14b.get(tid14) is None and gone14
      and not os.path.exists(eng14b._watch_path(tid14))
      and not os.path.exists(eng14b._resume_path(tid14)),
      f"{res14}, папка {'есть' if os.path.exists(dir14) else 'нет'}")

# Сериал: досмотрели одну серию, у другой есть свои данные — удаляется
# только файл, раздача остаётся на паузе. Порядок finish_file выведен
# замером (docstring): без upload_mode перепроверка качает файл заново,
# на паузе — не идёт вовсе.
eng14c, _ = temp_engine("s14c")
ENGINES.append(eng14c)
eng14c.start()
tid14c = eng14c.add_torrent_file(TORRENT, downloads14,
                                 peers=[("127.0.0.1", seed.port)], defer=True)
# Сначала начали смотреть серию 1 и закрыли на середине, потом досмотрели
# серию 2. Порядок нарочно такой, чтобы раздача не проходила через
# «завершена»: у завершённой раздачи единственный пир теста отключается,
# и вернуться к нему libtorrent готов только через ~58 с (замер 17.09.2026
# — та же минута, что в находках 18 и 31; в рое есть трекер и другие пиры)
seed.limit(300 * 1024)
eng14c.begin_download(tid14c, focus=I_BIG, temporary=True)
eng14c.open_stream(tid14c, I_BIG)
big_part = wait_for(lambda: eng14c.file_progress(tid14c)[I_BIG] >= 1 * MB, 60)
eng14c.stop_temp(tid14c)
big_before = eng14c.file_progress(tid14c)[I_BIG]
seed.limit(0)
eng14c.open_stream(tid14c, I_SMALL)
eng14c.focus_file(tid14c, I_SMALL)
small_done = wait_for(lambda: eng14c.file_progress(tid14c)[I_SMALL]
                      >= SIZES["видео 2.bin"], 60)
time.sleep(1.5)
check("14 сериал: пока смотрят, докачанная временная раздача не на паузе",
      state(eng14c, tid14c) == te.STATE_SEEDING, str(state(eng14c, tid14c)))
eng14c.stop_temp(tid14c)
small_path = os.path.join(eng14c.watch_dir(tid14c), "Тестовая раздача",
                          "видео 2.bin")
big_path14 = os.path.join(eng14c.watch_dir(tid14c), "Тестовая раздача",
                          "видео 1.bin")
# Та же раздача только что удалена предыдущим шагом и её папка убиралась в
# фоне: уборка не должна задеть файлы новой раздачи в той же папке
check("14 сериал: серия досмотрена, у соседней серии часть данных на диске",
      small_done and big_part and os.path.isfile(small_path)
      and os.path.isfile(big_path14),
      f"видео 1: {big_before} байт")
dl_before = int(eng14c._handles[tid14c].status().all_time_download)
res14c = eng14c.finish_file(tid14c, I_SMALL, keep_alive=[I_BIG])


def held(eng, tid):
    """На паузе и не в проверке. Состояние снимка тут «Скачано», а не
    «Пауза»: досмотрели последнюю заказанную серию, заказанных файлов не
    осталось, и libtorrent считает раздачу завершённой — важен сам флаг."""
    st = eng._handles[tid].status()
    return st.paused and "checking" not in str(st.state)


settled = wait_for(lambda: tid14c not in eng14c._rechecking
                   and held(eng14c, tid14c), 15)
prog14c = eng14c.file_progress(tid14c)
check("14 сериал: удалён только досмотренный файл",
      res14c == "deleted" and not os.path.exists(small_path)
      and eng14c.get(tid14c) is not None, res14c)
check("14 сериал: после перепроверки — пауза, без upload_mode, не ошибка",
      settled and not upload_mode(eng14c, tid14c)
      and state(eng14c, tid14c) in (te.STATE_PAUSED, te.STATE_FINISHED),
      f"{state(eng14c, tid14c)}, upload_mode={upload_mode(eng14c, tid14c)}")
check("14 сериал: перепроверка забыла удалённый файл, соседа не тронула",
      prog14c[I_SMALL] <= 256 * 1024
      and prog14c[I_BIG] >= big_before - 256 * 1024,
      f"было видео 1 {big_before}, стало {prog14c}")
time.sleep(2)
check("14 сериал: за перепроверку и 2 с после неё не скачано ни байта",
      int(eng14c._handles[tid14c].status().all_time_download) == dl_before,
      f"+{int(eng14c._handles[tid14c].status().all_time_download) - dl_before}")
watch14c = json.loads(open(eng14c._watch_path(tid14c), "rb").read())
check("14 сериал: метка перепроверки снята",
      "recheck" not in watch14c, str(watch14c))

# Программу закрыли посреди перепроверки: после запуска она повторяется.
# Состояние воспроизводим прямо: удаляем файл соседа при остановленном
# движке и взводим метку — как если бы finish_file не успел закончить
eng14c.shutdown(timeout=3.0)
ENGINES.remove(eng14c)
big_path = os.path.join(eng14c.watch_dir(tid14c), "Тестовая раздача",
                        "видео 1.bin")
os.remove(big_path)
watch14c["recheck"] = True
with open(eng14c._watch_path(tid14c), "wb") as f:
    f.write(json.dumps(watch14c).encode("ascii"))
eng14d, _ = temp_engine("s14c")
ENGINES.append(eng14d)
eng14d.start()
redone = wait_for(lambda: tid14c not in eng14d._rechecking
                  and tid14c not in eng14d._recheck_queued
                  and held(eng14d, tid14c), 20)
check("14 перезапуск посреди перепроверки: проверка повторена, пауза",
      redone and eng14d.file_progress(tid14c)[I_BIG] <= 512 * 1024
      and not upload_mode(eng14d, tid14c),
      f"{state(eng14d, tid14c)}, {eng14d.file_progress(tid14c)}, "
      f"очередь {tid14c in eng14d._recheck_queued}")

# Перезагрузка компьютера: временные раздачи удаляются, чужие папки —
# нет; папки-сироты с именем раздачи — да
orphan = os.path.join(watch_root("s14c"), "a" * 40)
foreign = os.path.join(watch_root("s14c"), "не наша папка")
os.makedirs(orphan, exist_ok=True)
os.makedirs(foreign, exist_ok=True)
dir14c = eng14d.watch_dir(tid14c)
eng14d.shutdown(timeout=3.0)
ENGINES.remove(eng14d)
eng14e, _ = temp_engine("s14c")
ENGINES.append(eng14e)
eng14e.start()
check("14 без перезагрузки: раздача на месте, сирота удалена, чужое цело",
      eng14e.get(tid14c) is not None
      and wait_for(lambda: not os.path.exists(orphan), 10)
      and os.path.isdir(foreign) and os.path.isdir(dir14c))
eng14e.shutdown(timeout=3.0)
ENGINES.remove(eng14e)
BOOT["at"] = time.time() + 1          # «загрузился» после last_active
eng14f, _ = temp_engine("s14c")
ENGINES.append(eng14f)
eng14f.start()
check("14 после перезагрузки: временная раздача и её папка удалены",
      eng14f.get(tid14c) is None
      and wait_for(lambda: not os.path.exists(dir14c), 10)
      and not os.path.exists(eng14f._watch_path(tid14c))
      and os.path.isdir(foreign))
BOOT["at"] = 1.0

# «Файлы» -> «Применить»: временная становится постоянной
eng14g, _ = temp_engine("s14g")
ENGINES.append(eng14g)
eng14g.start()
# Переводим НЕдокачанную раздачу: заодно переносятся частичные данные, и
# раздача не проходит через «завершена» (минута переподключения, см. выше)
seed.limit(300 * 1024)
downloads14g = os.path.join(BASE, "Загрузки с пробелом", "s14g")
tid14g = eng14g.add_torrent_file(TORRENT, downloads14g,
                                 peers=[("127.0.0.1", seed.port)], defer=True)
eng14g.begin_download(tid14g, focus=I_SMALL, temporary=True)
wait_for(lambda: eng14g.file_progress(tid14g)[I_SMALL] >= 1 * MB, 60)
eng14g.stop_temp(tid14g)
seed.limit(0)
dir14g = eng14g.watch_dir(tid14g)
stream14g = eng14g.open_stream(tid14g, I_SMALL)
try:
    eng14g.make_permanent(tid14g, downloads14g)
    refused = False
except RuntimeError:
    refused = True
check("14 в постоянные во время просмотра — отказ", refused
      and eng14g.is_temp(tid14g))
eng14g.close_stream(tid14g)
eng14g.make_permanent(tid14g, downloads14g, priorities=[4, 4, 4])
check("14 в постоянные: метки нет, раздача не на паузе",
      not eng14g.is_temp(tid14g)
      and not os.path.exists(eng14g._watch_path(tid14g))
      and wait_for(lambda: state(eng14g, tid14g) != te.STATE_PAUSED, 5),
      str(state(eng14g, tid14g)))
check("14 в постоянные: файлы перенесены в загрузки, временная папка убрана",
      wait_same_as_source(downloads14g, "Тестовая раздача\\видео 1.bin", 60)
      and same_as_source(downloads14g, "Тестовая раздача\\видео 2.bin")
      and wait_for(lambda: not os.path.exists(dir14g), 10),
      f"состояние {state(eng14g, tid14g)}, пиров "
      f"{eng14g.get(tid14g).num_peers}, прогресс "
      f"{eng14g.file_progress(tid14g)}, приоритеты "
      f"{list(eng14g._handles[tid14g].get_file_priorities())}, "
      f"видео1 {same_as_source(downloads14g, 'Тестовая раздача\видео 1.bin')}, "
      f"видео2 {same_as_source(downloads14g, 'Тестовая раздача\видео 2.bin')}, "
      f"загрузки {os.listdir(downloads14g)}, временная "
      f"{[os.path.join(r, f) for r, _, fs in os.walk(dir14g) for f in fs] if os.path.exists(dir14g) else 'нет'}, "
      f"уборки {list(eng14g._cleanups)}, перенос {eng14g._moving}, "
      f"save_path {eng14g.get(tid14g).save_path}")

# Докачалась без открытого просмотра — сама на паузу (не раздаёт), даже
# при включённой раздаче после скачивания
eng14h, _ = temp_engine("s14h")
ENGINES.append(eng14h)
eng14h.start()
tid14h = eng14h.add_torrent_file(
    TORRENT, os.path.join(BASE, "Загрузки с пробелом", "s14h"),
    peers=[("127.0.0.1", seed.port)], defer=True)
eng14h.begin_download(tid14h, focus=I_TXT, temporary=True)
check("14 докачанная без просмотра временная раздача сама встала на паузу",
      wait_for(lambda: state(eng14h, tid14h) == te.STATE_FINISHED, 30)
      and eng14h.seed_after_download,
      str(state(eng14h, tid14h)))
eng14h.remove(tid14h, delete_files=True)

# Журнал загрузок: BootType 2 (выход из гибернации) не перезагрузка
xml14 = (
    "<Event><System><TimeCreated SystemTime='2026-09-17T20:32:27.7920000Z'/>"
    "</System><EventData><Data Name='BootType'>2</Data></EventData></Event>"
    "<Event><System><TimeCreated SystemTime='2026-09-16T22:16:03.5000000Z'/>"
    "</System><EventData><Data Name='BootType'>1</Data></EventData></Event>")
parsed = te.boot_time_from_events(xml14)
check("14 журнал: гибернация пропущена, быстрый запуск — загрузка",
      parsed is not None and abs(parsed - 1789596963.5) < 0.01, str(parsed))
real_boot = te.last_boot_time()
check("14 время последней загрузки этой машины читается",
      real_boot is not None and real_boot <= time.time(),
      time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(real_boot or 0)))
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

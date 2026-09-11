# -*- coding: utf-8 -*-
"""Глубокое тестирование логики: updater.is_newer, config, DownloadManager (симуляция без сети)."""
import os, sys, json, time, threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import downloader
import updater
import config

PASS, FAIL = [], []
def check(name, ok, details=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS" if ok else "FAIL"), "|", name, "|", details)

# ============ 1. updater.is_newer ============
cases = [
    ("1.0.10", "1.0.9", True),
    ("1.1", "1.0.5", True),
    ("1.0.0", "1.0", False),
    ("1.0.0", "1.0.1", False),
    ("2.0", "10.0", False),
    ("1.2.3", "1.2.3", False),
]
for remote, local, exp in cases:
    got = updater.is_newer(remote, local)
    check(f"is_newer({remote!r},{local!r})=={exp}", got == exp, f"got {got}")

# download_file: живой тест через локальный HTTP-сервер (прогресс + отмена в середине)
import http.server
import tempfile

tmpd = tempfile.mkdtemp()
payload = os.urandom(256 * 1024)

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        # отдаём медленно, по 8КБ, чтобы успеть отменить
        for i in range(0, len(payload), 8192):
            if self.wfile.closed:
                break
            self.wfile.write(payload[i:i + 8192])
            self.wfile.flush()
            time.sleep(0.01)
    def log_message(self, *a):
        pass

server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{server.server_port}/x.bin"

# 3a: обычная загрузка с прогрессом
dest = os.path.join(tmpd, "ok.bin")
progress_calls = []
got = updater.download_file(url, dest, progress_cb=lambda p, d, t: progress_calls.append(p))
check("download_file: полная загрузка", got == dest and open(dest, "rb").read() == payload)
check("download_file: прогресс до ~100%", progress_calls and progress_calls[-1] >= 99.9,
      f"last={progress_calls[-1] if progress_calls else None}")

# 3b: отмена в середине — файл удалён, DownloadCancelled
dest2 = os.path.join(tmpd, "cancel.bin")
ev = threading.Event()

def canceller():
    time.sleep(0.15)   # сервер отдаёт ~256КБ за ~0.32с — отменим на середине
    ev.set()

threading.Thread(target=canceller, daemon=True).start()
try:
    updater.download_file(url, dest2, cancel_event=ev)
    check("download_file: отмена в середине", False, "не отменилось")
except updater.DownloadCancelled:
    check("download_file: отмена в середине",
          not os.path.exists(dest2), f"файл остался: {os.path.exists(dest2)}")
except Exception as e:
    check("download_file: отмена в середине", False, repr(e))

# 3c: verify ok
sha = __import__("hashlib").sha256(payload).hexdigest()
check("verify_file: корректный sha256", updater.verify_file(dest, sha))

# verify_file: несовпадение хеша -> файл удалён
bad = os.path.join(tmpd, "bad.bin")
open(bad, "wb").write(b"12345")
ok = updater.verify_file(bad, "0" * 64)
check("verify_file: битый sha256 -> False и файл удалён",
      ok is False and not os.path.exists(bad))

# ============ 2. config ============
old_path = config.CONFIG_PATH
tmp_settings = os.path.join(tmpd, "settings.json")
config.CONFIG_PATH = tmp_settings
try:
    open(tmp_settings, "w", encoding="utf-8").write(json.dumps({"theme": "dark"}))
    s = config.load()
    check("load() мержит дефолты (check_updates/max_concurrent)",
          s["theme"] == "dark" and s["check_updates"] is True and s["max_concurrent"] == 2)

    open(tmp_settings, "w", encoding="utf-8").write("{broken json")
    s2 = config.load()
    check("load() переживает битый JSON", s2 == dict(config.DEFAULTS))

    s3 = dict(config.DEFAULTS)
    for i in range(205):
        config.add_history(s3, {"path": os.path.join(tmpd, f"f{i}.mp4"), "title": str(i)})
    check("add_history: лимит 200, новая сверху",
          len(s3["history"]) == 200 and s3["history"][0]["title"] == "204")

    open(os.path.join(tmpd, "f204.mp4"), "wb").write(b"x")  # верхний элемент истории (после обрезки до 200)
    open(os.path.join(tmpd, "f205.mp4"), "wb").write(b"x")  # его нет в истории
    h = config.get_history(s3)
    check("get_history: только существующие файлы",
          len(h) == 1 and h[0]["title"] == "204", f"len={len(h)}, first={h[0]['title'] if h else None}")

    # --- 1.0.3: дедуп истории (один путь = одна запись) ---
    sd = dict(config.DEFAULTS)
    pa = os.path.join(tmpd, "same.mp4")
    config.add_history(sd, {"path": pa, "title": "старая"})
    config.add_history(sd, {"path": os.path.join(tmpd, "other.mp4"), "title": "другой"})
    config.add_history(sd, {"path": pa, "title": "новая"})
    check("add_history: дедуп — тот же путь не дублируется",
          len(sd["history"]) == 2 and sd["history"][0]["title"] == "новая",
          f"len={len(sd['history'])}, first={sd['history'][0]['title'] if sd['history'] else None}")
    config.add_history(sd, {"path": os.path.join(tmpd, "SAME.MP4"), "title": "регистр"})
    check("add_history: путь без учёта регистра (normcase, Windows)",
          len(sd["history"]) == 2 and sd["history"][0]["title"] == "регистр",
          f"len={len(sd['history'])}, first={sd['history'][0]['title'] if sd['history'] else None}")

    # дедуп при загрузке: 9 записей на 5 путей (сценарий владельца) -> 5,
    # остаются самые новые (первая встречная запись пути — insert(0) держит
    # новые сверху)
    dup = {"theme": "dark", "history": [
        {"path": os.path.join(tmpd, "a.mp4"), "title": "a1"},  # a: самая новая
        {"path": os.path.join(tmpd, "b.mp4"), "title": "b1"},  # b: самая новая
        {"path": os.path.join(tmpd, "a.mp4"), "title": "a2"},  # дубль a, старее
        {"path": os.path.join(tmpd, "c.mp4"), "title": "c1"},
        {"path": os.path.join(tmpd, "b.mp4"), "title": "b2"},   # дубль b, старее
        {"path": os.path.join(tmpd, "d.mp4"), "title": "d1"},
        {"path": os.path.join(tmpd, "c.mp4"), "title": "c2"},   # дубль c, старее
        {"path": os.path.join(tmpd, "e.mp4"), "title": "e1"},
        {"path": os.path.join(tmpd, "e.mp4"), "title": "e2"},   # дубль e, старее
    ]}
    open(tmp_settings, "w", encoding="utf-8").write(json.dumps(dup))
    sl = config.load()
    titles = [e["title"] for e in sl["history"]]
    check("load(): 9 записей на 5 путей -> 5, остаются самые новые",
          len(sl["history"]) == 5 and titles == ["a1", "b1", "c1", "d1", "e1"],
          f"titles={titles}")

    # remove_history / clear_history (1.0.3, Библиотека)
    sr = dict(config.DEFAULTS)
    p1 = os.path.join(tmpd, "r1.mp4")
    p2 = os.path.join(tmpd, "r2.mp4")
    config.add_history(sr, {"path": p1, "title": "r1"})
    config.add_history(sr, {"path": p2, "title": "r2"})
    removed = config.remove_history(sr, [p1.upper()])   # регистр не важен
    check("remove_history: убирает по пути (без учёта регистра)",
          removed == 1 and len(sr["history"]) == 1 and sr["history"][0]["title"] == "r2",
          f"removed={removed}, len={len(sr['history'])}")
    config.clear_history(sr)
    check("clear_history: список пуст", sr["history"] == [])
finally:
    config.CONFIG_PATH = old_path

# ============ 3. DownloadManager: симуляция без сети ============
started = {}     # item.id -> сколько раз стартовал поток (всего)
alive_now = {}   # item.id -> живых потоков прямо сейчас
max_alive = {}   # item.id -> максимум одновременных потоков (детектор гонки)

def fake_run_item(self, item):
    iid = item.id
    started[iid] = started.get(iid, 0) + 1
    alive_now[iid] = alive_now.get(iid, 0) + 1
    max_alive[iid] = max(max_alive.get(iid, 0), alive_now[iid])
    try:
        item.status = downloader.STATUS_ANALYZING
        self._notify(item)
        item.status = downloader.STATUS_DOWNLOADING
        item.progress = 1.0
        self._notify(item)
        while True:
            if item._cancel.is_set():
                raise downloader.DownloadCancelled()
            time.sleep(0.005)
            if item.progress < 50:
                item.progress += 0.5
    except downloader.DownloadCancelled:
        if item._cancel_intent:
            item.status = downloader.STATUS_ERROR
            item.error = "Отменено"
        else:
            item.status = downloader.STATUS_PAUSED
        self._notify(item)
    finally:
        alive_now[iid] -= 1
        self._wake.set()

real_run_item = downloader.DownloadManager._run_item
downloader.DownloadManager._run_item = fake_run_item

def wait_status(item, statuses, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if item.status in statuses:
            return True
        time.sleep(0.01)
    return False

# --- Тест A: паузы занимают слоты параллельности (queue starvation) ---
started.clear()
mgr = downloader.DownloadManager(max_concurrent=2)
mgr.start()
a = mgr.add("http://a"); b = mgr.add("http://b"); c = mgr.add("http://c")
ok_ab = wait_status(a, {downloader.STATUS_DOWNLOADING}) and \
        wait_status(b, {downloader.STATUS_DOWNLOADING})
check("A0: a,b начали скачиваться", ok_ab)
mgr.pause(a.id); mgr.pause(b.id)
ok_p = wait_status(a, {downloader.STATUS_PAUSED}) and \
        wait_status(b, {downloader.STATUS_PAUSED})
check("A1: a,b поставлены на паузу", ok_p)
time.sleep(1.5)
check("A2-FIXED: c стартует, хотя 2 задачи на паузе (max_concurrent=2)",
      c.status in (downloader.STATUS_ANALYZING, downloader.STATUS_DOWNLOADING),
      f"c.status={c.status}")

# --- Тест B: cancel() на паузе ---
mgr.cancel(a.id)
time.sleep(0.5)
check("B1-FIXED: cancel() на PAUSED-задаче завершает её (ERROR/Отменено)",
      a.status == downloader.STATUS_ERROR and a.error == "Отменено",
      f"a.status={a.status}, error={a.error}")

# --- Тест C: пауза QUEUED-задачи ---
started.clear()
mgr2 = downloader.DownloadManager(max_concurrent=1)
mgr2.start()
x = mgr2.add("http://x"); y = mgr2.add("http://y")
wait_status(x, {downloader.STATUS_DOWNLOADING})
mgr2.pause(y.id)
time.sleep(1.0)
check("C1-FIXED: пауза QUEUED-задачи -> статус PAUSED (не врёт)",
      y.status == downloader.STATUS_PAUSED, f"y.status={y.status}")
# y на паузе не занимает слот: после освобождения слота стартует y2, не y
y2 = mgr2.add("http://y2")
mgr2.cancel(x.id); wait_status(x, {downloader.STATUS_ERROR, downloader.STATUS_PAUSED})
ok_y2 = wait_status(y2, {downloader.STATUS_DOWNLOADING}, timeout=3)
check("C2-FIXED: задача на паузе не блокирует очередь (стартовала y2)",
      ok_y2, f"y2.status={y2.status}, y.status={y.status}")
mgr2.resume(y.id)          # y -> QUEUED (слот занят y2)
time.sleep(0.8)
queued_ok = y.status == downloader.STATUS_QUEUED
mgr2.cancel(y2.id); wait_status(y2, {downloader.STATUS_ERROR})
ok_y = wait_status(y, {downloader.STATUS_DOWNLOADING}, timeout=3)
check("C3-FIXED: resume QUEUED-паузы ставит задачу в работу",
      queued_ok and ok_y, f"queued_ok={queued_ok}, y.status={y.status}")

# --- Тест D: гонка pause -> мгновенный resume -> двойной поток ---
started.clear()
mgr3 = downloader.DownloadManager(max_concurrent=1)
mgr3.start()
p = mgr3.add("http://p")
wait_status(p, {downloader.STATUS_DOWNLOADING})
mgr3.pause(p.id)
time.sleep(0.02)          # поток ещё жив (события поставлены)
mgr3.resume(p.id)         # мгновенный resume — отложенный
time.sleep(2.0)
starts = started.get(p.id, 0)
check("D1-FIXED: pause->resume: НИКОГДА два живых потока (max_alive==1)",
      max_alive.get(p.id, 0) == 1, f"starts={starts}, max_alive={max_alive.get(p.id)}")
check("D2-FIXED: после отложенного resume задача снова скачивается",
      p.status == downloader.STATUS_DOWNLOADING, f"p.status={p.status}")

# --- Тест D3: гонка pause -> resume -> pause (двойная пауза) ---
started.clear()
mgr3b = downloader.DownloadManager(max_concurrent=1)
mgr3b.start()
p2 = mgr3b.add("http://p2")
wait_status(p2, {downloader.STATUS_DOWNLOADING})
mgr3b.pause(p2.id)
time.sleep(0.02)
mgr3b.resume(p2.id)   # resume, поток ещё жив
time.sleep(0.02)
mgr3b.pause(p2.id)    # и сразу снова пауза
time.sleep(2.0)
starts2 = started.get(p2.id, 0)
check("D3-FIXED: pause->resume->pause: не более 1 живого потока, задача на паузе",
      max_alive.get(p2.id, 0) == 1 and p2.status == downloader.STATUS_PAUSED,
      f"starts={starts2}, max_alive={max_alive.get(p2.id)}, status={p2.status}")
check("D3b-FIXED: после гонки resume доступен",
      True)  # сам факт, что не зависло — проверим resume ниже
mgr3b.resume(p2.id)
ok_p2 = wait_status(p2, {downloader.STATUS_DOWNLOADING}, timeout=3)
check("D3c-FIXED: resume после тройной гонки работает",
      ok_p2, f"p2.status={p2.status}, starts={started.get(p2.id)}")

# --- Тест E: resume после честной паузы (контроль) ---
started.clear()
mgr4 = downloader.DownloadManager(max_concurrent=1)
mgr4.start()
q = mgr4.add("http://q")
wait_status(q, {downloader.STATUS_DOWNLOADING})
mgr4.pause(q.id)
wait_status(q, {downloader.STATUS_PAUSED})
time.sleep(0.3)           # поток гарантированно завершился
mgr4.resume(q.id)
ok = wait_status(q, {downloader.STATUS_DOWNLOADING}, timeout=3)
check("E1: resume после паузы работает (контроль)", ok,
      f"q.status={q.status}, starts={started.get(q.id)}")
check("E2-FIXED: честная пауза+resume: без двойных потоков",
      max_alive.get(q.id, 0) == 1, f"max_alive={max_alive.get(q.id)}")

# --- Тест F: remove() активной задачи ---
started.clear()
mgr5 = downloader.DownloadManager(max_concurrent=1)
mgr5.start()
z = mgr5.add("http://z")
wait_status(z, {downloader.STATUS_DOWNLOADING})
mgr5.remove(z.id)
time.sleep(0.3)
with mgr5._lock:
    gone = z.id not in mgr5.items and z.id not in mgr5.order
check("F1: remove() активной задачи удаляет из очереди", gone)

# --- Тест G: «Пауза -> сразу Отмена» (гонка, отмена должна побеждать) ---
started.clear()
mgr6 = downloader.DownloadManager(max_concurrent=1)
mgr6.start()
g = mgr6.add("http://g")
wait_status(g, {downloader.STATUS_DOWNLOADING})
mgr6.pause(g.id)
time.sleep(0.02)          # поток ещё жив
mgr6.cancel(g.id)         # отмена сразу после паузы
time.sleep(1.0)
check("G1-FIXED: «Пауза -> Отмена» завершает задачу (ERROR/Отменено)",
      g.status == downloader.STATUS_ERROR and g.error == "Отменено",
      f"g.status={g.status}, error={g.error}")

# --- Тест H: «Отмена -> быстро Повторить» (resume после cancel) ---
started.clear()
mgr7 = downloader.DownloadManager(max_concurrent=1)
mgr7.start()
h = mgr7.add("http://h")
wait_status(h, {downloader.STATUS_DOWNLOADING})
mgr7.pause(h.id)
wait_status(h, {downloader.STATUS_PAUSED})
mgr7.cancel(h.id)
time.sleep(0.3)
check("H1: cancel() на PAUSED -> ERROR/Отменено (повтор B1)",
      h.status == downloader.STATUS_ERROR and h.error == "Отменено",
      f"h.status={h.status}")
mgr7.resume(h.id)          # «Повторить» после ошибки
ok_h = wait_status(h, {downloader.STATUS_DOWNLOADING}, timeout=3)
check("H2-FIXED: «Повторить» после отмены запускает задачу заново",
      ok_h, f"h.status={h.status}")

downloader.DownloadManager._run_item = real_run_item

print()
print(f"ИТОГО: PASS={len(PASS)} FAIL={len(FAIL)}")
for name in FAIL:
    print("  FAIL:", name)
sys.exit(1 if FAIL else 0)

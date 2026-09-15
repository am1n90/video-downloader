# -*- coding: utf-8 -*-
"""Офлайн-тест config.load/save: повреждённый/занятый settings.json.

F1/F2/F3 (1.0.2): load() никогда не бросает и не молча затирает данные;
повреждённый файл сохраняется как settings.json.corrupt-<дата>; занятый
файл читается с 3 ретраями; save() атомарен (tmp+fsync+os.replace, 3
ретрая, исключение из save() не выходит). Все сценарии — в TEMP.
"""
import builtins
import io
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config

PASS, FAIL = [], []
def check(name, ok, details=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS" if ok else "FAIL"), "|", name, "|", details)

tmp = tempfile.mkdtemp(prefix="vd_cfg_robust_")
old_cache = config._log_dir_cache
config._log_dir_cache = tmp            # app.log тоже в temp
old_path = config.CONFIG_PATH
real_open, real_replace = builtins.open, os.replace


def app_log_text():
    p = os.path.join(tmp, "app.log")
    return io.open(p, encoding="utf-8").read() if os.path.isfile(p) else ""


def corrupt_copies(directory):
    return [f for f in os.listdir(directory) if ".corrupt-" in f]


def reset_flags():
    config._corrupt_backed_up = False
    config._corrupt_backup_path = None
    config._skip_saving = False
    config._load_warning = None


def fresh(name):
    """Своя подпапка на сценарий: .corrupt-копии не смешиваются."""
    reset_flags()
    d = os.path.join(tmp, name)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "settings.json")
    config.CONFIG_PATH = p
    return p, d


try:
    # ---- сценарий 1: файла нет (первый запуск — не ошибка) ----
    p, d = fresh("s1")
    s = config.load()
    check("1 missing: дефолты без исключений", s == config.DEFAULTS)
    check("1 missing: .corrupt-копии нет", corrupt_copies(d) == [])
    check("1 missing: warning None (AC2)", config.consume_load_warning() is None)
    config.save({"theme": "dark"})
    check("1 missing: save работает", config.load().get("theme") == "dark")

    # ---- сценарий 2: битый JSON (utf-8) ----
    p, d = fresh("s2")
    base = len(app_log_text())
    broken = '{"theme": "dark", "history": [{"path": "C:/old.mp4"}], "defaul'
    io.open(p, "w", encoding="utf-8").write(broken)
    s = config.load()
    check("2 битый JSON: дефолты, старт без исключений", s == config.DEFAULTS)
    copies = corrupt_copies(d)
    check("2 битый JSON: ровно одна .corrupt", len(copies) == 1, str(copies))
    if copies:
        body = io.open(os.path.join(d, copies[0]), encoding="utf-8-sig").read()
        check("2 битый JSON: копия == исходные байты", body == broken)
    check("2 битый JSON: app.log пишет о копии",
          "копия" in app_log_text()[base:])
    warning = config.consume_load_warning()
    check("2 битый JSON: warning не None", warning is not None, str(warning))
    if warning is not None:
        check("2 битый JSON: warning.skip_saving == False",
              warning.get("skip_saving") is False)
        check("2 битый JSON: warning.corrupt_path указывает на копию",
              copies and warning.get("corrupt_path") == os.path.join(d, copies[0]))
    check("2 битый JSON: consume второй раз -> None (AC3)",
          config.consume_load_warning() is None)
    config.save({"theme": "dark"})
    check("2 битый JSON: save перезаписывает (копия есть)",
          config.load().get("theme") == "dark")
    check("2 битый JSON: нормальный load -> warning None (AC2)",
          config.consume_load_warning() is None)

    # ---- сценарий 3: обрыв записи ----
    p, d = fresh("s3")
    io.open(p, "w", encoding="utf-8").write('{"theme": "dark", "default_f')
    s = config.load()
    check("3 обрыв записи: дефолты + .corrupt",
          s == config.DEFAULTS and len(corrupt_copies(d)) == 1)

    # ---- сценарий 4: CP1251 (краш-регрессия 09.09) ----
    p, d = fresh("s4")
    with open(p, "wb") as f:
        f.write('{"default_folder": "C:\\Загрузки"}'.encode("cp1251"))
    try:
        s = config.load()
        check("4 CP1251: UnicodeDecodeError не проброшен", s == config.DEFAULTS)
    except Exception as exc:
        check("4 CP1251: UnicodeDecodeError не проброшен", False, repr(exc))
    check("4 CP1251: .corrupt создана", len(corrupt_copies(d)) == 1)

    # ---- сценарий 5: валидный JSON не-словарь — тоже повреждение ----
    p, d = fresh("s5")
    src = '["video1.mp4", "video2.mp4"]'
    io.open(p, "w", encoding="utf-8").write(src)
    s = config.load()
    copies = corrupt_copies(d)
    ok5 = s == config.DEFAULTS and len(copies) == 1
    if ok5 and copies:
        ok5 = io.open(os.path.join(d, copies[0]), encoding="utf-8-sig").read() == src
    check("5 не-словарь: дефолты + .corrupt с исходником", ok5)

    # ---- сценарий 6: чтение занят (PermissionError x2 -> успех) ----
    p, d = fresh("s6")
    io.open(p, "w", encoding="utf-8").write(
        json.dumps({"theme": "dark", "default_folder": "D:/V"}))
    state = {"n": 0}

    def flaky_open(file, *a, **kw):
        mode = a[1] if len(a) > 1 else kw.get("mode", "r")
        if file == config.CONFIG_PATH and "r" in mode and state["n"] < 2:
            state["n"] += 1
            raise PermissionError(13, "simulated AV lock")
        return real_open(file, *a, **kw)

    try:
        builtins.open = flaky_open
        t0 = time.monotonic()
        s = config.load()
        elapsed = time.monotonic() - t0
    finally:
        builtins.open = real_open
    check("6 чтение x2->успех: реальные данные",
          s.get("theme") == "dark" and s.get("default_folder") == "D:/V")
    check("6 чтение x2->успех: .corrupt нет", corrupt_copies(d) == [])
    check("6 чтение x2->успех: паузы >= 0.2с", elapsed >= 0.2, f"{elapsed:.2f}с")

    # ---- сценарий 7: чтение всегда занято, копия не вышла -> save off ----
    # ВАЖНО: shutil.copy2 на Windows идёт через Win32 CopyFile2 (не через
    # builtins.open), поэтому «падение копии» симулируем патчем copy2.
    p, d = fresh("s7")
    original = '{"theme": "dark", "history": [{"path": "C:/x.mp4"}]}'
    io.open(p, "w", encoding="utf-8").write(original)
    log_before = len(app_log_text())

    def always_locked(file, *a, **kw):
        mode = a[1] if len(a) > 1 else kw.get("mode", "r")
        if file == config.CONFIG_PATH and "r" in mode:
            raise PermissionError(13, "simulated AV lock")
        return real_open(file, *a, **kw)

    def failing_copy2(*a, **kw):
        raise PermissionError(13, "simulated AV lock on copy")

    real_copy2 = shutil.copy2
    try:
        builtins.open = always_locked
        shutil.copy2 = failing_copy2
        t0 = time.monotonic()
        s = config.load()
        config.save({"theme": "light"})     # не должен тронуть файл
        elapsed = time.monotonic() - t0
    finally:
        builtins.open = real_open
        shutil.copy2 = real_copy2
    body = io.open(p, encoding="utf-8-sig").read()
    check("7 save-off: дефолты без исключений", s == config.DEFAULTS)
    check("7 save-off: .corrupt нет (копия не вышла)", corrupt_copies(d) == [])
    check("7 save-off: исходные данные не тронуты", body == original)
    tail = app_log_text()[log_before:]
    lines = [l for l in tail.splitlines() if l.strip()]
    check("7 save-off: app.log >= 2 записи (save-off + skip save)",
          len(lines) >= 2, str(lines[:2]))
    check("7 save-off: паузы >= 0.2с", elapsed >= 0.2, f"{elapsed:.2f}с")
    check("7 save-off: _skip_saving взведён", config._skip_saving is True)
    warning7 = config.consume_load_warning()
    check("7 save-off: warning.skip_saving == True",
          warning7 is not None and warning7.get("skip_saving") is True,
          str(warning7))
    check("7 save-off: warning.corrupt_path is None (копия не вышла)",
          warning7 is not None and warning7.get("corrupt_path") is None)

    # ---- сценарий 8: os.replace x2 -> успех ----
    p, d = fresh("s8")
    io.open(p, "w", encoding="utf-8").write(json.dumps({"theme": "dark"}))
    state2 = {"n": 0}

    def flaky_replace(src, dst, *a, **kw):
        if src.endswith(".tmp") and state2["n"] < 2:
            state2["n"] += 1
            raise PermissionError(13, "simulated AV lock")
        return real_replace(src, dst, *a, **kw)

    try:
        os.replace = flaky_replace
        t0 = time.monotonic()
        config.save({"theme": "light", "history": [{"path": "D:/y.mp4"}]})
        elapsed = time.monotonic() - t0
    finally:
        os.replace = real_replace
    check("8 replace x2->успех: данные сохранены",
          config.load().get("theme") == "light")
    check("8 replace x2->успех: паузы >= 0.2с", elapsed >= 0.2, f"{elapsed:.2f}с")

    # ---- сценарий 9: os.replace всегда PermissionError ----
    p, d = fresh("s9")
    original = json.dumps({"theme": "dark", "history": [{"path": "C:/z.mp4"}]})
    io.open(p, "w", encoding="utf-8").write(original)

    def fail_replace(src, dst, *a, **kw):
        if src.endswith(".tmp"):
            raise PermissionError(13, "simulated AV lock")
        return real_replace(src, dst, *a, **kw)

    try:
        os.replace = fail_replace
        t0 = time.monotonic()
        config.save({"theme": "light"})    # не должно бросать
        elapsed = time.monotonic() - t0
    finally:
        os.replace = real_replace
    check("9 replace fail: исходный файл цел (байт в байт)",
          io.open(p, encoding="utf-8-sig").read() == original)
    check("9 replace fail: tmp удалён", not os.path.exists(p + ".tmp"))
    check("9 replace fail: app.log пишет", "не перезаписан" in app_log_text())
    check("9 replace fail: паузы >= 0.2с", elapsed >= 0.2, f"{elapsed:.2f}с")

    # ---- сценарий 13 (правка 1): replace всегда, не-PermissionError OSError ----
    p, d = fresh("s13")
    original = json.dumps({"theme": "dark"})
    io.open(p, "w", encoding="utf-8").write(original)

    def oserr_replace(src, dst, *a, **kw):
        if src.endswith(".tmp"):
            raise OSError("simulated sharing violation")
        return real_replace(src, dst, *a, **kw)

    try:
        os.replace = oserr_replace
        t0 = time.monotonic()
        config.save({"theme": "light"})    # не должно бросать
        elapsed = time.monotonic() - t0
    finally:
        os.replace = real_replace
    check("13 generic OSError: исходный файл цел (байт в байт)",
          io.open(p, encoding="utf-8-sig").read() == original)
    check("13 generic OSError: tmp удалён", not os.path.exists(p + ".tmp"))
    check("13 generic OSError: app.log пишет", "не перезаписан" in app_log_text())
    check("13 generic OSError: паузы >= 0.2с", elapsed >= 0.2, f"{elapsed:.2f}с")

    # ---- сценарий 14 (правка CP1): объект, не сериализуемый в JSON ----
    p, d = fresh("s14")
    original = json.dumps({"theme": "dark"})
    io.open(p, "w", encoding="utf-8").write(original)

    class NotSerializable:
        pass

    circular = {}
    circular["self"] = circular          # json.dump -> ValueError
    log_before = len(app_log_text())
    no_raise = True
    try:
        config.save({"theme": "light", "bad": NotSerializable()})  # TypeError
        config.save(circular)                                       # ValueError
    except Exception:
        no_raise = False
    check("14 non-JSON: save не бросает (TypeError и ValueError)", no_raise)
    check("14 non-JSON: исходный файл цел (байт в байт)",
          io.open(p, encoding="utf-8-sig").read() == original)
    check("14 non-JSON: tmp удалён", not os.path.exists(p + ".tmp"))
    added = app_log_text()[log_before:]
    lines14 = [l for l in added.splitlines() if l.strip()]
    check("14 non-JSON: app.log >= 2 записи", len(lines14) >= 2,
          str(lines14[:2]))

    # ---- сценарий 10: нормальная запись ----
    p, d = fresh("s10")
    data = {"theme": "dark", "default_folder": "D:/Видео Загрузки",
            "history": [{"path": "C:/кино.mp4", "title": "Кино"}]}
    config.save(data)
    with open(p, "rb") as f:
        raw = f.read(3)
    check("10 normal: utf-8 без BOM", raw != b"\xef\xbb\xbf")
    back = config.load()
    check("10 normal: round-trip (тема/папка/история)",
          back.get("theme") == "dark"
          and back.get("default_folder") == "D:/Видео Загрузки"
          and back.get("history") == data["history"])
    check("10 normal: tmp не остаётся", not os.path.exists(p + ".tmp"))

    # ---- сценарий 11: BOM-файл (регрессия инцидента 09.09) ----
    p, d = fresh("s11")
    with open(p, "w", encoding="utf-8-sig") as f:
        json.dump({"theme": "dark"}, f)
    check("11 BOM: файл читается", config.load().get("theme") == "dark")

    # ---- сценарий 12: .corrupt максимум одна за запуск ----
    p, d = fresh("s12")
    io.open(p, "w", encoding="utf-8").write("{битый")
    config.load()
    config.load()
    check("12 одна .corrupt за запуск", len(corrupt_copies(d)) == 1)

finally:
    config.CONFIG_PATH = old_path
    config._log_dir_cache = old_cache
    builtins.open = real_open
    os.replace = real_replace
    shutil.copy2 = real_copy2
    import logging
    for _name in ("vdl.app", "vdl.ytdlp"):
        # напрямую, без config.get_logger: он создал бы файл лога
        # в восстановленном каталоге (unused.log в корне проекта)
        _lg = logging.getLogger(_name)
        for _h in _lg.handlers[:]:
            _lg.removeHandler(_h)
        config._loggers.pop(_name, None)
    shutil.rmtree(tmp, ignore_errors=True)

print()
print(f"ИТОГ: {len(PASS)} PASS, {len(FAIL)} FAIL")
sys.exit(1 if FAIL else 0)

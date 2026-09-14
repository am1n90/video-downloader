"""Конфигурация приложения: загрузка и сохранение settings.json."""

import json
import logging
import os
import shutil
import sys
import threading
import time

# Единый источник версии приложения (build.bat подставляет её в installer.iss
# и в Output\latest.json). Bump версии = правка этой строки.
APP_VERSION = "1.0.5"

# Репозиторий релизов (GitHub Releases)
REPO = "am1n90/video-downloader"
UPDATE_MANIFEST_URL = (
    f"https://github.com/{REPO}/releases/latest/download/latest.json"
)

DEFAULTS = {
    "theme": "system",                      # light / dark / system
    "default_folder": os.path.join(os.path.expanduser("~"), "Downloads"),
    "default_quality": "best",               # best / 1080 / 720 ...
    "default_mode": "video",                # video / audio
    "max_concurrent": 2,                     # одновременных загрузок
    "notifications": True,
    "window_geometry": None,                # "WxH" или null
    "history": [],                           # завершённые загрузки
    "check_updates": True,                   # автопроверка при старте
    # Стабильный алиас latest (не URL конкретного релиза — иначе старые
    # билды не увидят новые версии). Переопределяется в settings.json
    # только для локального теста цикла обновления.
    "update_manifest_url": UPDATE_MANIFEST_URL,
}


def _config_path():
    """Путь до settings.json.

    В собранном exe (PyInstaller) настройки хранятся в
    %LOCALAPPDATA%\\VideoDownloader\\settings.json — данные выживают
    при обновлении установки. В dev-режиме — рядом с config.py.
    """
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        directory = os.path.join(base, "VideoDownloader")
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError:
            directory = os.path.dirname(sys.executable)
        return os.path.join(directory, "settings.json")
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "settings.json"
    )


CONFIG_PATH = _config_path()

_lock = threading.Lock()

_logger_lock = threading.Lock()
_loggers = {}                 # имена уже созданных логгеров (idempotent)
_log_dir_cache = None         # каталог логов (кэшируется)

_corrupt_backed_up = False    # .corrupt-копия: не больше одной за запуск
_skip_saving = False          # файл не читался и копии нет: не перезаписывать


# ---------- единый механизм логов (config и downloader) ----------


def _log_dir():
    """Каталог логов: frozen — %LOCALAPPDATA%\\VideoDownloader (рядом
    с settings.json, переживает обновления установки), dev — корень
    проекта (покрыт *.log в .gitignore). Результат кэшируется;
    недоступен — '' (логи молча отключены, приложение работает дальше).
    """
    global _log_dir_cache
    if _log_dir_cache is None:
        if getattr(sys, "frozen", False):
            base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
            directory = os.path.join(base, "VideoDownloader")
        else:
            directory = os.path.dirname(os.path.abspath(__file__))
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError:
            directory = ""
        _log_dir_cache = directory
    return _log_dir_cache


def get_logger(name, filename, level=logging.WARNING):
    """Единая точка создания файловых логгеров приложения.

    Один logger на имя при повторных вызовах: FileHandler добавляется
    один раз — дублей записей нет. Каталог/файл недоступны — NullHandler
    (логирование отключается, приложение не ломается). append + utf-8,
    единый формат "%(asctime)s %(levelname)s %(message)s".

    config.py пишет сюда события целостности настроек (app.log),
    downloader.py — предупреждения yt-dlp (yt-dlp.log).
    """
    logger = logging.getLogger(name)
    with _logger_lock:
        if name in _loggers:
            return logger
        _loggers[name] = True
        logger.setLevel(level)
        logger.propagate = False
        directory = _log_dir()
        if directory:
            try:
                handler = logging.FileHandler(
                    os.path.join(directory, filename), encoding="utf-8"
                )
                handler.setFormatter(
                    logging.Formatter("%(asctime)s %(levelname)s %(message)s")
                )
                logger.addHandler(handler)
            except OSError:
                logger.addHandler(logging.NullHandler())
        else:
            logger.addHandler(logging.NullHandler())
    return logger


def _app_log(message):
    """Событие целостности настроек -> app.log."""
    get_logger("vdl.app", "app.log").warning(message)


# ---------- чтение: защита от занятого/повреждённого файла ----------

_READ_MISSING = object()   # файла нет — первый запуск, не ошибка
_READ_FAILED = object()    # есть, но не прочитан (занят/повреждён)


def _read_settings():
    """Прочитать settings.json -> (результат, исключение).

    OSError (файл занят антивирусом/индексатором) — до 3 попыток с
    паузой 0.1 с, как в save(). ValueError (битый JSON / чужая
    кодировка) — без повторов: повтор не изменит результат.
    """
    last_exc = None
    for attempt in range(3):
        try:
            # utf-8-sig: устойчив к BOM (могут добавить внешние редакторы)
            with open(CONFIG_PATH, encoding="utf-8-sig") as f:
                return json.load(f), None
        except FileNotFoundError:
            return _READ_MISSING, None
        except ValueError as exc:
            return _READ_FAILED, exc
        except OSError as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(0.1)
    return _READ_FAILED, last_exc


def _backup_corrupt(reason):
    """Сохранить нечитаемый settings.json как settings.json.corrupt-<дата>.

    Копия — единственный шанс восстановить данные вручную: следующим
    save() файл будет перезаписан. Не больше одной копии за запуск:
    _corrupt_backed_up ставится только при успехе копии. Возвращает
    True, если копия создана (в том числе ранее в этом же запуске —
    повторный load() не должен отключать save()).
    """
    global _corrupt_backed_up
    if _corrupt_backed_up:
        return True
    if not os.path.isfile(CONFIG_PATH):
        return False
    stamp = time.strftime("%Y%m%d-%H%M%S")
    copy_path = f"{CONFIG_PATH}.corrupt-{stamp}"
    try:
        shutil.copy2(CONFIG_PATH, copy_path)
    except OSError as exc:
        _app_log(f"не удалось сохранить копию повреждённого settings.json: {exc}")
        return False
    _corrupt_backed_up = True
    _app_log(f"settings.json повреждён ({reason}); копия: "
             f"settings.json.corrupt-{stamp}; загружены дефолты")
    return True


def load():
    """Читает настройки с диска, дополняет недостающие дефолтами.

    Повреждённый файл (битый JSON, чужая кодировка, не-словарь) или
    файл, который так и не удалось прочитать (занят):
      - сохраняется копия settings.json.corrupt-<дата> (данные можно
        восстановить вручную) и загружаются дефолты;
      - если копию сохранить не удалось — save() отключается до конца
        запуска (_skip_saving), чтобы закрытие приложения не затёрло
        файл дефолтами: данные должны оставаться целыми.
    Никогда не бросает исключений (вызывается при старте приложения).
    """
    global _skip_saving
    settings = dict(DEFAULTS)
    with _lock:
        data, exc = _read_settings()
        if data is _READ_MISSING:
            return settings
        if data is _READ_FAILED or not isinstance(data, dict):
            reason = f"{type(exc).__name__}: {exc}" if exc else "не словарь"
            if not _backup_corrupt(reason):
                _skip_saving = True
                _app_log("settings.json не читался и резервная копия не "
                         "создана: сохранение отключено до перезапуска")
            return settings
        settings.update({k: v for k, v in data.items() if k in DEFAULTS})
        # 1.0.3: у истории могла накопиться одна запись на путь (до 1.0.3
        # add_history не проверял существующие пути). Дедуплицируем при
        # загрузке: остаётся первая запись каждого пути — она самая новая
        # (add_history всегда вставляет в начало). Некорректные записи
        # (не dict / без path) не трогаем — их фильтрует get_history().
        settings["history"] = _dedup_history(settings.get("history"))
        return settings


def _dedup_history(history):
    """Одна запись истории на путь (остаётся самая новая — первая).

    Новая запись всегда в начале списка (add_history -> insert(0)), поэтому
    при обходе с начала первая встречная запись пути и есть самая новая.
    Сравнение путей — как во всём коде: os.path.normcase (Windows не
    различает регистр). Записи без path не дедуплицируются.
    """
    if not isinstance(history, list):
        return history
    seen = set()
    result = []
    for entry in history:
        if isinstance(entry, dict) and entry.get("path"):
            key = os.path.normcase(entry["path"])
            if key in seen:
                continue
            seen.add(key)
        result.append(entry)
    return result


def save(settings):
    """Сохраняет настройки на диск атомарно.

    Запись: settings.json.tmp в той же папке -> fsync -> os.replace
    (обрыв записи больше не оставляет половину файла). Если os.replace
    не проходит (антивирус/индексатор держит файл; любой OSError, не
    только PermissionError) — 3 попытки с паузой 0.1 с, затем запись
    в app.log, tmp удаляется, старый файл остаётся целым. Значения,
    не сериализуемые в JSON (TypeError/ValueError), — та же ветка:
    app.log, tmp удалён, старый файл цел. Исключение
    из save() не выходит: он вызывается в том числе из closeEvent
    (исключение заблокировало бы закрытие приложения).

    _skip_saving (при старте файл не читался и копия не создана):
    settings.json не перезаписывается вообще — только запись в app.log.
    """
    with _lock:
        if _skip_saving:
            _app_log("settings.json не перезаписан: при старте он не "
                     "читался, копия не создана; перезапустите приложение")
            return
        tmp = CONFIG_PATH + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
        except (OSError, TypeError, ValueError) as exc:
            # OSError — диск/доступ; TypeError/ValueError — в settings
            # объект, не сериализуемый в JSON: записать нельзя, но данные
            # на диске (старый settings.json) должны остаться целыми
            _app_log(f"не удалось записать {os.path.basename(tmp)}: {exc}")
            try:
                os.remove(tmp)
            except OSError:
                pass
            return
        last_exc = None
        for attempt in range(3):
            try:
                os.replace(tmp, CONFIG_PATH)
                return
            except OSError as e:
                # переменная except-as удаляется после блока — держим свою
                last_exc = e
                if attempt < 2:
                    time.sleep(0.1)
        # 3 попытки не прошли (PermissionError и любой другой OSError)
        _app_log(f"settings.json не перезаписан ({last_exc}); временный "
                 f"файл удалён, старые данные целы")
        try:
            os.remove(tmp)
        except OSError:
            pass


def add_history(settings, entry):
    """Добавить завершённую загрузку в историю (в начало).

    entry: {url, source, title, quality, duration, path, mode}
    Один файл = одна запись: если путь уже есть в истории, старая запись
    удаляется — новая встаёт в начало (замена, не дубль). До 1.0.3 путь не
    проверялся и повторная загрузка того же файла дублировала запись.
    """
    with _lock:
        history = list(settings.get("history", []))
        path = entry.get("path") if isinstance(entry, dict) else None
        if path:
            key = os.path.normcase(path)
            history = [e for e in history
                        if not (isinstance(e, dict) and e.get("path")
                                and os.path.normcase(e["path"]) == key)]
        history.insert(0, entry)
        settings["history"] = history[:200]  # максимум 200 записей


def remove_history(settings, paths):
    """Убрать из истории записи с указанными путями (1.0.3, Библиотека).

    paths — исходные пути записей; сравнение через os.path.normcase,
    как в add_history. Записи без path не трогаются. Возвращает число
    удалённых записей. Файлы на диске не затрагиваются — их удаляет
    вызывающий код (gui) по отдельному подтверждению пользователя.
    """
    keys = {os.path.normcase(p) for p in paths if p}
    removed = 0
    with _lock:
        kept = []
        for e in settings.get("history", []):
            if (isinstance(e, dict) and e.get("path")
                    and os.path.normcase(e["path"]) in keys):
                removed += 1
            else:
                kept.append(e)
        settings["history"] = kept
    return removed


def clear_history(settings):
    """Очистить историю целиком (только записи; файлы не трогаются)."""
    with _lock:
        settings["history"] = []


def get_history(settings):
    """Вернуть историю (только существующие файлы)."""
    with _lock:
        history = list(settings.get("history", []))
    return [
        e for e in history
        if isinstance(e, dict) and e.get("path") and os.path.isfile(e["path"])
    ]

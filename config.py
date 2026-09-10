"""Конфигурация приложения: загрузка и сохранение settings.json."""

import json
import os
import sys
import threading

# Единый источник версии приложения (build.bat подставляет её в installer.iss
# и в Output\latest.json). Bump версии = правка этой строки.
APP_VERSION = "1.0.1"

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


def load():
    """Читает настройки с диска, дополняет недостающие дефолтами."""
    with _lock:
        settings = dict(DEFAULTS)
        try:
            # utf-8-sig: устойчив к BOM (могут добавить внешние редакторы)
            with open(CONFIG_PATH, encoding="utf-8-sig") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                settings.update(
                    {k: v for k, v in saved.items() if k in DEFAULTS}
                )
        except (OSError, json.JSONDecodeError):
            pass
        return settings


def save(settings):
    """Сохраняет настройки на диск."""
    with _lock:
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except OSError:
            pass


def add_history(settings, entry):
    """Добавить завершённую загрузку в историю (в начало).

    entry: {url, source, title, quality, duration, path, mode}
    """
    with _lock:
        history = list(settings.get("history", []))
        history.insert(0, entry)
        settings["history"] = history[:200]  # максимум 200 записей


def get_history(settings):
    """Вернуть историю (только существующие файлы)."""
    with _lock:
        history = list(settings.get("history", []))
    return [
        e for e in history
        if isinstance(e, dict) and e.get("path") and os.path.isfile(e["path"])
    ]

"""Автообновление через GitHub Releases (только stdlib, без Qt).

Схема: владелец публикует релиз с VideoDownloader-Setup-<ver>.exe и
latest.json { version, url, sha256, notes }. Приложение сравнивает
version с config.APP_VERSION, скачивает exe по url, проверяет sha256
и запускает тихую переустановку (Inno Setup), затем перезапускается.

Манифест и установщик ходят только по HTTPS (GitHub Releases).
sha256 — проверка целостности, НЕ цифровая подпись.
"""

import hashlib
import json
import os
import subprocess
import sys
import threading
import urllib.request

import config

MANIFEST_TIMEOUT = 5   # секунд
DOWNLOAD_TIMEOUT = 30   # секунд на чанк


def _is_frozen():
    return getattr(sys, "frozen", False)


def fetch_manifest(url):
    """Скачать и распарсить latest.json. Оффлайн/любая ошибка → None."""
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "VideoDownloader-Updater"}
        )
        with urllib.request.urlopen(request, timeout=MANIFEST_TIMEOUT) as resp:
            data = resp.read()
        manifest = json.loads(data.decode("utf-8"))
        if not isinstance(manifest, dict):
            return None
        if not all(k in manifest for k in ("version", "url")):
            return None
        return manifest
    except Exception:
        return None


def is_newer(remote, local):
    """Семвер-сравнение: remote > local? (кортежи int, разная длина ок).

    1.0.10 > 1.0.9; 1.1 > 1.0.5; 1.0.0 == 1.0 → False.
    """
    def parse(version):
        try:
            parts = []
            for chunk in str(version).strip().split("."):
                # отсекаем возможные суффиксы (1.0.0-beta → 1.0.0)
                digits = "".join(ch for ch in chunk if ch.isdigit())
                parts.append(int(digits) if digits else 0)
            return tuple(parts)
        except (ValueError, AttributeError):
            return (0,)

    a, b = parse(remote), parse(local)
    # разная длина: 1.0 vs 1.0.0 добиваем нулями
    length = max(len(a), len(b))
    a += (0,) * (length - len(a))
    b += (0,) * (length - len(b))
    return a > b


def download_file(url, dest, progress_cb=None, cancel_event=None):
    """Скачать файл чанками. Прогресс progress_cb(percent, downloaded, total).

    Возвращает dest. Ошибки: raise. Отмена: CancelledError-подобное
    исключение DownloadCancelled, файл частичной загрузки удаляется.
    """
    cancel = cancel_event or threading.Event()

    request = urllib.request.Request(
        url, headers={"User-Agent": "VideoDownloader-Updater"}
    )
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        downloaded = 0
        chunk_size = 64 * 1024
        with open(dest, "wb") as f:
            while True:
                if cancel.is_set():
                    f.close()
                    try:
                        os.remove(dest)
                    except OSError:
                        pass
                    raise DownloadCancelled("Загрузка отменена")
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb and total:
                    progress_cb(downloaded / total * 100, downloaded, total)
    if cancel.is_set():
        try:
            os.remove(dest)
        except OSError:
            pass
        raise DownloadCancelled("Загрузка отменена")
    return dest


class DownloadCancelled(Exception):
    pass


def sha256_file(path):
    """SHA-256 файла (hex, lowercase)."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(path, expected_sha256):
    """Проверка хеша; несовпадение → файл удаляется, возвращается False."""
    if not expected_sha256:
        return True  # без хеша в манифесте проверка не выполняется
    actual = sha256_file(path)
    if actual.lower() == str(expected_sha256).lower():
        return True
    try:
        os.remove(path)
    except OSError:
        pass
    return False


def apply_update(setup_path, app_exe):
    """Запустить тихую переустановку и перезапуск приложения.

    Схема без cmd-обёртки (вложенные кавычки start "" в cmd /c "..."
    ломаются на путях с пробелами):
      1. Popen установщика detached c /VERYSILENT /NORESTART
         /CLOSEAPPLICATIONS /AUTOLAUNCH (Popen сам корректно квотит
         пути с пробелами и кириллицей);
      2. /CLOSEAPPLICATIONS — Inno дождётся выхода родителя;
      3. /AUTOLAUNCH — секция [Run] запустит приложение после установки
         (Check: LaunchAfterUpdate в installer.iss);
      4. вызывающий код немедленно завершает приложение.
    """
    if not _is_frozen():
        raise RuntimeError("apply_update доступен только в собранном приложении")

    args = [
        setup_path,
        "/VERYSILENT", "/NORESTART", "/CLOSEAPPLICATIONS",
        "/SUPPRESSMSGBOXES", "/AUTOLAUNCH",
    ]
    subprocess.Popen(
        args,
        shell=False,
        creationflags=subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
        cwd=os.path.dirname(setup_path),
    )

# -*- coding: utf-8 -*-
"""Генератор тестовых данных Библиотеки 1.0.3 (dev-проверка владельцем).

Создаёт:
  - %TEMP%\\vd-lib-demo\\demo1..3.mp4 (маленькие) + stranger.txt (не медиа)
  - dev settings.json (рядом с config.py) с историей как у владельца
    до 1.0.3: 6 записей, 3 уникальных пути + дубль + запись без файла.

Запуск: build-venv\\Scripts\\python.exe .vd-tests\\make_demo_data.py
Удалить после проверки: settings.json и %TEMP%\\vd-lib-demo (см. отчёт).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

demo = os.path.join(os.environ["TEMP"], "vd-lib-demo")
os.makedirs(demo, exist_ok=True)
for i in (1, 2, 3):
    with open(os.path.join(demo, f"demo{i}.mp4"), "wb") as f:
        f.write(b"demo" * 256)
with open(os.path.join(demo, "stranger.txt"), "wb") as f:
    f.write(b"not media")


def entry(url, source, title, quality, duration, name):
    return {
        "url": url,
        "source": source,
        "title": title,
        "quality": quality,
        "duration": duration,
        "mode": "video",
        "path": os.path.join(demo, name),
    }


settings = dict(config.DEFAULTS)
settings["default_folder"] = demo
settings["check_updates"] = False        # живая проверка без сети к GitHub
settings["history"] = [
    entry("https://youtube.com/watch?v=demo1", "YouTube",
          "Демо-ролик 1 (новее)", "1080p", 301, "demo1.mp4"),
    entry("https://vk.com/video-1_1", "VK",
          "Демо-ролик 2", "720p", 122, "demo2.mp4"),
    entry("https://youtube.com/watch?v=demo1", "YouTube",
          "Демо-ролик 1 (старая запись)", "720p", 301, "demo1.mp4"),
    entry("https://tiktok.com/@x/video/1", "TikTok",
          "Демо-ролик 3", "Лучшее", 44, "demo3.mp4"),
    entry("https://youtube.com/watch?v=demo4", "YouTube",
          "Призрак (файла нет на диске)", "1080p", 61, "ghost.mp4"),
    entry("https://youtube.com/watch?v=demo5", "YouTube",
          "Ещё один дубль demo1", "480p", 301, "demo1.mp4"),
]

config.save(settings)

# Контроль: файл читается тем же кодом, что и в приложении
loaded = config.load()
print("settings.json:", config.CONFIG_PATH)
print("записей записано:", len(settings["history"]),
      "| прочитано после дедупа:", len(loaded["history"]))
print("файлы:", sorted(os.listdir(demo)))
# 4 уникальных пути (demo1×3 + demo2 + demo3 + ghost); в Библиотеке будет
# 3 строки — ghost скрыт (файла нет на диске, фильтр get_history())
assert len(loaded["history"]) == 4, "дедуп при загрузке не сработал"
titles = [e["title"] for e in loaded["history"]]
assert titles[0] == "Демо-ролик 1 (новее)", f"первой должна быть самая новая: {titles}"
print("OK: 6 записей -> 4 уникальных пути (в GUI будет 3 строки: ghost скрыт),")
print("    самой новой осталась первая запись")

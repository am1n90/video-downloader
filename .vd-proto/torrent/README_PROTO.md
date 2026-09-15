# Прототип торрент-движка (Этап 0) — порядок запуска

Не часть приложения. В git — только код и SRT; venv, `results/`,
`RESULTS.md`, тестовые видео и .torrent — локально (см. `.gitignore`).
Все данные — в `%TEMP%\vd-torrent-proto` (удалять после этапа).

Команды — Git Bash из корня проекта.

## 1. Окружение (Python 3.13)

    py -3.13 -m venv .vd-proto/proto-venv
    .vd-proto/proto-venv/Scripts/python.exe -m pip install libtorrent==2.1.1

Нужен собранный `dist\VideoDownloader\ffmpeg.exe` (build.bat).

## 2. Тестовые видео (~11 мин)

    cd .vd-proto/torrent
    PYTHONIOENCODING=utf-8 ../proto-venv/Scripts/python.exe make_media.py

## 3. Локальный сид (оставить работать в отдельном терминале)

    PYTHONIOENCODING=utf-8 ../proto-venv/Scripts/python.exe local_seed.py

Первый запуск создаёт `torrents\local-test.torrent`; строка `READY` — готов.

## 4. Дисковый движок (при работающем сиде, по одному режиму за раз)

    PYTHONIOENCODING=utf-8 ../proto-venv/Scripts/python.exe probe_disk.py full
    PYTHONIOENCODING=utf-8 ../proto-venv/Scripts/python.exe probe_disk.py resume
    PYTHONIOENCODING=utf-8 ../proto-venv/Scripts/python.exe probe_disk.py select
    PYTHONIOENCODING=utf-8 ../proto-venv/Scripts/python.exe probe_disk.py locked

Не запускать во время build.bat — нагрузка на диск исказит цифры.

## 5. Метаданные по магнет-ссылке — ТОЛЬКО домашняя машина

Настоящий рой в офисной сети не запускать (решение владельца).

    mkdir -p "$TEMP/vd-torrent-proto/torrents"
    curl -o "$TEMP/vd-torrent-proto/torrents/ubuntu-26.04.1-desktop-amd64.iso.torrent" https://releases.ubuntu.com/26.04.1/ubuntu-26.04.1-desktop-amd64.iso.torrent
    curl -o "$TEMP/vd-torrent-proto/torrents/debian-13.7.0-amd64-netinst.iso.torrent" https://cdimage.debian.org/debian-cd/current/amd64/bt-cd/debian-13.7.0-amd64-netinst.iso.torrent
    PYTHONIOENCODING=utf-8 ../proto-venv/Scripts/python.exe probe_metadata.py --runs 5 --timeout 180 --download-seconds 60 --dead

При первом открытии порта Windows покажет запрос брандмауэра для Python и
сама создаст правила Inbound Block — см. AGENTS.md (Этап 2, ограничение).

## Результаты

Сырые события — `results\*.jsonl`. Сверять с цифрами домашней машины
(AGENTS.md → «Торрент-стриминг» → «Статус» и «История» 15.09.2026).

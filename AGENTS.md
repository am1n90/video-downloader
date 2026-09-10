# Video Downloader — память проекта

Прочитай этот файл перед началом работы. Здесь всё состояние проекта.

## Что это

Десктопный загрузчик видео/аудио (YouTube, VK, TikTok и др.): Fluent Design,
очередь с паузой/докачкой, библиотека, автообновление через GitHub Releases.
Пользователь — владелец репозитория am1n90. Язык общения — русский.

## Текущее состояние (10 сентября 2026, v1.0.1)

- Версия: **1.0.1** (APP_VERSION в config.py). Установленная копия на этой
  машине обновлена с 1.0.0 через автообновление — полный цикл проверен:
  автопроверка → InfoBar → скачивание → sha256 → тихая установка →
  перезапуск 1.0.1, данные пользователя целы
- Все фиксы P0/P1/P2 из аудита 09.09 выполнены и покрыты тестами:
  - P0: краш Библиотеки на 3-м refresh (строки в rows_vbox, постоянные
    элементы не трогаются); пауза не занимает слот параллельности;
    cancel() работает на PAUSED/QUEUED; гонки pause→resume /
    pause→resume→pause / pause→cancel закрыты схемой отложенного resume
    (_resume_requested + is_alive-проверки) и _cancel_intent; пауза
    доступна и в ANALYZING
  - P1: max_concurrent применяется на лету (maxConcurrentChanged);
    build.bat шаг 5 не виснет (main.py -selftest, авто-выход 8с);
    closeEvent останавливает UpdateWorker и миниатюры Библиотеки
    (воркеры миниатюр парентятся к странице, не к карточкам)
  - P2: офлайн отличим от «нет обновлений» (ManifestError/manifestError);
    кнопки QueueCard не пересоздаются на тиках (кэш _last_status);
    чистка мёртвого кода; guard двойного анализа; плейлист пишет в
    историю все файлы; кеш миниатюр Библиотеки (url→QImage, на сессию)
- Сборка: **Output\VideoDownloader-Setup-1.0.1.exe (139 МБ) + latest.json**
  (sha256 сходится). Артефакта 1.0.0 в Output больше НЕТ — для живого
  теста обновления с GitHub его нужно пересобрать (checkout старого
  кода или временный APP_VERSION=1.0.0 → build.bat → вернуть 1.0.1)
- YouTube в установленной 1.0.1 (проверено 10.09): вшит yt-dlp
  **2026.08.19**; пакета yt-dlp-ejs и JS-рантайма (Deno) НЕТ; при анализе
  yt-dlp выдаёт warning «No supported JavaScript runtime» — приложение
  его подавляет (no_warnings), на парсинг форматов это сейчас не влияет:
  4K-ролик отдаёт все высоты [2160…144] и 4K скачивается+merge OK.
  Рантайм-зависимость yt-dlp может «сломаться» в будущем — тогда
  добавить yt-dlp-ejs в requirements.txt + Deno в сборку
- Тесты: .vd-tests\ (core 33, manifest 4, library, gui smoke, p1
  concurrency, e2e download с паузой/докачкой, live fetch_info, диагностика
  установленного yt-dlp). Запуск: build-venv\Scripts\python.exe
  .vd-tests\<имя> с PYTHONIOENCODING=utf-8
- ВАЖНО: старая сборка (от 14:40 09.09) НЕ читала settings.json с BOM
  (PowerShell 5.1 Set-Content пишет BOM). Начиная с финальной 1.0.0
  (16:54) и в 1.0.1 чтение utf-8-sig. При ручном патче настроек — писать
  БЕЗ BOM. Данные владельца после инцидента восстановлены из dev-копии
  настроек (история загрузок и папка сохранения)

## Что осталось (по приоритету)

1. **Коммит фиксов 1.0.1 и релиз** (только после явного подтверждения
   владельца, самостоятельно НЕ публиковать):
   - коммит 7 изменённых файлов + set_version.ps1 + AGENTS.md (+ .vd-tests
     по желанию — добавить в git или оставить локально)
   - push в main → `gh release create v1.0.1 --target main`
   - порядок строго: коммит → push → релиз; latest.json ссылается на asset
   - gh CLI на этой машине НЕ установлен — установить (winget install
     GitHub.cli) и авторизовать (gh auth login) до релиза
2. **Артефакт 1.0.0 для живого теста обновления с GitHub**: пересобрать
   (APP_VERSION временно 1.0.0 → build.bat → вернуть 1.0.1) или принять
   решение тестировать с другой машины/учётки
3. Живой тест автообновления с GitHub (не локального сервера) после
   релиза v1.0.1: установленная копия должна увидеть 1.0.1... (для этого
   теста и нужна сборка 1.0.0; текущая установленная уже 1.0.1)
4. Чеклист чистой машины (README_BUILD.md): установка, анализ, 1080p
   merge, MP3, пауза/.part, кириллица/пробелы, деинсталляция
5. Дистрибуция: README для пользователей (SmartScreen), при желании
   LICENSE (MIT)

## Архитектура

- `main.py` — точка входа → `gui.run()`; `-selftest` — сборочный режим
  (окно + авто-выход через 8с, exit-код для build.bat)
- `gui.py` — PySide6 + qfluentwidgets (FluentWindow): страницы
  Загрузка/Библиотека/Настройки; `Bridge(QObject)` (itemChanged/
  queueChanged) передаёт события фоновых потоков в GUI; QThread-воркеры:
  AnalyzeWorker, ThumbWorker, UpdateWorker (check/download, cancel()),
  LibraryThumbWorker; LibraryPage: rows_vbox + кеш миниатюр; SettingsPage:
  тема/папка/параллельность/обновления (manifestReady/manifestError)
- `downloader.py` — ядро: `fetch_info()` (анализ), `DownloadManager`:
  планировщик с _thread/_resume_requested/_cancel_intent (отложенный
  resume, is_alive-проверки, пауза не занимает слот), формат yt-dlp:
  видео `bestvideo+bestaudio/best` или `bestvideo[height<=N]+bestaudio/...`,
  аудио → FFmpegExtractAudio MP3 192k; пауза/докачка через .part
- `updater.py` — автообновление (stdlib): `fetch_manifest` (при ошибках
  сети/формата raise ManifestError), `is_newer` (семвер), `download_file`
  (чанки/прогресс/отмена), `sha256_file`/verify (битый → файл удалён),
  `apply_update` — Popen `/VERYSILENT /NORESTART /CLOSEAPPLICATIONS/
  /SUPPRESSMSGBOXES /AUTOLAUNCH` (только при sys.frozen)
- `config.py` — APP_VERSION 1.0.1, REPO, DEFAULTS, load/save (utf-8-sig),
  история (макс 200, только существующие файлы); при sys.frozen —
  %LOCALAPPDATA%\VideoDownloader\settings.json, в dev — settings.json
  в корне проекта
- `set_version.ps1` — подстановка APP_VERSION в installer.iss (вызывается
  из build.bat; прежний inline powershell был сломан кавычками cmd)
- `assets/app.ico` — иконка 16–256px

## Сборка (build.bat)

build-venv (пересоздаётся) → requirements.txt + requirements-dev.txt →
PyInstaller `--noconsole --onedir --collect-all qfluentwidgets
--collect-all yt_dlp` → build_ffmpeg.ps1 (BtbN win64-gpl, кэш в
build-ffmpeg-cache/) → тестовый запуск `-selftest` (авто-выход) →
set_version.ps1 (версия в installer.iss) → ISCC (Inno Setup 6) →
build_manifest.ps1 → Output\latest.json (version/url/sha256).

Результат: `Output\VideoDownloader-Setup-<ver>.exe` + `Output\latest.json`.

## Установщик (installer.iss)

Per-user (`PrivilegesRequired=lowest`), `{localappdata}\Programs\
VideoDownloader`, без UAC, AppId фиксированный, RU/EN, ярлыки
Пуск+рабочий стол, PATH не трогается (ffmpeg — через `ffmpeg_location`
при sys.frozen), данные пользователя при деинсталляции сохраняются
(интерактивный вопрос; в /VERYSILENT — всегда).

## Автообновление

- Манифест: `releases/latest/download/latest.json` (стабильный алиас)
- Схема: автопроверка при старте (2.5с, ключ check_updates) или кнопка →
  InfoBar «Доступна версия X» → подтверждение → скачивание с прогрессом и
  отменой в %TEMP% → SHA-256 → тихая установка → перезапуск (через
  [Run]+Check:LaunchAfterUpdate); офлайн → «Не удалось проверить
  обновления» (не «нет обновлений»)
- SHA-256 = целостность, НЕ подпись (см. README_BUILD.md)

## GitHub

- Репо: https://github.com/am1n90/video-downloader (публичный), ветка main,
  единственный коммит a8e3c6c «Initial release» (исходники 1.0.0)
- Локально не запушено: фиксы 1.0.1 (7 файлов, set_version.ps1, AGENTS.md,
  .vd-tests/)
- .gitignore: settings.json, __pycache__/, build/, dist/, Output/,
  *.spec, ffmpeg*.exe, ffprobe*.exe, venv/, .venv/, build-ffmpeg-cache/
  (settings.json и бинари НИКОГДА не пушить — там личные данные)

## Правила работы

- НИЧЕГО не публиковать на GitHub (push, release) без явного
  подтверждения владельца
- downloader.py (логика загрузок) не трогать без явной задачи владельца
- Новые зависимости: только stdlib в рантайме; сборочные — в
  requirements-dev.txt
- Bump версии = правка APP_VERSION в config.py (одно место), build.bat
  разнесёт в installer.iss (set_version.ps1) и latest.json
- settings.json — личные данные владельца: не коммитить, не пушить,
  не показывать содержимое
- SmartScreen-предупреждения при установке — ожидаемое поведение
  (нет кодовой подписи), это не баг
- Релизы: тег v<ver> строго равен APP_VERSION; порядок: коммит →
  push в main → gh release create v<ver> --target main; latest.json
  загружается вместе с установщиком
- Если задача неоднозначна — выбрать разумное допущение и явно
  написать его в отчёте; правила молча не придумывать
- В конце каждого отчёта — отдельный список «Что не проверено»
- Команды для этой машины — под фактическую оболочку терминала Cline
  (сейчас Git Bash/MINGW64; проверять uname -s), одной строкой; gh при
  необходимости по полному пути. Многострочный текст (коммиты, notes
  релиза) передавать файлом UTF-8 без BOM во временной папке вне
  репозитория
- Не ломай работающее: не переписывать несвязанный код, не создавать
  второй механизм там, где уже есть рабочий, сохранять совместимость
  settings.json и истории загрузок
- Ищи корневую причину проблемы: изучить текущую реализацию и поток
  данных; временные обходы без понимания причины не допускаются

## История

- **10.09.2026, v1.0.1** — аудит + фиксы P0/P1/P2 (см. «Текущее
  состояние»), пересборка, полный локальный цикл обновления
  1.0.0→1.0.1. Артефакт Setup-1.0.0.exe удалён пересборкой Output
  (build.bat чистит Output)
- **09.09.2026, v1.0.0** — первый коммит на GitHub «Initial release»
  (14 файлов), сборка 1.0.0, локальный тест автообновления (манифест
  1.0.1); первоначальный план «релиз v1.0.0 первым» устарел — владелец
  решил релизить сразу початую 1.0.1
- Перенос проекта из прежнего расположения в профиле пользователя на
  `E:\OpenCode Project\video-downloader` (старый путь пуст и не действует),
  пересборка 1.89 ГБ артефактов, git-история из GitHub (клон)

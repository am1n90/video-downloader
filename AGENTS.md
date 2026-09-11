# Video Downloader — память проекта

Прочитай этот файл перед началом работы. Здесь всё состояние проекта.

## Что это

Десктопный загрузчик видео/аудио (YouTube, VK, TikTok и др.): Fluent Design,
очередь с паузой/докачкой, библиотека, автообновление через GitHub Releases.
Пользователь — владелец репозитория am1n90. Язык общения — русский.

## Текущее состояние (11 сентября 2026, v1.0.2)

- Версия: **1.0.2** (APP_VERSION в config.py). yt-dlp-ejs==0.8.0 + Deno v2.9.6
  вшиты; предупреждения yt-dlp идут в лог (logger), а не подавляются;
  config.load/save защищены от повреждённого/занятого settings.json (F1/F2/F3)
- Сборка: **Output\VideoDownloader-Setup-1.0.2.exe (168 198 089 байт, ~160 МБ)
  + latest.json** (sha256 сходится: FAC74E4E…70DBB). AC1–AC5 проверены на
  dist-сборке: ejs 0.8.0 PRESENT, deno 2.9.6 рядом с exe, анализ 4K без
  предупреждения (высоты [2160…27]), 4K-скачивание + merge OK (722 МБ webm)
- Установленная копия на этой машине — **1.0.2** (живое обновление
  1.0.1→1.0.2 с GitHub выполнено 11.09, все проверки пройдены). Релиз v1.0.2
  опубликован 11.09.2026 (Latest): тег v1.0.2 → 98b28e4 (= main, запушен);
  Setup-ассет 168 198 089 байт (digest sha256:fac74e4e…70dbb) и latest.json
  270 байт (sha256:326c79c4…c557) сверены с локальными байт-в-байт; алиас
  latest отдаёт 1.0.2. Публикация заранее одобрена владельцем условием
  «строка CP2 появилась → публикация в силе». CP2 подтверждён 11.09 10:43:
  без deno.exe в yt-dlp.log появилась WARNING «No supported JavaScript
  runtime», deno возвращён (sha256 2FF9493D…B7A6 — официальный)
- Пины: yt-dlp==2026.8.19 + yt-dlp-ejs==0.8.0 (согласованная пара, версия
  ejs — из METADATA yt-dlp). yt-dlp находит deno.exe рядом с exe (frozen:
  _find_exe ищет с dirname(sys.executable), конфиг js_runtimes не нужен)
- YouTube в 1.0.2 (проверено 11.09 на dist): предупреждения
  «No supported JavaScript runtime» больше нет — ejs+deno работают; в
  `%LOCALAPPDATA%\VideoDownloader\yt-dlp.log` уходят предупреждения yt-dlp
  (dev: корень проекта, покрыт *.log); события целостности настроек — в
  app.log (единый config.get_logger; каталог: frozen %LOCALAPPDATA%,
  dev — корень проекта)
- Тесты: .vd-tests\ — core 33, manifest 4, library, gui smoke, p1
  concurrency, e2e download, live fetch_info + НОВЫЕ: test_config_robust.py
  (40 проверок: битый JSON/CP1251/обрыв/не-словарь/занятый файл/ретраи
  чтения и replace/сценарий _skip_saving/non-JSON в save/BOM/одна
  .corrupt-копия за запуск), test_ytdlp_logger.py (8 проверок: logger в
  опциях, no_warnings убран, warning→yt-dlp.log, уровень/propagate,
  gitignore). Запуск: build-venv\Scripts\python.exe .vd-tests\<имя>
  с PYTHONIOENCODING=utf-8
- config (F1/F2/F3): load() никогда не бросает (ValueError=JSON+Unicode);
  повреждённый/занятый файл → одна копия settings.json.corrupt-<дата> за
  запуск; если копия не вышла — save() отключён до перезапуска
  (_skip_saving: закрытие не затрёт данные); save() атомарен (tmp+fsync+
  os.replace, 3 ретрая с паузой 0.1с, ловит любой OSError + TypeError/
  ValueError; исключение наружу не выходит — не блокирует closeEvent)
- ВАЖНО: старая сборка (от 14:40 09.09) НЕ читала settings.json с BOM
  (PowerShell 5.1 Set-Content пишет BOM). С 1.0.0 (16:54) чтение utf-8-sig.
  При ручном патче настроек — писать БЕЗ BOM. История 09.09: данные
  восстановлены ТОЛЬКО в dev-копию; история установленной копии была пуста
  с инцидента (не потеря при обновлении — обновление настройки не трогает);
  11.09 с согласия владельца перенесены 9 записей из dev-копии в
  установленную (backup settings.json.bak-20260910-233245)

## Что осталось (по приоритету)

1. ~~Живой тест обновления 1.0.1→1.0.2 с GitHub~~ — **ВЫПОЛНЕНО 11.09
   15:25–15:55, все проверки пройдены** (backup settings.json.bak-
   20260911-152528; детали в «Истории»). Далее по списку — пп. 2–7
2. **Библиотека (1.0.3)**: каждая загрузка показана 2 раза —
   в history 9 записей и только 5 уникальных путей (4 пути
   продублированы; дубли — в самой истории, dedup нет);
   показываются чужие файлы из папки загрузок — gui.py,
   `LibraryPage._entries()` (~строки 1050–1083, подпись «Папка
   загрузок»); нужно удаление записей с выбором и галочкой
   «Удалить также файлы с диска» и кнопка «Очистить библиотеку»
   (только список, без файлов). Дома истории нет — для проверки
   нужны тестовые данные
3. Чеклист чистой машины (README_BUILD.md): установка, анализ, 1080p
   merge, MP3, пауза/.part, кириллица/пробелы, деинсталляция
4. Перевести build_ffmpeg.ps1 на ASCII (работает, но кириллица
   без BOM читается PS 5.1 как ANSI)
5. Если settings.json не читается и сохранение отключено
   (_skip_saving) — показывать пользователю предупреждение в GUI
   (InfoBar); сейчас это видно только в app.log
6. Защита от второго экземпляра (single instance): иначе последний
   закрытый экземпляр затирает историю другого
7. Дистрибуция: README для пользователей (SmartScreen), при желании
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
- `config.py` — APP_VERSION 1.0.2, REPO, DEFAULTS, load/save (utf-8-sig),
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

- Репо: https://github.com/am1n90/video-downloader (публичный), ветка main
- Коммиты: a8e3c6c «Initial release» (1.0.0), b002b1f «1.0.1: fixes
  P0/P1/P2, tests, build tooling» (23 файла; текст коммита передавался
  файлом UTF-8 без BOM через git commit -F)
- **Релиз v1.0.1 опубликован 10.09.2026** (Latest): установщик
  139 054 674 байт + latest.json; алиас latest/download/latest.json
  отдаёт 1.0.1; тройная сверка sha256 (манифест/локальный/скачанный
  asset) сошлась; кириллица и «» в notes отображаются корректно.
  gh CLI 2.100.0 установлен (Program Files), авторизация GH_TOKEN.
  Порядок релиза 1.0.2 (draft-first, утверждён владельцем): gh release
  create --draft → отсоединённая загрузка setup.exe (Start-Process с
  наследованием env, НЕ WMI — env с GH_TOKEN в WMI не попадает) →
  latest.json → сверка размер байт-в-байт + digest==sha256 →
  edit --draft=false. Алиас latest ни на секунду не укажет на релиз
  без latest.json. GH_TOKEN никогда не выводить в чат/логи/файлы.
  Отсоединённый запуск сборки — run_build.ps1 (WMI), лог на каждую
  попытку свой: build-<дата-время>.log; таймаут поллинга — не падение
  сборки: смотреть лог и процессы, вторую сборку не запускать, пока
  первая не завершилась (BUILD_EXIT в хвосте лога)
- .gitignore: settings.json, __pycache__/, build/, dist/, Output/,
  *.spec, ffmpeg*.exe, ffprobe*.exe, venv/, .venv/, build-ffmpeg-cache/,
  *.log, *.tmp (settings.json и бинари НИКОГДА не пушить — там личные
  данные; логи отсекаются *.log)

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
- Файлы писать только инструментом редактирования, не через echo/Python
  в bash — экранирование портит символы
- .ps1 и .bat писать только ASCII (PowerShell 5.1 читает файлы без BOM
  как ANSI)

## История

- **11.09.2026, v1.0.2** — yt-dlp-ejs==0.8.0 + Deno v2.9.6 (build_deno.ps1,
  ASCII, обе официальные sha256 запинены) в сборке; предупреждения yt-dlp —
  в yt-dlp.log (logger вместо no_warnings, downloader.py +2 места);
  config F1/F2/F3 (load/save устойчивы к повреждённому/занятому
  settings.json); перенесена история 9 записей в установленную копию
  (backup bak-20260910-233245); AC8 скорректирован владельцем: до
  обновления 9 записей / после — те же 9 (4K-скачивание шло не через
  приложение, 10-й записи нет). Инцидент: build_deno.ps1, написанный
  Python-скриптом, был побит экранированием (0x08 вместо \b, LF) и уронил
  сборку; отсюда правила «писать инструментом редактирования» и «.ps1/.bat
  только ASCII». Сборка: BUILD_EXIT=0, Setup-1.0.2.exe 168 198 089 байт.
  Релиз v1.0.2 опубликован (Latest): тег→98b28e4=main, ассеты сверены
  байт-в-байт, алиас latest отдаёт 1.0.2; публикация заранее одобрена
  владельцем условием «строка CP2 появилась». CP2 подтверждён 10:43:
  без deno.exe в yt-dlp.log появляется WARNING «No supported JavaScript
  runtime», deno возвращён (официальный sha256). Живой тест обновления
  1.0.1→1.0.2 выполнен 11.09 15:25–15:55: патч update_manifest_url на
  GitHub-алиас (backup bak-20260911-152528, UTF-8 без BOM) → холодный
  старт 1.0.1 → InfoBar → «Обновить» (владелец) → скачивание с GitHub
  (download_count ассета +1) → SHA-256 → тихая установка → автоперезапуск.
  Проверки: установленная exe = dist байт-в-байт (sha256 86371404…AF71),
  deno 2.9.6 + ejs 0.8.0 в установленной копии, история 9 → те же 9,
  настройки целы, анализ aqz-KE-bpKQ: 4K есть, новых WARNING нет,
  холодный старт ×2 (0 процессов → окно → чистый выход)
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

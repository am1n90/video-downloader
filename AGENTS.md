# Video Downloader — память проекта

Прочитай этот файл перед началом работы. Здесь всё состояние проекта.

## Что это

Десктопный загрузчик видео/аудио (YouTube, VK, TikTok и др.): Fluent Design,
очередь с паузой/докачкой, библиотека, автообновление через GitHub Releases.
Пользователь — владелец репозитория am1n90. Язык общения — русский.

## Текущее состояние (13 сентября 2026, v1.0.4 — релиз опубликован)

- Версия: **1.0.4** (APP_VERSION в config.py). Изменения против 1.0.3:
  TikTok починен — curl_cffi==0.16.0 в requirements.txt (impersonation
  для yt-dlp, без него любая ссылка TikTok падает «Unexpected response
  from webpage request»); повтор переходящих ошибок в downloader.py
  (2 доп. попытки с паузой 2 с; постоянные ошибки — сразу; отмена/
  пауза прерывают ожидание; успешный extract_info не повторяется;
  дисковые ошибки без повторов; каждая попытка в yt-dlp.log,
  финальная ошибка с числом попыток); build.bat --collect-all
  curl_cffi + проверки бинарников в dist (_wrapper.pyd,
  libcurl-impersonate dll)
- **Релиз v1.0.4 опубликован 13.09.2026 00:57 (Latest)**: коммит
  d596066 «1.0.4: TikTok via curl_cffi + retry on transient errors»
  (push в main), draft-first как v1.0.2 (draft → exe 180 856 514 байт →
  latest.json → тройная сверка digest==sha256==latest.json →
  --draft=false). Тег v1.0.4 → d596066 = main; алиас latest/
  download/latest.json отдаёт 1.0.4; в релизе 2 ассета (exe + latest.
  json). gh CLI 2.100.0 установлен владельцем 13.09 (Program Files,
  авторизация keyring, am1n90; в bash этой машины gh не в PATH —
  вызывать по полному пути /c/Program Files/GitHub CLI/gh.exe).
  Проверки перед релизом: sha256 установщика == latest.json;
  журнал Defender чист (наши файлы не тронуты, детектов нет);
  интерактивная установка мастером (владелец) без ошибок чтения
  («.tmp не существует» не воспроизвелась — инцидент зависания
  bash-запуска 12.09 был разовым, лечится Start-Process как
  в updater.py)
- **Сборка 1.0.4 выполнена 12.09.2026 (BUILD_EXIT=0)**:
  Output\VideoDownloader-Setup-1.0.4.exe (180 856 514 байт) +
  latest.json (sha256 7945c735…c062). Inno Setup 6.7.3 установлен
  12.09 через winget per-user (без UAC; ISCC в %LOCALAPPDATA%\
  Programs\Inno Setup 6 — ровно где ищет build.bat). Установлена
  в %LOCALAPPDATA%\Programs\VideoDownloader (на этой машине раньше
  не стояла; settings.json %LOCALAPPDATA%\VideoDownloader — девственно-
  тестовый, backup не требовался). Проверки в установленной копии:
  AC2 (curl_cffi 0.16.0 + бинарники _internal\curl_cffi.libs\
  libcurl-impersonate*.dll), AC3 (TikTok ×2: анализ+видео 2.7 МБ+MP3
  1.4 МБ+ffprobe audio-поток; .vd-tests\check_installed_tiktok.py),
  AC8 (YouTube 4K 2160, ejs 0.8.0, deno 2.9.6; Instagram/VK анализ;
  check_installed_yt.py — поправлен exe_dir, deno теперь виден).
  Инцидент: первый запуск установщика из bash завис (CPU~0 после
  самораспаковки; убит); повтор через PowerShell Start-Process
  (install_vd.ps1 в %TEMP%, как updater.py) — SETUP_EXIT=0
- Рабочая машина: домашний ноутбук (свежий клон, см. «Историю» 11.09):
  Python 3.14.5 в PATH, build-venv пересоздан, requirements.txt стоит
  (PySide6 6.11.2, qfluentwidgets 1.11.3, curl_cffi 0.16.0 — колёса
  под 3.14 есть); Inno Setup 6 и gh CLI НЕ установлены (для сборки/релиза понадобятся:
  winget 1.29.290 / choco 2.7.2 доступны); GH_TOKEN не задан
- Сборка 1.0.2: Output\VideoDownloader-Setup-1.0.2.exe (168 198 089
  байт) + latest.json (sha256 сходится) — на старой машине; релиз
  v1.0.2 опубликован 11.09.2026 (Latest), алиас latest отдаёт 1.0.2,
  тег v1.0.2 → 98b28e4; живой тест обновления 1.0.1→1.0.2 с GitHub
  выполнен 11.09, все проверки пройдены
- Пины: yt-dlp==2026.8.19 + yt-dlp-ejs==0.8.0 + curl_cffi==0.16.0.
  Тройка согласована: ejs и curl_cffi обновлять ТОЛЬКО вместе с
  yt-dlp, версии брать из METADATA нового yt-dlp (Requires-Dist:
  yt-dlp-ejs==X; extra curl-cffi — допустимый диапазон, extra
  pin-curl-cffi — рекомендованная точная версия; для 2026.8.19 это
  curl-cffi>=0.5.10,<0.17, pin 0.16.0). curl_cffi 0.16.0 проверен
  12.09 на реальных ссылках TikTok (анализ+скачивание+MP3; 0.16.3
  тоже работал — выбран 0.16.0 как рекомендация yt-dlp). yt-dlp
  находит deno.exe рядом с exe (frozen: _find_exe ищет с
  dirname(sys.executable), конфиг js_runtimes не нужен);
  curl_cffi несёт бинарники в пакете (_wrapper.pyd +
  curl_cffi.libs\\libcurl-impersonate*.dll — build.bat проверяет
  их наличие в dist)
- Предупреждения yt-dlp и неудачные попытки повторов — в yt-dlp.log
  (logger в опциях; frozen %LOCALAPPDATA%\VideoDownloader\, dev —
  корень проекта, покрыт *.log); события целостности настроек — в app.log (единый config.get_logger)
- Тесты: .vd-tests\ — core **38**, retry **42** (1.0.4: подмена
  YoutubeDL; AC4/AC5/AC6 + счётчик попыток + записи в yt-dlp.log +
  правки владельца: без повторного скачивания после успешного
  extract_info, дисковые ошибки без повторов), manifest 4,
  check_installed_tiktok (AC3 в установленной копии), library (28 проверок), gui smoke,
  p1 concurrency, config_robust 40, ytdlp_logger 8, e2e download,
  live fetch_info, check_sources (analyze/download, range-фрагменты,
  кадры; sources.local.txt — личное, в .gitignore) + вспомогательные
  make_demo_data.py / live_demo_check.py. Запуск:
  build-venv\Scripts\python.exe .vd-tests\<имя> с PYTHONIOENCODING=utf-8
  (GUI-тесты — с QT_QPA_PLATFORM=offscreen)
- config (F1/F2/F3): load() никогда не бросает (ValueError=JSON+Unicode);
  повреждённый/занятый файл → одна копия settings.json.corrupt-<дата> за
  запуск; если копия не вышла — save() отключён до перезапуска
  (_skip_saving: закрытие не затрёт данные); save() атомарен (tmp+fsync+
  os.replace, 3 ретрая с паузой 0.1с, ловит любой OSError + TypeError/
  ValueError; исключение наружу не выходит — не блокирует closeEvent)
- ВАЖНО: старая сборка (от 14:40 09.09) НЕ читала settings.json с BOM
  (PowerShell 5.1 Set-Content пишет BOM). С 1.0.0 (16:54) чтение utf-8-sig.
  При ручном патче настроек — писать БЕЗ BOM. dev settings.json на этом
  ноутбуке — только тестовые данные (make_demo_data.py; реальные данные
  владельца — на установленной копии старой машины)

## Что осталось (по приоритету)

1. ~~Живой тест обновления 1.0.1→1.0.2 с GitHub~~ — **ВЫПОЛНЕНО 11.09
   15:25–15:55, все проверки пройдены** (backup settings.json.bak-
   20260911-152528; детали в «Истории»). Далее по списку — пп. 3–10
2. ~~Библиотека (1.0.3)~~ — **ВЫПОЛНЕНО 11.09.2026 (код + тесты, без
   сборки/релиза)**: дедуп истории (config.add_history — путь-замена,
   _dedup_history в load(), самоизлечение settings.json при первом
   save); Библиотека показывает только историю (сканирование папки
   загрузок и «Папка загрузок» убраны); чекбоксы + «Удалить» (диалог с
   галочкой «Удалить также файлы с диска», выкл. по умолчанию; с
   галочкой os.remove навсегда, только точный путь записи; неудача ->
   запись остаётся + InfoBar с именем; файла нет -> запись удаляется
   без ошибки); «Очистить данные библиотеки» (только список). Тесты:
   test_core 38 (+5 dedup), test_library переписан (28 проверок),
   весь офлайн-набор зелёный. APP_VERSION 1.0.3 (сборка/релиз —
   отдельной задачей)
3. ~~**Проверка источников на реальных ссылках** (TikTok, Instagram,
   VK): анализ, водяной знак, нужен ли вход (Instagram часто требует
   cookies)~~ — **ВЫПОЛНЕНО 12.09.2026** (см. «Историю»): TikTok ×2,
   Instagram reel, VK — анализ, водяные знаки, вход, скачивание +
   кадры; .vd-tests\check_sources.py + sources.local.txt (в .gitignore)
4. **1.0.4 Фрагмент**: ползунок с двумя ручками (начало/конец), над
   ручками время, между ними длительность фрагмента, поля точного
   ввода, галочка «Точная обрезка (медленнее)» (download_ranges +
   force_keyframes_at_cuts); проверить паузу/докачку при фрагменте.
   Замечание из проверки источников 12.09: VK HLS-форматы на
   длинном видео зависали на медленном CDN (две попытки по 45+ мин
   без прогресса) — фрагменты надёжнее качать прямым форматом
   (url1080); download_ranges на прямом формате работает (30с
   фрагмент 1080p скачан, звук есть)
5. ~~**TikTok не работает в собранной программе**~~ — **ВЫПОЛНЕНО
   12.09.2026, v1.0.4**: curl_cffi ==0.16.0 в requirements.txt
   (рекомендация yt-dlp из METADATA pin-curl-cffi; проверен 12.09 на
   реальных ссылках TikTok), build.bat --collect-all curl_cffi +
   ASCII-проверки бинарников (_wrapper.pyd, libcurl-impersonate*.dll)
   в dist, повтор переходящих ошибок в downloader.py (см. «Историю»
   12.09). **Сборка и проверка установленной копии тоже выполнены
   12.09** (AC2/AC3/AC8 — см. «Текущее состояние»); ~~осталось:
   коммит/push/релиз v1.0.4~~ — релиз опубликован 13.09 (см.
   «Текущее состояние»)
6. **1.0.5 Галочка «Без водяного знака» и вход для Instagram** — по
   итогам проверки источников: вход оказался не нужен (публичный
   reel анализируется и скачивается без cookies), yt-dlp выбирает
   чистые форматы (bytevc1), watermarked-формат («download»,
   водяной знак в note) не выбирается — возможно, галочка не нужна
7. Чеклист чистой машины (README_BUILD.md): установка, анализ, 1080p
   merge, MP3, пауза/.part, кириллица/пробелы, деинсталляция
8. Перевести build_ffmpeg.ps1 на ASCII (работает, но кириллица
   без BOM читается PS 5.1 как ANSI)
9. Если settings.json не читается и сохранение отключено
   (_skip_saving) — показывать пользователю предупреждение в GUI
   (InfoBar); сейчас это видно только в app.log
10. Защита от второго экземпляра (single instance): иначе последний
   закрытый экземпляр затирает историю другого
11. Дистрибуция: README для пользователей (SmartScreen), при желании
   LICENSE (MIT)

## Архитектура

- `main.py` — точка входа → `gui.run()`; `-selftest` — сборочный режим
  (окно + авто-выход через 8с, exit-код для build.bat)
- `gui.py` — PySide6 + qfluentwidgets (FluentWindow): страницы
  Загрузка/Библиотека/Настройки; `Bridge(QObject)` (itemChanged/
  queueChanged) передаёт события фоновых потоков в GUI; QThread-воркеры:
  AnalyzeWorker, ThumbWorker, UpdateWorker (check/download, cancel()),
  LibraryThumbWorker; LibraryPage: rows_vbox + кеш миниатюр + чекбоксы
  записей + «Удалить» (ConfirmDeleteDialog: галочка «Удалить также
  файлы с диска», выкл. по умолчанию, os.remove только точный путь
  записи) + «Очистить данные библиотеки» (только список); SettingsPage:
  тема/папка/параллельность/обновления (manifestReady/manifestError)
- `downloader.py` — ядро: `fetch_info()` (анализ), `DownloadManager`:
  планировщик с _thread/_resume_requested/_cancel_intent (отложенный
  resume, is_alive-проверки, пауза не занимает слот), формат yt-dlp:
  видео `bestvideo+bestaudio/best` или `bestvideo[height<=N]+bestaudio/...`,
  аудио → FFmpegExtractAudio MP3 192k; пауза/докачка через .part;
  повтор переходящих ошибок (1.0.4): RETRY_ATTEMPTS=3, пауза 2 с,
  _is_retryable (RETRYABLE_MARKERS — сеть/5xx/извлечение; NO_RETRY_
  MARKERS — удалено/приватно/нужен вход/диск и пр., слова с границами
  \b), успешный extract_info не повторяется (_finish_item вне цикла),
  каждая неудачная попытка — WARNING в yt-dlp.log, финальная ошибка —
  «... (после N попыток)»; ожидание между попытками прерывается
  отменой/паузой (item._cancel.wait)
- `updater.py` — автообновление (stdlib): `fetch_manifest` (при ошибках
  сети/формата raise ManifestError), `is_newer` (семвер), `download_file`
  (чанки/прогресс/отмена), `sha256_file`/verify (битый → файл удалён),
  `apply_update` — Popen `/VERYSILENT /NORESTART /CLOSEAPPLICATIONS/
  /SUPPRESSMSGBOXES /AUTOLAUNCH` (только при sys.frozen)
- `config.py` — APP_VERSION 1.0.4, REPO, DEFAULTS, load/save (utf-8-sig),
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

- Работать только внутри папки проекта. Вне её разрешено только:
  %LOCALAPPDATA%\VideoDownloader и %LOCALAPPDATA%\Programs\
  VideoDownloader (с backup перед изменением) и %TEMP% (удалять после
  себя). Любое другое место на диске — сначала спросить владельца
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
- Пути в скриптах и командах всегда в кавычках — проект может лежать в
  папке с пробелом (сейчас C:\Claude Projects\...)

## История

- **12.09.2026 (вечер), v1.0.4 — TikTok + повтор, код+тесты, без
  сборки/релиза**: curl_cffi==0.16.0 (по правке владельца — сначала
  0.16.0 как рекомендация yt-dlp из METADATA pin-curl-cffi, живая
  проверка TikTok ×2 анализ + скачивание видео + MP3 прошла; пин
  0.16.0; 0.16.3 работал 12.09 утром тоже). Повтор переходящих
  ошибок: RETRY_ATTEMPTS=3 / RETRY_PAUSE_SECONDS=2.0; _is_retryable
  двухступенчатый — сначала RETRYABLE_MARKERS (timed out/timeout/
  connection/ssl/certificate/unexpected response/unable to extract/
  unable to download webpage/temporary/reset by peer/internal server
  error/500/502/503/504), затем NO_RETRY_MARKERS (unavailable/not
  available/private/login/sign in/log in/unsupported url/removed/
  deleted/age/copyright/geo/404/not a video) с границами слов \b
  (голое «age» ловится в «webpage»/«message» — переходящие ошибки
  TikTok остались бы без повтора); неизвестная ошибка — повторяем.
  Тот же экземпляр YoutubeDL на все попытки (докачка .part и cookies
  сохраняются); DownloadCancelled — наружу без повторов; ожидание
  между попытками — item._cancel.wait(2.0): отмена → сразу ERROR
  «Отменено», пауза → PAUSED. extract_info(download=True) и финальная
  фаза (_finish_item: COMPLETED, files, _notify) разделены — падение
  после успешного extract_info не повторяет скачивание. Дисковые
  ошибки (no space left/not enough space/permission denied/файл
  занят/unable to write) — без повторов. Финальная ошибка «исходный
  текст (после N попыток)» одной строкой (GUI однострочный; тип
  ошибки yt-dlp не сохраняется — gui.py различает только str(exc)),
  каждая попытка — WARNING «attempt k/N failed (url): …» в yt-dlp.log.
  Живое подтверждение повтора: TikTok MP3 упал с первой попытки
  («Unable to extract universal data for rehydration»), yt-dlp.log
  записал attempt 1/3 failed, повтор прошёл — задача COMPLETED.
  Тесты: новый .vd-tests\test_retry.py — 42 PASS (классификация
  20, AC4 анализ+скачивание, AC5 постоянные 1 попытка, AC6 отмена/
  пауза в ожидании <0.35с, счётчик в ошибке, записи в логе, правки
  владельца: падение после extract_info без повторов, дисковые
  ошибки 1 попытка); весь
  офлайн-набор зелёный (core 38, manifest 4, config_robust 40,
  ytdlp_logger 8, library, smoke, p1). Проверки источников на
  0.16.0: TikTok ×2 (анализ+скачивание+кадры+MP3), Instagram
  анализ, VK анализ, YouTube 4K (2160) на месте. build.bat:
  --collect-all curl_cffi + ASCII-проверки _wrapper.pyd и
  libcurl-impersonate*.dll) в dist (подтверждено сборкой 12.09:
  проверки в build.bat отработали, бинарники в dist и в установленной
  копии). APP_VERSION 1.0.4. Правки владельца после ревью диффа
  (вечер 12.09): _run_attempt повторяет только extract_info —
  _finish_item (COMPLETED/files/_notify) вынесен из retry-цикла,
  падение после успешного extract_info не перекачивает файл;
  дисковые ошибки (no space left/not enough space/permission
  denied/файл занят/unable to write) добавлены в NO_RETRY — 1 попытка;
  тип ошибки yt-dlp в финале не сохраняется (gui.py различает только
  str(exc) — одинаково для всех исключений)
- **12.09.2026, проверка источников (п.3 «Что осталось») — выполнено**
  (.vd-tests\check_sources.py + sources.local.txt, личное — в
  .gitignore; ffmpeg в dist\VideoDownloader через build_ffmpeg.ps1).
  Ссылки владельца: TikTok ×2 (vt.tiktok), Instagram reel, VK видео
  (3ч16м). Итоги: Instagram — вход/cookies не нужны, выбран
  1080×1920 DASH, скачивание 12.2 МБ + звук + кадры; TikTok —
  yt-dlp 2026.8.19 БЕЗ curl_cffi падает (Unexpected response,
  impersonation), с curl_cffi (установлен только в build-venv) —
  анализ и скачивание ок, yt-dlp сам выбирает чистые форматы
  bytevc1_720p (водяной знак только у download-формата, не
  выбирается) — см. п.5 «Что осталось» (curl_cffi в requirements/
  сборке); VK — анализ ок (dash_sep-6 1080p + аудио), полное
  скачивание не проводилось (9 ГБ), фрагмент 0-30с через
  download_ranges: HLS-форматы дважды зависали на CDN (45+ мин,
  убиты), прямой формат url1080 скачался (12.8 МБ, 30.08с, h264+
  aac, 3 кадра). VK-SSL: один прогон анализа упал на
  CERTIFICATE_VERIFY_FAILED (переходящее, повтор прошёл; сертификат
  vk.com легитимный Google Trust Services). Коммит/push —
  по подтверждению владельца
- **11.09.2026 (вечер), Библиотека 1.0.3 — код+тесты, без сборки/релиза**
- **11.09.2026 (вечер), Библиотека 1.0.3 — код+тесты, без сборки/релиза**
  (новая машина — домашний ноутбук, свежий клон 90a47cb, отделение от
  старой машины после релиза 1.0.2): config.add_history — путь-замена
  (insert(0) — единственная точка записи, «самая новая» подтверждена
  кодом), _dedup_history в load() (первая встречная = самая новая),
  remove_history/clear_history; gui: _entries() только история
  (сканирование папки и MEDIA_EXTS убраны), чекбоксы строк,
  «Удалить» (ConfirmDeleteDialog с галочкой «Удалить также файлы с
  диска», выкл. по умолчанию, «Файлы будут удалены навсегда»; с
  галочкой os.remove ровно путь записи — без папок/масок/.part;
  занятый файл → запись остаётся + InfoBar с именем; файла нет →
  запись удаляется), «Очистить данные библиотеки» (только список),
  ru_records. Тесты: test_core 38 (+5), test_library переписан (28),
  smoke/robust/logger/manifest/p1 зелёные; live_demo_check на реальном
  dev settings.json: 6 записей → 3 строки, дедуп самоизлечением
  записался в файл. Окружение: build-venv на Python 3.14.5 (колёса
  PySide6/qfluentwidgets есть), Inno Setup/gh не установлены. Инцидент:
  settings.json тестовых данных, писавшийся bash-командой, побился
  экранированием (контрольный символ в пути) — по правилу «файлы
  писать редактором» пересоздан через config.save()
  (make_demo_data.py); заодно правило кавычек добавлено выше

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

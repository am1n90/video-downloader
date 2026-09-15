# Video Downloader — память проекта

Прочитай этот файл перед началом работы. Здесь всё состояние проекта.

## Что это

Десктопный загрузчик видео/аудио (YouTube, VK, TikTok и др.): Fluent Design,
очередь с паузой/докачкой, библиотека, автообновление через GitHub Releases.
Пользователь — владелец репозитория am1n90. Язык общения — русский.

## Текущее состояние (14 сентября 2026, v1.0.5 — релиз опубликован)

- Версия: **1.0.5** (APP_VERSION в config.py). Изменения против 1.0.4:
  скачивание фрагмента (галочка «Скачать только фрагмент», RangeSlider
  двумя ручками, поля мм:сс/ч:мм:сс, ~размер, «Точная обрезка», имя
  «[clip Ns-Ms]», сторож _fragment_watch, пауза скрыта, ContextVar-фикс
  ffmpeg) + _kill_fragment_ffmpeg: отмена фрагмента убивает
  ffmpeg-сироту (CIM Win32_Process, фильтр: ParentProcessId == наш PID
  + [clip Ns-Ms] + normcase(output_dir), os.kill(pid, 9))
- **Релиз v1.0.5 опубликован 14.09.2026 (Latest)**: коммит 7374b73
  «1.0.5: fragment download + kill orphan ffmpeg on cancel» (push в
  main) + 1a197f9 «sync: 2026-09-14-0747» (sync.bat закоммитил себя и
  pull.bat). Порядок как в 1.0.4: draft → exe 180 880 028 байт (sha256
  b62d0513…5eee) + latest.json → тройная сверка digest==sha256==
  latest.json → --draft=false. Тег v1.0.5 → 7374b73 = main; алиас
  latest/download/latest.json отдаёт 1.0.5; в релизе 2 ассета
- **Сборка 1.0.5 выполнена 14.09.2026**: Output\VideoDownloader-
  Setup-1.0.5.exe (180 880 028 байт) + latest.json (sha256 сошёлся);
  все 7 шагов build.bat OK (curl_cffi-бинарники в dist, selftest exe).
  Установка /VERYSILENT — INSTALL_EXIT=0; состав установленной копии
  цел (exe/ffmpeg/ffprobe/deno/_wrapper.pyd/libcurl-impersonate),
  selftest exit=0; backup settings.json.bak-20260914-070200.
  Живые проверки установленной копии
  (.vd-tests\check_installed_fragment.py — **10 PASS, 0 FAIL**):
  YouTube-фрагмент 0:30–1:00 precise — отмена при РЕАЛЬНО работающем
  ffmpeg (CIM-дочерний pid=17164, tasklist count=1) → задача ERROR
  «Отменено» за 1.2 с, ffmpeg исчез из tasklist через 1.7 с — сироты
  нет; полное видео 24.2 МБ webm + audio-поток (не сломано); MP3-
  фрагмент 30.0 с + audio-поток
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
  **с 15.09.2026 сборка на Python 3.13.15** (per-user, %LOCALAPPDATA%\
  Programs\Python\Python313, build.bat — `py -3.13 -m venv`; Python 3.14.5
  остаётся в PATH и для сборки не используется), build-venv 3.13
  (PySide6 6.11.2, qfluentwidgets 1.11.3, curl_cffi 0.16.0, yt-dlp
  2026.8.19); proto-venv 3.13 с libtorrent 2.1.1 в .vd-proto\; Inno Setup 6.7.3 и gh CLI 2.100.0 установлены (ISCC
  в %LOCALAPPDATA%\Programs\Inno Setup 6; gh — keyring, am1n90, в bash
  не в PATH — полный путь /c/Program Files/GitHub CLI/gh.exe)
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
  p1 concurrency, config_robust **48** (1.0.5: +8 на consume_load_warning,
  п.9 «Что осталось»), config_warning_gui **7** новый (1.0.5: InfoBar
  предупреждения о повреждённом settings.json, offscreen), ytdlp_logger 8,
  e2e download, live fetch_info, check_sources (analyze/download,
  range-фрагменты, кадры; sources.local.txt — личное, в .gitignore) +
  вспомогательные make_demo_data.py / live_demo_check.py. Запуск:
  build-venv\Scripts\python.exe .vd-tests\<имя> с PYTHONIOENCODING=utf-8
  (GUI-тесты — с QT_QPA_PLATFORM=offscreen)
- config (F1/F2/F3): load() никогда не бросает (ValueError=JSON+Unicode);
  повреждённый/занятый файл → одна копия settings.json.corrupt-<дата> за
  запуск; если копия не вышла — save() отключён до перезапуска
  (_skip_saving: закрытие не затрёт данные); save() атомарен (tmp+fsync+
  os.replace, 3 ретрая с паузой 0.1с, ловит любой OSError + TypeError/
  ValueError; исключение наружу не выходит — не блокирует closeEvent).
  1.0.5 (п.9): load() кладёт результат в consume_load_warning()
  ({"skip_saving": bool, "corrupt_path": str|None}, сбрасывается при
  отдаче — одноразово за сессию); gui.run() показывает InfoBar один раз
  при старте (MainWindow.show_config_warning): копия создана — warning
  с кнопками «Открыть папку»/«Скопировать путь» на .corrupt-файл;
  _skip_saving — error без кнопок (данные не сохранятся вообще)
- ВАЖНО: старая сборка (от 14:40 09.09) НЕ читала settings.json с BOM
  (PowerShell 5.1 Set-Content пишет BOM). С 1.0.0 (16:54) чтение utf-8-sig.
  При ручном патче настроек — писать БЕЗ BOM. dev settings.json на этом
  ноутбуке — только тестовые данные (make_demo_data.py; реальные данные
  владельца — на установленной копии старой машины)

## Что осталось (по приоритету)

1. ~~Живой тест обновления 1.0.1→1.0.2 с GitHub~~ — **ВЫПОЛНЕНО 11.09
   15:25–15:55, все проверки пройдены** (backup settings.json.bak-
   20260911-152528; детали в «Истории»). Далее по списку — пп. 3–10
1b. ~~Живое автообновление на второй (офисной) машине~~ —
   **ВЫПОЛНЕНО 15.09.2026**: 1.0.2 → 1.0.5 через GitHub (не ручная
   установка) на E:\OpenCode Project (пользователь am1n, отдельно
   от домашней машины, где шла разработка 1.0.5). settings.json,
   история и настройки целы, deno и curl_cffi на месте. Подтверждено
   сверкой: установленный VideoDownloader.exe содержит APP_VERSION
   1.0.5 (чтение байткода config.pyz без запуска), sha256 установщика
   из %TEMP% (b62d0513…5eee) совпал с digest ассета релиза v1.0.5 на
   GitHub
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
4. ~~**Фрагмент (планировался в 1.0.4)**: ползунок с двумя ручками…~~ —
   **ВЫПОЛНЕНО 13.09.2026, v1.0.5 (код + тесты, без сборки/релиза)**:
   галочка «Скачать только фрагмент», слайдер + поля мм:сс/ч:мм:сс
   (синхронны в обе стороны, конец >= начала+1с), ~размер от
   выбранного качества, «Точная обрезка (медленнее)», имя
   «[clip Ns-Ms]» (латиница/цифры — правило владельца), сторож
   фрагмента (отмена сразу; VK-зависание → понятная ошибка через
   10 мин без роста файлов), пауза скрыта (FFmpegFD без
   progress-hooks), ContextVar-фикс ffmpeg. Детали — «История»
   13.09; сборка/релиз — **ВЫПОЛНЕНО 14.09.2026** (см. «Текущее
   состояние» и «Историю»)
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
7. ~~Чеклист чистой машины (README_BUILD.md): установка, анализ, 1080p
   merge, MP3, пауза/.part, кириллица/пробелы, деинсталляция~~ —
   **ВЫПОЛНЕНО 15.09.2026** (см. «Историю»): найдены и исправлены
   два бага деинсталлятора (installer.iss), остальные пункты чеклиста
   прошли на v1.0.6 без замечаний
8. Перевести build_ffmpeg.ps1 на ASCII (работает, но кириллица
   без BOM читается PS 5.1 как ANSI)
9. ~~Если settings.json не читается и сохранение отключено
   (_skip_saving) — показывать пользователю предупреждение в GUI
   (InfoBar); сейчас это видно только в app.log~~ — **ВЫПОЛНЕНО
   15.09.2026** (код + тесты, без сборки/релиза; см. «Историю»)
10. ~~Защита от второго экземпляра (single instance)~~ — **ВЫПОЛНЕНО
   15.09.2026** (см. «Историю»)
11. Дистрибуция: README для пользователей (SmartScreen), при желании
   LICENSE (MIT)
12. ~~**1.0.6: убивать ffmpeg-сироту при отмене фрагмента**~~ —
    **ВЫПОЛНЕНО 13.09 (1.0.5)**: _kill_fragment_ffmpeg в
    _fragment_watch (отмена + stall, не на паузе). CIM-запрос
    PowerShell Get-CimInstance Win32_Process (Name='ffmpeg.exe'),
    фильтр: ParentProcessId == PID нашего процесса И суффикс
    [clip Ns-Ms] в cmdline И normcase(output_dir) в cmdline →
    os.kill(pid, 9). Ошибки в yt-dlp.log, «не найдено» молча.
    Тесты: test_fragment §8 (4 сценария: три условия, OSError ×2,
    time_range=None).
    (1.0.5 выпущен 14.09 — kill-сироты проверен вживую в установленной
    копии: ffmpeg исчез из tasklist через 1.7 с после отмены).
    ~~Осталось: VK фрагмент прямым форматом (url1080)~~ —
    **ВЫПОЛНЕНО 15.09.2026** (код + тесты, без сборки/релиза; см.
    «Историю»): VK + фрагмент -> best[protocol~='^https?$'] перед
    прежним селектором, _is_vk_url по хосту (vk.com/vk.ru/vkvideo.ru)
13. Мелочи с чеклиста чистой машины 15.09.2026 (не блокируют релиз):
    завышенный размер в списке качеств (1080p показан ~255 МБ, реально
    скачано 128 МБ — оценка берётся по формату с наибольшим ожидаемым
    размером, а yt-dlp может выбрать более компактный кодек, напр. AV1
    вместо AVC1); во время загрузки в строке показывается ссылка вместо
    названия ролика; путь папки из диалога выбора сохраняется с прямыми
    слэшами (`C:/Users/...`) вместо `\`
14. **Торрент-стриминг фильмов/сериалов** — большая задача на 17-23
    сессии, начата 15.09.2026; решения и план — раздел «Торрент-
    стриминг» ниже. Текущий этап — там же («Статус»)

## Торрент-стриминг (план, утверждён владельцем 15.09.2026)

### Решения разведки (1-7) + уточнения владельца

1. Сборка на **Python 3.13** вместо 3.14 (у libtorrent нет колеса
   cp314; проверено 15.09: libtorrent-2.1.1-cp313-cp313-win_amd64.whl
   5.0 МБ есть, для 3.14 — нет). Установить на обеих машинах (per-user,
   %LOCALAPPDATA%\Programs\Python\Python313 — разрешено владельцем),
   весь регресс после перехода. build.bat создаёт venv через
   `py -3.13`, не через python из PATH
2. **libtorrent — исключение из «только stdlib в рантайме»** (как
   curl_cffi). Пин точной версии в requirements.txt, build.bat
   --collect-all libtorrent + проверка DLL в dist
3. **Никакого встроенного поиска/каталога контента** — только
   магнет-ссылки и .torrent-файлы, которые вставляет сам пользователь
4. **Раздача после скачивания включена по умолчанию**, индикатор в
   интерфейсе, выключается в Настройках. Закрытие окна останавливает
   раздачу (трея нет — в первой версии, решение владельца)
5. **Карточки**: сначала проверить доступность TMDB из региона
   владельца. Недоступен → TVmaze для сериалов, остальное без карточек
   (имя файла + размер + качество), карточки позже отдельно
6. **Лицензии**: на первом этапе ничего под GPL (libtorrent — BSD;
   внешний плеер VLC/mpv отдельным процессом, не встраивать; не
   TorrServer). Лицензия проекта — вопрос отложен
7. **Архитектура — переключатель режима приложения** наверху окна:
   «Video Downloader» | «Torrent», при смене режима меняется весь
   набор вкладок. Реализация (вариант B, выбран по разбору
   FluentWindow в qfluentwidgets 1.11.3):
   - одно окно MainWindow(FluentWindow); SegmentedWidget
     (qfluentwidgets) в titleBar.hBoxLayout после titleLabel
   - все страницы обоих режимов добавляются в stackedWidget один раз
     (addSubInterface), реестр «страница → режим»; при смене режима
     пункты чужого режима скрываются
     navigationInterface.widget(routeKey).setVisible(...), переход на
     последнюю страницу режима, история qrouter очищается (кнопка
     «Назад» не уводит в скрытый режим)
   - settings["app_mode"] ("video" по умолчанию; новый ключ в DEFAULTS
     — совместимость settings.json сохраняется)
   - DownloadManager и торрент-движок живут на уровне окна — загрузки
     одного режима идут, пока открыт другой
   - **одна общая страница «Настройки»** (внизу, в обоих режимах) с
     группами «Общие» / «Video Downloader» / «Torrent» (решение
     владельца)
   - торрентовый GUI — новый модуль gui_torrent.py; в gui.py меняются
     только MainWindow/_setup_navigation/switch_to/closeEvent;
     downloader.py не трогается
   - отклонены: removeInterface/addSubInterface при каждой смене
     (логика count()==1, порядок пунктов, qrouter); две вложенные
     оболочки во внешнем QStackedWidget (уход от FluentWindow:
     resizeEvent со сдвигом titleBar на 46px, InfoBar, отступы)

Правила прототипа (владелец): прототип — `.vd-proto\` в проекте, свой
proto-venv на 3.13; **в git только код** (.vd-proto\torrent\*.py,
README_PROTO.md, media\*.srt — решение владельца 15.09: офисная машина
повторяет эксперимент с нуля для сверки цифр); venv, results\,
RESULTS.md и тестовые видео/torrent — не коммитятся; тестовые данные — %TEMP%\
vd-torrent-proto (удалять после); mpv/VLC — portable-архивы в %TEMP%,
без установки. Контент — только легальный: локальный сид (тестовые
MKV/MP4, сгенерированные ffmpeg lavfi, .torrent через
lt.create_torrent, раздача по 127.0.0.1) + открытые фильмы Blender
(BBB, Sintel), ISO Ubuntu/Debian. **Настоящий рой — только на домашней
машине**; на офисной — только переход на 3.13 и локальный сид.

### Этапы

- **Этап 0 (2 сессии) — прототип вне кода приложения**
  - 0.1: Python 3.13 (дом) + build.bat `py -3.13` + полный регресс
    (офлайн-набор, check_sources, build.bat с selftest, установка с
    backup, check_installed_*; без релиза); proto-venv + libtorrent;
    замеры: (1) метаданные по магнет-ссылке — время до первого пира и
    до metadata_received_alert, 5 прогонов, медиана/максимум (цель
    популярной раздачи < 30 с), с трекерами/только DHT, холодный/
    тёплый DHT, мёртвая ссылка — таймаут 3 мин; (2) дисковый движок —
    какой по умолчанию (mmap/pread), Private Bytes/Working Set при
    1+ ГБ, кириллица+пробелы в пути, чтение недокачанного файла другим
    процессом во время записи, sparse/предвыделение, нехватка места,
    kill + перезапуск с fastresume, file_priorities; (3) колесо — DLL,
    import в чистом venv, Защитник (Get-MpThreatDetection до/после,
    MpCmdRun -Scan по колесу, CPU MsMpEng), окно брандмауэра на
    входящий порт и пиры при «Отмена» без админа; (4) доступность
    TMDB (api.themoviedb.org без ключа → 401 = доступен;
    image.tmdb.org) и api.tvmaze.com
  - 0.2: HTTP Range-сервер (stdlib ThreadingHTTPServer): 206/
    Content-Range/416/HEAD, параллельные соединения, обрыв клиента,
    только 127.0.0.1 + токен в пути + проверка Host,
    set_piece_deadline на запрошенный диапазон, приоритет начала/конца
    файла; mpv и VLC: до первого кадра (< 15 с), перемотка на 80%
    (< 10 с), подвисания за 10 мин по логам, MP4 с moov в конце,
    переключение дорожек/субтитров MKV; ffprobe по URL — время,
    число Range-запросов/байт, сверка с ffprobe по полному файлу;
    мини-сборка PyInstaller --collect-all libtorrent (размер, скан
    Защитником, запуск без админа)
  - итог: .vd-proto\torrent\RESULTS.md (цифры + вердикт по каждому
    замеру), выводы и чеклист офисной машины — в AGENTS.md
- **Этап 1 (4 сессии) — движок, режим «Torrent», полное скачивание**
  - 1.1: каркас переключателя без торрентов (SegmentedWidget, реестр
    режимов, app_mode, страницы-заглушки «Торренты»/«Библиотека»,
    группы в Настройках); test_mode_switch.py (offscreen: туда/обратно,
    видимость пунктов, последняя страница, «Назад» не в чужой режим,
    видеозагрузка не прерывается, режим после перезапуска); плюс
    раскладка скрытых пунктов в развёрнутой/свёрнутой навигации,
    перетаскивание/двойной клик по заголовку, InfoBar о повреждённых
    настройках, single instance, -selftest, весь регресс
  - 1.2: torrent_engine.py без GUI — сессия в своём потоке,
    pop_alerts → Bridge-сигналы, магнет/.torrent, метаданные, выбор
    файлов, пауза/докачка, fastresume в %LOCALAPPDATA%\VideoDownloader\
    torrents\resume\<infohash> (не в settings.json), раздача по
    умолчанию, завершение с save_resume_data и ограничением по времени;
    test_torrent_engine.py на локальном сиде (loopback, без интернета)
  - 1.3: GUI режима — добавление (магнет/.torrent), дерево файлов с
    галочками и размерами, карточки очереди (прогресс, скорость, пиры,
    ETA, индикатор раздачи), пауза/продолжить/удалить (галочка «удалить
    файлы» по образцу ConfirmDeleteDialog), группа «Torrent» в
    Настройках (папка, раздача вкл/выкл, порт), closeEvent
  - 1.4: отдельная Библиотека торрентов (отдельный ключ истории, тот
    же атомарный механизм config.py — второго не делать); build.bat
    --collect-all libtorrent + ASCII-проверки DLL, -selftest импортирует
    libtorrent; автообновление с /CLOSEAPPLICATIONS не теряет resume;
    деинсталлятор с мьютексом; Защитник; установленная копия
  - не входит: стриминг, ассоциация magnet:/.torrent, трей
- **Этап 2 (3-4 сессии)**: просмотр во время закачки через внешний
  плеер (VLC/mpv), HTTP Range-сервер в приложении
  - **Известное ограничение (владелец, 15.09.2026)**: при первом
    открытии порта Windows показывает запрос «Разрешить общедоступным и
    частным сетям доступ…» и сама создаёт правила Inbound Block (профиль
    Public) для exe — даже если пользователь просто закрыл/отменил
    запрос. Для метаданных и скачивания входящие не обязательны (Ubuntu
    16.7 МБ/с при Block, 0.1), но если для стриминга в реальном времени
    понадобятся входящие ради скорости — вопрос всплывёт снова, и
    пользователю, вероятно, понадобятся права администратора для
    разблокировки (не проверено: требует ли кнопка «Разрешить» UAC у
    обычного пользователя — проверить на тестовом пользователе без
    прав админа, как в чеклисте чистой машины). Факт на домашней
    машине: до 22:12 15.09 правила для Python313\python.exe были
    Inbound Block (Public) — все замеры 0.1 шли при Block; позже запрос
    закрыли кнопкой «Разрешить», правила стали Inbound Allow (Public) —
    замеры 0.2 и дальше на этой машине идут уже при разрешённых
    входящих, это учитывать при сверке цифр
- **Этап 3 (3-5 сессий)**: карточка раздачи — дорожки/субтитры через
  ffprobe, TMDB/TVmaze (по итогам проверки доступности)
- **Этап 4 (4-6 сессий, можно отложить)**: встроенный плеер вместо
  внешнего
- **Выпуск (2 сессии)**: чеклист чистой машины для торрент-части

### Статус

- 15.09.2026: план утверждён; **сессия 0.1 выполнена на домашней машине**
  (итоги — .vd-proto\torrent\RESULTS.md и «История» 15.09); далее 0.2
- Находки 0.1 для Этапа 1 (подробно в RESULTS.md):
  1. alert.message() на русской Windows может бросить UnicodeDecodeError
     (libtorrent обрезает текст ошибки посреди буквы) — всегда через
     защитную обёртку
  2. занятый файл -> file_error_alert, раздача молча в upload_mode (errc
     пустой, «downloading», 0 пиров); clear_error()+resume() не помогают,
     сам libtorrent повторит через optimistic_disk_retry=600 с;
     unset_flags(upload_mode) -> загрузка через 2 с. Движку: ловить
     file_error_alert -> понятная ошибка + «Повторить»
  3. закрытие сессии 1.8-5.0 с (с трекерами дольше) — closeEvent с
     ограничением по времени
  4. частичный выбор файлов -> .<infohash>.parts в корне save_path —
     удалять вместе с раздачей
  5. dht_state.nodes в обёртке не читается — DHT-состояние только буфером
     write_session_params_buf
  6. установщик не удаляет файлы прежней сборки: после перехода 3.14->3.13
     в установленной копии 145 файлов / 22 МБ (python314.dll, *.cp314*.pyd,
     *.cpython-314.pyc) — не грузятся, но мусор у всех при автообновлении;
     в 1.4 добавить [InstallDelete] {app}\_internal в installer.iss
- Офисная машина (не сделано): per-user Python 3.13 — `winget install
  --id Python.Python.3.13 -e --scope user --override "/quiet
  InstallAllUsers=0 PrependPath=0 Include_launcher=0 AssociateFiles=0
  Shortcuts=0 Include_test=0 TargetDir=<LOCALAPPDATA>\Programs\Python\
  Python313"`; pull.bat (изменение build.bat); build.bat -> BUILD_EXIT=0;
  офлайн-набор .vd-tests; установка с backup + check_installed_*.
  Прототип: код приходит через git (pull.bat), порядок запуска —
  .vd-proto\torrent\README_PROTO.md (make_media.py пересоздаёт тестовые
  видео); цифры сверять с RESULTS.md домашней машины / «Историей»; там только
  локальный сид (probe_disk full/resume/select/locked), probe_metadata НЕ
  запускать (настоящий рой — только дома)
- Тестовые данные 0.2 оставлены в %TEMP%\vd-torrent-proto: seed\ (MKV
  1.2 ГБ: h264 + 2 AAC rus/eng + 2 SRT; MP4 292 МБ с moov в конце; SRT),
  torrents\local-test.torrent (v1, 512 КБ, 2913 кусков), bin\ffmpeg.exe и
  ffprobe.exe (копия — НЕ запускать ffmpeg из build-ffmpeg-cache во время
  сборки: build_ffmpeg.ps1 пересоздаёт extracted и падает на занятом exe)

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
- `config.py` — APP_VERSION 1.0.5, REPO, DEFAULTS, load/save (utf-8-sig),
  история (макс 200, только существующие файлы); при sys.frozen —
  %LOCALAPPDATA%\VideoDownloader\settings.json, в dev — settings.json
  в корне проекта
- `set_version.ps1` — подстановка APP_VERSION в installer.iss (вызывается
  из build.bat; прежний inline powershell был сломан кавычками cmd)
- `sync.bat` — точка синхронизации с GitHub: чистое дерево →
  «Working tree is clean. Nothing to sync.» + log -1; грязное →
  git add -A, коммит «sync: <yyyy-MM-dd-HHmm>», push origin main,
  log -1, сверка main == origin/main. Только git + cmd builtins
  (проверка грязи через %%~zA временного файла — БЕЗ find: в Git Bash
  PATH GNU find затеняет Windows find.exe, из-за чего find /c /v
  ломался)
- `pull.bat` — обновление с GitHub: незакоммиченные изменения → STOP
  (сначала коммит или sync.bat); чистое дерево → git pull --ff-only
  origin main + log -1. Те же builtins, без find
- `single_instance.py` — защита от второго экземпляра (только stdlib/
  ctypes): именованный мьютекс + именованный pipe для активации окна
  первого экземпляра; force_foreground() — обход anti-focus-stealing
  Windows через AttachThreadInput (activateWindow()/raise_() из
  PySide6 сами по себе не выводят окно поверх активного стороннего —
  см. «Историю» 15.09)
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
  себя). Любое другое место на диске — сначала спросить владельца.
  Разрешено дополнительно (15.09.2026): %LOCALAPPDATA%\Programs\
  Python\Python313 (установка Python 3.13 для сборки)
- НИЧЕГО не публиковать на GitHub (push, release) без явного
  подтверждения владельца
- downloader.py (логика загрузок) не трогать без явной задачи владельца
- Новые зависимости: только stdlib в рантайме; сборочные — в
  requirements-dev.txt. Согласованные исключения: curl_cffi (1.0.4),
  libtorrent (торрент-стриминг, 15.09.2026)
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
- У бесплатной модели GLM лимит 8 запросов в минуту. Не делай
  много мелких шагов подряд: объединяй проверки в один скрипт
  или одну команду, не запускай по отдельному вызову на каждый
  файл. При ошибке лимита — подождать минуту и повторить,
  не повторять сразу

## История

- **15.09.2026 (вечер), торрент-стриминг: план + сессия 0.1 (домашняя
  машина)**: решения разведки 1-7 и план этапов записаны (раздел
  «Торрент-стриминг»). Python 3.13.15 установлен per-user (winget,
  PATH не тронут); build.bat -> `py -3.13 -m venv`. У libtorrent колесо
  только cp313 (2.1.1, один .pyd 13 МБ без DLL; Защитник чист).
  Регресс на 3.13: build.bat все 7 шагов BUILD_EXIT=0 (Setup-1.0.7.exe
  179 892 328 байт, локально, не публиковался); офлайн + живые тесты
  зелёные (core 38, retry 42, manifest 4, library, smoke, p1,
  config_robust 48, config_warning_gui 7, ytdlp_logger 8, fragment 154,
  single_instance 7, e2e, net); установка /VERYSILENT поверх (backup
  settings.json.bak-20260915-220846) INSTALL_EXIT=0, selftest 0;
  check_installed_yt (4K, ejs 0.8.0, deno 2.9.6), check_installed_tiktok
  6 PASS, check_installed_fragment 10 PASS, check_sources analyze 4/4,
  check_sources download 3 (Instagram 12.2 МБ + кадры), check_fragment
  vk 0 30 1 video — 4 PASS (107 с, 30.08 с + звук). ВНИМАНИЕ:
  `check_sources download 4 range=...` для VK в dev-режиме не годится —
  скрипт ставит только ffmpeg_location без ContextVar («ffmpeg is not
  installed») и задаёт download_ranges в обход item.time_range (VK-фикс
  прямого формата не срабатывает -> зависание на HLS-аудио); VK-фрагмент
  проверять check_fragment.py (путь приложения).
  TMDB доступен (API 401 без ключа, картинки 200), TVmaze 200.
  Прототип (.vd-proto\torrent, в .gitignore): метаданные по магнет-
  ссылке 60/60 (медианы 1.6-4.9 с; Ubuntu, Debian, BBB, Sintel; мёртвый
  hash — таймаут 180 с без зависания), скачивание Ubuntu 60 с 16.7 МБ/с
  при заблокированных входящих; диск на локальном сиде 1456 МБ —
  90 МБ/с, Private <= 87 МБ, sparse, чтение готовых кусков другим
  процессом OK, kill 51% -> fastresume 47.4% за 0.5 с, выбор файлов,
  занятый файл (upload_mode, см. находки в «Статусе»). Брандмауэр:
  первая сессия на 0.0.0.0 -> системный запрос «Разрешить…» для
  Python; пока он открыт, Windows создаёт 2 правила Inbound Block
  (Public) для python.exe — удаляются только админом. Инцидент: первая
  сборка упала на шаге 4 — моя генерация тестового видео запускала
  ffmpeg.exe из build-ffmpeg-cache\extracted, build_ffmpeg.ps1 не смог
  его удалить; не баг 3.13, повторная сборка прошла

- **15.09.2026, превью кадра при drag ползунка фрагмента не
  показывалось (баг с 8fc336c)**: YouTube, preview_format и ffmpeg
  рабочие (HLS 144p, PNG за 1.2-1.8с). Причины в GUI: debounce 250мс
  перезапускался на каждом тике движения — при drag ffmpeg не
  запускался вовсе; отпускание скрывало превью и (с ef2a530) убивало
  ffmpeg — кадр был виден, только если держать ручку неподвижно >2с;
  -ss <duration> у ffmpeg — rc 69 без кадра, а ручка конца по
  умолчанию стоит на конце видео. Исправлено в gui.py: throttle
  (сразу при нажатии, 1 ffmpeg за раз, не чаще 250мс, по завершении —
  запрос актуальной позиции; во время drag ffmpeg не убивается,
  kill — только сброс фрагмента/смена видео/закрытие окна); кадр
  остаётся после отпускания до следующего касания (обновляется и
  после ввода в поля/стрелок); клэмп секунды duration-1; кадр над
  активной ручкой — в полосе фиксированной высоты 90px между полями
  времени и слайдером (полоса скрыта без preview_format).
  FramePreviewWorker.loaded теперь (worker, QImage). Тест:
  test_fragment.py сценарии A-D на медленном фейк-ffmpeg + поля +
  сброс — 154 PASS, 0 FAIL; живая симуляция на YouTube — кадр виден
  после отпускания во всех A-D, 0 убитых ffmpeg. Консоль cp1251
  роняет test_fragment.py на «→» при выводе в файл — запускать с
  PYTHONIOENCODING=utf-8

- **15.09.2026, чеклист чистой машины (п.7 «Что осталось») —
  выполнено**: временный локальный пользователь Windows без прав
  администратора (кириллица+пробел в имени профиля — заодно покрывает
  п.8 чеклиста), релизный установщик v1.0.6 с GitHub (sha256 сверен
  с latest.json). Все пункты чеклиста прошли (установка без UAC,
  анализ, 1080p merge, MP3, пауза/.part докачка с того же места,
  кириллица/пробелы, тема/настройки/Библиотека переживают перезапуск,
  SmartScreen — «Неизвестный издатель», кнопка «Выполнить в любом
  случае»), кроме деинсталляции — найдены и исправлены два бага
  installer.iss:
  1) удаление при запущенной программе снимало запись из
     «Приложений» и ярлыки, но не могло удалить занятые exe/DLL
     (оставалось 89 МБ без штатного способа убрать) — добавлена
     проверка CheckForMutexes(Local\VideoDownloader-SingleInstance-
     Mutex) в InitializeUninstall с сообщением «закройте программу»
     (Retry/Cancel); не AppMutex — тот проверяется и install-стороной,
     а автообновление (updater.apply_update) запускает тихий setup,
     пока программа ещё работает;
  2) вопрос «Удалить также данные…?» задавался в InitializeUninstall
     раньше стандартного «Вы действительно хотите удалить?» — легко
     нажать «Да» по инерции (так и произошло в первом прогоне,
     история потерялась, затем восстановилась из памяти незакрытого
     процесса при выходе). Перенесён в CurUninstallStepChanged
     (usUninstall), после подтверждения.
  Оба исправления проверены дважды: интерактивно (скриншоты по
  каждому окну) и силент-режимом от имени тестового пользователя —
  удаление при запущенной программе отменяется (InitializeUninstall
  returned False), тихое обновление с /CLOSEAPPLICATIONS (как в
  updater.py) не блокируется и корректно перезапускает программу.
  Три мелких наблюдения (завышенный размер качества, ссылка вместо
  названия в строке загрузки, слэши в пути) — в «Что осталось» п.13,
  не блокируют релиз. Заодно поправлены устаревшие места в
  README_BUILD.md (шаг 1 — версия в примере, шаг 9 — текст про
  вопрос, а не Inno-задачу)

- **15.09.2026, закрыт пункт «Без водяного знака» для TikTok/Instagram
  как неактуальный**: по итогам проверки источников (12.09.2026,
  check_sources.py) yt-dlp сам выбирает чистые (не watermarked)
  форматы у TikTok, Instagram и VK без какого-либо специального кода
  в программе — проверено на реальных ссылках, ни один
  watermarked-формат не выбирался. Отдельная галочка не нужна, пункт
  удалён из «Что осталось»

- **15.09.2026, VK-фрагмент прямым форматом (п.12 «Что осталось») —
  код + тесты, без сборки/релиза**: причина зависания найдена по
  списку форматов реального VK-видео (3ч16м): прежний селектор
  bestvideo+bestaudio/best брал dash_sep-6 (видео, https) +
  hls_fmp4-12_3-Audio (m3u8_native) — FFmpegFD висел на HLS-аудио.
  «Прямой» формат определяется по protocol (заполняется yt-dlp до
  выбора формата), не по имени: url144…url1080 — https, цельный mp4
  видео+звук (кодеки экстрактор не указывает); hls* — m3u8_native;
  ВНИМАНИЕ: dash_sep-* у VK тоже https (отдельные дорожки), их
  отсекает сам селектор best (видео+звук в одном формате).
  downloader.py: только при time_range И _is_vk_url(item.url) формат
  = "best[protocol~='^https?$']" (+[height<=N] для видео с выбранным
  качеством) + "/" + прежний селектор; аудио — тот же прямой mp4, MP3
  извлекает FFmpegExtractAudio. Нет прямого формата -> yt-dlp берёт
  прежний селектор после «/» (решение владельца: вариант А — дальше
  сторож, без раздельного extract/download). Полное скачивание VK и
  другие сайты не тронуты (решение владельца). Нет прямого 1080p ->
  720p и т.п. — приемлемо (владелец). _is_vk_url: разбор хоста через
  urlsplit (VK_HOSTS = vk.com, vk.ru, vkvideo.ru + поддомены, как
  _VALID_URL экстрактора yt-dlp); раньше подстрока: vk.ru не
  распознавался, notvk.com распознался бы. gui.source_from_url
  (подпись источника) переведён на _is_vk_url — был отдельный список
  доменов ("vk.com"/"vkvideo"), vk.ru подписывался «Другое»; фикс по
  замечанию владельца при ревью диффа, единственный источник правды
  о доменах VK теперь downloader.VK_HOSTS.
  Тесты: test_fragment §4.5 +12 (123->135): _is_vk_url (14 URL),
  строка формата VK/не-VK/без фрагмента/аудио, реальный выбор
  YoutubeDL.process_ie_result офлайн на VK-подобных форматах (url1080,
  url720, аудио->url1080, полное -> не url*, только HLS и HLS+DASH ->
  тот же выбор, что прежний селектор). Весь офлайн-набор зелёный
  (core 38, retry 42, manifest 4, library, smoke, p1, config_robust 48,
  config_warning_gui 7, ytdlp_logger 8, fragment 135, single_instance 7).
  Живые проверки (офисная машина, sources.local.txt создан с VK-ссылкой
  владельца): VK 0–30 видео — 103.7с (параллельно ещё 3 загрузки),
  12.2 МБ, 30.08с, h264 1920x1080 + aac; VK 60–90 аудио — 187.5с,
  MP3 192k 30.00с; YouTube BBB 30–60 precise — 514.2с, 30.00с + звук;
  полное VK — формат прежний, без download_ranges, рост 0->16.3 МБ за
  90с, отмена -> «Отменено». Публичный vk.com/video-77521_162222515
  полным скачиванием падает CERTIFICATE_VERIFY_FAILED — так же и у
  голого yt-dlp (CDN/сертификат на этой машине, не связано с правкой)

- **15.09.2026, GUI-предупреждение при повреждённом settings.json
  (п.9 «Что осталось») — код + тесты, без сборки/релиза**: до этой
  задачи load() только писал в app.log — пользователь не видел, что
  сохранение в этом запуске частично или полностью отключено.
  config.py: `_backup_corrupt()` теперь возвращает путь к .corrupt-копии
  (или None), `load()` кладёт итог в модульную `_load_warning`
  ({"skip_saving": bool, "corrupt_path": str|None}), новая функция
  `consume_load_warning()` отдаёт и сразу сбрасывает — гарантия «один раз
  за сессию» на стороне config, а не GUI. Важная находка при разборе
  кода: `_skip_saving=True` ставится только когда .corrupt-копию
  сохранить НЕ удалось (диск/права) — в обычном случае повреждённого,
  но читаемого файла копия создаётся успешно и `_skip_saving` остаётся
  False (save() продолжает работать, просто поверх пишутся дефолты
  текущей сессии). Поэтому GUI-предупреждение показывается при любом
  нечитаемом/повреждённом settings.json, а не только при _skip_saving —
  иначе сценарий «битый JSON» из задачи не подсветился бы вовсе. gui.py:
  `MainWindow.show_config_warning(warning)` вызывается один раз из
  `run()` после `window.show()`; при `_skip_saving=True` — `InfoBar.error`
  без кнопок («настройки не сохранятся, перезапустите»); при успешной
  .corrupt-копии — `InfoBar.warning` с текстом без жаргона + две кнопки
  (по образцу «Открыть папку» из Библиотеки): «Открыть папку»
  (`QDesktopServices.openUrl` на директорию копии) и «Скопировать путь»
  (`QGuiApplication.clipboard().setText`) — путь к .corrupt-файлу длинный,
  копипаст текстом в один InfoBar неудобен. Тесты: test_config_robust
  +8 (40->48: consume_load_warning для сценариев «битый JSON»/«copy не
  вышла»/повторный consume->None/обычный load->None), новый
  test_config_warning_gui.py (7 PASS, offscreen): обычный старт без
  InfoBar, битый файл -> ровно один InfoBar с обеими кнопками и без
  техжаргона в тексте, skip_saving -> отдельный InfoBar, повторный
  consume в той же сессии не показывает второй раз. Весь офлайн-набор
  зелёный (core 38, retry 42, manifest 4, library, gui smoke,
  p1 concurrency, config_robust 48, ytdlp_logger 8, fragment 123,
  single_instance 7, config_warning_gui 7).

- **15.09.2026, single instance protection (п.10 «Что осталось») —
  код + тесты, без сборки/релиза**: новый `single_instance.py`
  (только stdlib/ctypes, без pywin32) — именованный мьютекс
  (`Local\VideoDownloader-SingleInstance-Mutex`, освобождается ОС
  автоматически при завершении процесса, даже аварийном — надёжнее
  lock-файла с PID) определяет, что экземпляр уже запущен; активация
  окна первого экземпляра — именованный pipe (`ConnectNamedPipe`/
  `WaitNamedPipeW` с таймаутом 1.5с), второй процесс шлёт "SHOW" и
  выходит. gui.run(): already_running -> send_show_request(); если
  ответил -> sys.exit(0); не ответил (первый завис) -> показывает
  своё окно, не виснет сам. main.py -selftest не участвует в этой
  логике (свой путь запуска, gui.run() не вызывается) — exit=0
  подтверждён после изменений. НАХОДКА (пригодится для будущей
  активации окна где-либо ещё): PySide6 activateWindow()/raise_()
  НЕ выводят окно поверх активного стороннего окна — Windows
  блокирует SetForegroundWindow из фонового потока чужого процесса
  (anti-focus-stealing); подтверждено живым тестом (скриншот:
  окно разворачивалось из свёрнутого, но оставалось позади VSCode).
  Обход — force_foreground() в single_instance.py: AttachThreadInput
  временно присоединяет очередь ввода к потоку текущего foreground-
  окна, что разрешает SetForegroundWindow; после этого окно реально
  встаёт поверх (подтверждено скриншотом и GetForegroundWindow()==
  hwnd). Живые проверки (два реальных процесса main.py, не только
  модульно): первый экземпляр свёрнут + фокус на другом окне ->
  второй запуск (623-747 мс, exit=0) -> первый разворачивается и
  становится foreground (дважды, с разным сторонним активным окном).
  Только один процесс с заголовком «Video Downloader» на экране —
  второе окно не открывается. Тесты: .vd-tests\test_single_instance.py
  (7 PASS: мьютекс занят/свободен, send_show_request без сервера не
  виснет, живой pipe-обмен), весь офлайн-набор зелёный (core 38,
  retry 42, manifest 4, library, smoke, p1, config_robust 40,
  ytdlp_logger 8, fragment 123). Ложная тревога в процессе: показалось,
  что первый запуск «завис» (0% CPU, пустой MainWindowTitle) — через
  faulthandler.dump_traceback_later подтверждено, что это штатное
  ожидание в цикле событий Qt (app.exec()), пайп-поток корректно ждёт
  соединение на ConnectNamedPipe; не баг. Что не проверено (не
  блокеры, на будущее): реальный exe из установленного дистрибутива
  (build.bat не запускался, проверено только на main.py через
  build-venv); поведение при ДЕЙСТВИТЕЛЬНО зависшем первом экземпляре
  (AC2) — только модульно (таймаут 300мс без сервера); влияние
  force_foreground на модальные диалоги поверх MainWindow при
  активации — не проверялось

- **14.09.2026, v1.0.5 — сборка, установка, живые проверки, релиз
  (draft-first), sync.bat/pull.bat**: сборка 07:10–07:19 (все 7 шагов
  OK, exe 180 880 028 байт, sha256 b62d0513…45eee == latest.json),
  установка /VERYSILENT INSTALL_EXIT=0, selftest установленной копии
  exit=0, состав цел, backup settings.json.bak-20260914-070200.
  Живые проверки (check_installed_fragment.py, 10 PASS): фрагмент BBB
  0:30–1:00 precise → DOWNLOADING за 0.4с → ffmpeg реально работает
  (CIM-дочерний нашего PID, tasklist count=1>0) → отмена → ERROR
  «Отменено» за 1.2с, ffmpeg исчез из tasklist за 1.7с — kill сироты
  работает (противоречие записи 13.09 «в 1.0.5 НЕ реализовано» снято:
  код был в дереве 13.09, п.12 «Что осталось» верен, форма записи
  введена в заблуждение черновиком «Истории»); полное видео 24.2 МБ
  webm + audio-поток; MP3-фрагмент 30.0с + audio. Релиз: коммит
  7374b73 → push → draft → ассеты (exe + latest.json) → тройная
  сверка (digest==sha256==latest.json, размер байт-в-байт) →
  --draft=false; тег v1.0.5 → 7374b73 = main; latest-алиас отдаёт
  1.0.5. sync.bat + pull.bat (ASCII; git + cmd builtins, %%~zA без
  find — GNU find из Git Bash PATH ломал find /c /v; проверены
  вживую: pull STOP на грязном дереве exit 1, sync коммит «sync:
  2026-09-14-0747» 1a197f9 + push + OK main==origin/main).
  Инциденты дня: bash-терминал дважды зависал на heredoc-подаче
  (первая «зависшая» команда в фоне всё же выполнилась и успела
  запустить сборку — до повторных действий проверять процессы и
  build-*.log, дубль не создавать); у edit-инструмента этой сессии
  при создании файла нельзя передавать пустой old_text; файлы >6К
  пишутся кусками. MSYS TEMP=/tmp: Python в WMI-запусках видит
  настоящий %TEMP%, но в скриптах пути задавать абсолютными;
  WMI cmd.exe /c ломается на кавычках в «Claude Projects» —
  обёртка powershell -File

- **13.09.2026, v1.0.5 «Фрагмент видео» — код + тесты, без сборки/релиза**:
  галочка «Скачать только фрагмент» (выкл. по умолчанию; доступна
  только одиночному видео с длительностью, плейлист — скрыт),
  RangeSlider двумя ручками + поля мм:сс/ч:мм:сс (синхронны в обе
  стороны; конец >= начала+1с; мин. 1с), ~размер фрагмента от
  выбранного качества (_selected_quality — единая функция с
  _start_download, правка владельца), «Точная обрезка (медленнее)»
  с подсказкой; downloader: time_range/precise_cut в
  DownloadItem/add, _build_options — download_ranges +
  force_keyframes_at_cuts + outtmpl «[clip Ns-Ms]» (латиница/
  цифры — правило владельца; yt-dlp сам санитизирует кириллицу/
  эмодзи в title), ContextVar-фикс ffmpeg при frozen (FFmpegFD.
  available() читает ContextVar, не params — известный Fixme
  yt-dlp; без фикса фрагмент https-форматов падал «ffmpeg is not
  installed», а e2e-тест в dev-окружении падал на merge), сторож
  _fragment_watch (daemon-поток; отмена — задача закрыта сразу,
  _abandoned глушит воркер-зомби; 10 мин без роста целевых файлов
  -> ERROR, для VK «VK пока не поддерживает скачивание фрагмента»),
  статус «Скачивание фрагмента…» (FFmpegFD не дергает progress-
  hooks — раньше висело «Анализ…»), пауза при фрагменте скрыта
  (правка владельца п.4-5). Правки владельца после ревью диффа:
  без isVisible() в _start_download (виджет скрыт родителем, а
  выбор молча отбрасывался бы), единая _selected_quality, QPoint
  убран, честный статус + таймаут VK, main.py -selftest запущен
  (exit=0). Живые проверки: YouTube 30–60с precise — ровно 30.00с
  + звук (ffprobe), fast — сдвиг к ключевому кадру (40с при
  4K-кодеке — ожидаемо, описано в подсказке), MP3 precise —
  30.00с; TikTok 30.13с (кириллица+эмодзи в имени ок), Instagram
  30.03с; отмена фрагмента посреди скачивания — ERROR «Отменено»
  за 0.8с; VK — сторож закрыл ошибкой про VK (257с при тестовом
  таймауте 180с; .part реально не рос 17 мин). Тесты: test_fragment
  83 PASS (timecode/suffix/слайдер/клэмпы/_build_options/санити-
  зация/GUI-синхронизация/сторож-отмена/сторож-VK/сторож-рост/
  скрытая галочка), весь офлайн-набор зелёный (core 38, retry 42,
  library, smoke, robust 40, manifest 4, ytdlp_logger 8, p1,
  e2e с ffmpeg-инъекцией для dev). APP_VERSION 1.0.5. Сирота
  ffmpeg после отмены: исследовано 13.09 — решение есть (см. п.12
  «Что осталось»), в 1.0.5 НЕ реализовано (правило «не менять код
  без задачи»): задача закрывается, ffmpeg-процесс может
  дописывать .part до своего конца (у VK-зависшего держал
  ~900 МБ RSS); при следующем скачивании перезапишется

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

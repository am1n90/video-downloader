## Релиз (GitHub Releases)

`build.bat` автоматически подставляет версию из `config.APP_VERSION`
(`config.py`) в `installer.iss` и генерирует `Output\latest.json`
(version, url, sha256, notes) — манифест автообновления.

Публикация релиза (нужен [GitHub CLI](https://cli.github.com/)):

```bash
# 1. Bump версии в config.py: APP_VERSION = "1.1.0"
# 2. Полная сборка
build.bat

# 3. Создать релиз: СНАЧАЛА exe, ПОТОМ latest.json
gh release create v1.1.0 ^
   "Output/VideoDownloader-Setup-1.1.0.exe" ^
   "Output/latest.json" ^
   --repo am1n90/video-downloader ^
   --title "Video Downloader 1.1.0" ^
   --notes "Кратко о новом"
```

Порядок важен: `latest.json` содержит URL вида
`releases/download/v<ver>/VideoDownloader-Setup-<ver>.exe` — asset
должен существовать до того, как старые билды начнут читать манифест.

Приложение проверяет **стабильный алиас**
`releases/latest/download/latest.json` — старые билды всегда видят
новейший релиз, URL конкретной версии в них не зашит.

### Как работает автообновление

- Кнопка «Проверить обновления» в Настройках (группа «Обновления»)
  или тихая автопроверка через ~2.5 с после старта (отключается
  чекбоксом «Проверять автоматически»).
- Есть новая версия → InfoBar с версией, notes и кнопкой «Обновить»
  → подтверждение → скачивание с прогрессом → тихая переустановка
  (Inno `/VERYSILENT /NORESTART /CLOSEAPPLICATIONS`) → автоперезапуск.
- Данные пользователя (`%LOCALAPPDATA%\VideoDownloader`) переживают
  обновление: тот же AppId, `UsePreviousAppDir=yes`, данные не удаляются.

### Примечание о безопасности

`sha256` в манифесте — проверка **целостности** (файл скачан без
повреждений), **не** цифровая подпись. Манифест и установщик ходят
только по HTTPS (GitHub). Установщик по-прежнему не подписан —
см. примечание про SmartScreen выше.

## Сборка установщика Video Downloader (Windows)

## Требования к машине сборки

| Инструмент | Зачем | Где взять |
|---|---|---|
| Python 3.10+ | создание venv для PyInstaller | python.org (галочка "Add to PATH") |
| Inno Setup 6 | упаковка установщика | jrsoftware.org/isdl (winget install JRSoftware.InnoSetup) |
| Доступ в сеть | pip-пакеты + ffmpeg (~80 МБ) | — |

## Сборка

```bat
build.bat
```

Скрипт (в корне проекта):
1. создаёт изолированный `build-venv` и ставит `requirements.txt` + `requirements-dev.txt` (pyinstaller);
2. запускает PyInstaller: `--onedir --noconsole --collect-all qfluentwidgets --collect-all yt_dlp --collect-all yt_dlp_ejs`;
3. скачивает ffmpeg (BtbN win64-gpl, кэшируется в `build-ffmpeg-cache\`) и кладёт `ffmpeg.exe`/`ffprobe.exe` в `dist\VideoDownloader\`;
4. скачивает Deno (JS-рантайм для yt-dlp; фиксированная версия, официальные sha256, кэш в `build-deno-cache\`) и кладёт `deno.exe` в `dist\VideoDownloader\` рядом с exe;
5. **тестово запускает** собранный exe (авто-выход через 8 с) — при провале сборка останавливается;
6. вызывает `ISCC.exe installer.iss`.

Результат: **`Output\VideoDownloader-Setup-<APP_VERSION>.exe`** (версия читается из `config.py`).

### Как работает собранное приложение

- Установка **per-user** в `%LOCALAPPDATA%\Programs\VideoDownloader` — без UAC, папка доступна для записи.
- `ffmpeg.exe`/`ffprobe.exe` лежат рядом с exe; yt-dlp находит их через `ffmpeg_location` (системный PATH не трогается).
- `deno.exe` лежит рядом с exe; yt-dlp находит его сам (при frozen поиск JS-рантайма начинается с папки exe). Пара `yt-dlp`/`yt-dlp-ejs` в `requirements.txt` пинена и обновляется только вместе (версия ejs — из METADATA нового yt-dlp).
- Настройки и история: `%LOCALAPPDATA%\VideoDownloader\settings.json` — выживают при обновлении установки; запись атомарная (tmp+fsync+replace), повреждённый файл сохраняется как `settings.json.corrupt-<дата>`, события целостности — в `app.log` рядом; предупреждения yt-dlp — в `yt-dlp.log` рядом.
- Обновление: запустить новый установщик поверх старого (AppId фиксированный, Inno Setup предложит удалить старую версию; данные останутся).

## Проверка после сборки

1. Запустить `Output\VideoDownloader-Setup-1.0.0.exe` → установка без ошибок, ярлыки в меню Пуск (и на рабочем столе, если выбрано).
2. Первый запуск с чистым `%LOCALAPPDATA%\VideoDownloader` — программа стартует, создаёт settings.json.
3. Вставить YouTube-ссылку → «Анализ» → превью, список качеств с размерами.
4. Скачать видео 1080p до конца — прогресс с МБ/скоростью/ETA, merge проходит (ffmpeg), файл открывается.
5. Скачать аудио MP3 — конвертация через postprocessor.
6. Во время загрузки нажать «Пауза», затем «Продолжить» — докачка `.part`.
7. Сменить тему/папку в Настройках, перезапустить — настройки сохранились; Библиотека показывает завершённые загрузки.
8. Проверить путь установки с пробелами/кириллицей (например, `C:\Users\Иван Иванов\...`) — работает.
9. Деинсталляция: папка приложения удалена, ярлыки сняты; `%LOCALAPPDATA%\VideoDownloader` остаётся (или удаляется, если отметить задачу при удалении).

### Если нет чистой машины

Создайте нового локального пользователя Windows и ставьте/тестируйте от него.

## SmartScreen (для конечных пользователей)

Установщик **не подписан** цифровой подписью, поэтому Windows может показать:
«Система Windows защитила ваш компьютер».

Это нормально: нажмите **«Подробнее» → «Выполнить в любом случае»**.

## Файлы сборки

| Файл | Назначение |
|---|---|
| `build.bat` | полный конвейер сборки |
| `build_ffmpeg.ps1` | скачивание/распаковка ffmpeg |
| `requirements-dev.txt` | pyinstaller (только для сборки) |
| `installer.iss` | скрипт Inno Setup 6 |
| `assets/app.ico` | иконка приложения |

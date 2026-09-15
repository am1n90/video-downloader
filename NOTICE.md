# Сторонние компоненты

Video Downloader распространяется под лицензией GPLv3 (см. [LICENSE](LICENSE)).
Установщик и собранное приложение включают следующие сторонние
компоненты — их собственные лицензии продолжают действовать:

| Компонент | Назначение | Лицензия |
|---|---|---|
| [PySide6](https://pypi.org/project/PySide6/) (Qt for Python) | GUI-фреймворк | LGPLv3 (опционально GPLv3 / коммерческая Qt-лицензия) |
| [PySide6-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets) (qfluentwidgets) | Fluent Design виджеты | GPLv3 (Community Edition) |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | загрузка видео/аудио с площадок | Unlicense (общественное достояние) |
| [yt-dlp-ejs](https://github.com/yt-dlp/ejs) | JS-рантайм для yt-dlp (YouTube) | MIT |
| [curl_cffi](https://github.com/lexiforest/curl_cffi) | HTTP-клиент с TLS-имитацией браузера | MIT |
| [Deno](https://deno.com/) | JS-рантайм, используется yt-dlp-ejs | MIT |
| [FFmpeg](https://ffmpeg.org/) (сборка [BtbN win64-gpl](https://github.com/BtbN/FFmpeg-Builds)) | слияние видео/аудио, конвертация в MP3 | GPLv3 (сборка включает GPL-only кодеки) |
| [Pillow](https://python-pillow.org/) | обработка изображений (миниатюры) | MIT-CMU (HPND) |

FFmpeg и Deno запускаются как отдельные исполняемые файлы (не
компонуются с кодом приложения) и распространяются рядом с
установщиком в исходном бинарном виде — исходный код и лицензии
доступны по ссылкам выше.

PySide6 и PySide6-Fluent-Widgets связаны с кодом приложения
напрямую (импортируются как Python-библиотеки и включаются в сборку
PyInstaller), поэтому весь проект распространяется под GPLv3, что
совместимо с условиями обеих библиотек.

Полный текст лицензий сторонних компонентов — по указанным выше
ссылкам на их репозитории/сайты.

# -*- coding: utf-8 -*-
"""Тест Библиотеки 1.0.3 (offscreen).

1.0.3: показывается только история (сканирование папки загрузок убрано),
дубли путей схлопываются (config.load), записи с несуществующими файлами
скрыты; выбор записей + кнопка «Удалить» (без галочки — только записи,
с галочкой — файлы удаляются навсегда, os.remove); неудача удаления
(занят/нет прав) оставляет запись и попадает в InfoBar; файла нет на
диске — запись удаляется без ошибки; «Очистить данные библиотеки» чистит
список, не трогая файлы.

Тестовые данные — только в %TEMP%; settings.json и файлы владельца не
затрагиваются (config.save подменён, CONFIG_PATH -> временный файл).
"""
import json
import os
import shutil
import stat
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config

config.save = lambda settings: None   # settings.json владельца не трогаем

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEvent, QTimer
import gui

app = QApplication(sys.argv)

TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "vd-lib-103")
FILES = os.path.join(TMP, "files")        # тестовые файлы истории
STRANGER = os.path.join(TMP, "stranger")  # «папка загрузок» с чужим файлом
shutil.rmtree(TMP, ignore_errors=True)
for d in (FILES, STRANGER):
    os.makedirs(d, exist_ok=True)


def touch(path, data=b"x" * 1024):
    open(path, "wb").write(data)


V1 = os.path.join(FILES, "v1.mp4")
V2 = os.path.join(FILES, "v2.mp4")
V3 = os.path.join(FILES, "v3.mp4")
V4 = os.path.join(FILES, "v4.mp4")        # станет read-only (неудаляемый)
V5 = os.path.join(FILES, "v5.mp4")        # исчезнет с диска до удаления
GHOST = os.path.join(FILES, "ghost.mp4")  # файла нет с самого начала
for p in (V1, V2, V3, V4, V5):
    touch(p)
touch(os.path.join(STRANGER, "stranger.mp4"))

# История как у владельца до 1.0.3: дубли + запись без файла на диске.
# Новая запись в истории — ближе к началу (add_history -> insert(0)),
# поэтому у дубля V1 «новее» стоит раньше. Дедуп делает config.load() —
# так проверяется весь реальный конвейер (файл -> load -> GUI).
tmp_settings = os.path.join(TMP, "settings.json")
raw_history = [
    {"path": V1, "title": "Видео 1", "source": "YouTube"},          # новее
    {"path": V2, "title": "Видео 2", "source": "VK"},
    {"path": V1, "title": "Видео 1 (старое)", "source": "YouTube"},  # старее
    {"path": V3, "title": "Видео 3", "source": "TikTok"},
    {"path": GHOST, "title": "Призрак", "source": "YouTube"},       # без файла
]
with open(tmp_settings, "w", encoding="utf-8") as f:
    json.dump({"default_folder": STRANGER, "check_updates": False,
               "history": raw_history}, f, ensure_ascii=False)

_old_path = config.CONFIG_PATH
config.CONFIG_PATH = tmp_settings
settings = config.load()
config.CONFIG_PATH = _old_path

window = gui.MainWindow(settings)
window.show()
lib = window.library_page

QTimer.singleShot(300, app.quit)
app.exec()

def settle():
    app.processEvents()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def row_titles():
    settle()
    return sorted(r.entry.get("title") for r in lib.findChildren(gui.LibraryRow))


def row_by_title(title):
    settle()
    for r in lib.findChildren(gui.LibraryRow):
        if r.entry.get("title") == title:
            return r
    return None


errors = []
def check(name, ok, details=""):
    print(("PASS" if ok else "FAIL"), "|", name, ("| " + details) if details else "")
    if not ok:
        errors.append(name)


# --- 1. только история: дедуп, без чужих файлов, без «Папки загрузок» ---
lib.refresh()
check("дедуп: дубли схлопнулись (V1 одна строка, самая новая)",
      row_titles() == ["Видео 1", "Видео 2", "Видео 3"], str(row_titles()))
check("чужой файл из папки загрузок не показан", "stranger" not in row_titles())
check("запись без файла на диске скрыта", "Призрак" not in row_titles())
items = [lib.source_filter.itemText(i) for i in range(lib.source_filter.count())]
check("фильтр: нет источника «Папка загрузок»", "Папка загрузок" not in items,
      str(items))
check("фильтр: источники только из истории",
      set(items) == {"Все", "YouTube", "VK", "TikTok"}, str(items))

# --- 2. 7 refresh подряд без крашей/дублей ---
try:
    for i in range(7):
        lib.refresh()
        if row_titles() != ["Видео 1", "Видео 2", "Видео 3"]:
            errors.append(f"refresh #{i+1}: дубликаты/потеря строк")
    print("PASS | 7x refresh без крашей и дублей")
except RuntimeError as e:
    errors.append(f"краш при refresh: {e}")

# --- 3. поиск и фильтр по источнику ---
try:
    lib.search.setText("Видео 1")
    settle()
    ok_search = row_titles() == ["Видео 1"]
    lib.search.clear()
    settle()
    idx = lib.source_filter.findText("VK")
    lib.source_filter.setCurrentIndex(idx)
    settle()
    ok_filter = row_titles() == ["Видео 2"]
    lib.source_filter.setCurrentIndex(0)
    settle()
    check("поиск по названию", ok_search)
    check("фильтр по источнику VK", ok_filter)
except RuntimeError as e:
    errors.append(f"краш при поиске/фильтре: {e}")

# --- 4. выбор записей и кнопка «Удалить» ---
check("«Удалить» неактивна без выбора", not lib.delete_btn.isEnabled())
check("«Очистить данные библиотеки» активна при непустой истории",
      lib.clear_btn.isEnabled())
r1 = row_by_title("Видео 1")
r1.select_check.setChecked(True)
settle()
check("«Удалить» активна при выборе", lib.delete_btn.isEnabled())
r1.select_check.setChecked(False)
settle()
check("«Удалить» снова неактивна", not lib.delete_btn.isEnabled())

# --- 5. удаление без галочки: только записи, файлы целы ---
notifies = []
lib._notify = lambda kind, text: notifies.append((kind, text))
r1 = row_by_title("Видео 1")
r2 = row_by_title("Видео 2")
r1.select_check.setChecked(True)
r2.select_check.setChecked(True)
lib._confirm_delete = lambda count: (True, False)
lib._delete_selected()
settle()
check("удаление без галочки: записи убраны",
      row_titles() == ["Видео 3"], str(row_titles()))
check("удаление без галочки: файлы на диске целы",
      os.path.isfile(V1) and os.path.isfile(V2))
check("удаление без галочки: итог в InfoBar",
      notifies and notifies[-1] == ("success", "Удалено: 2 записи"),
      str(notifies[-1] if notifies else None))

# --- 6. удаление с галочкой: файлы навсегда; занятый файл остаётся ---
config.add_history(settings, {"path": V4, "title": "Ролик 4", "source": "VK"})
config.add_history(settings, {"path": V5, "title": "Ролик 5", "source": "VK"})
lib.refresh()
check("после refresh: 3 строки (Видео 3, Ролик 4, Ролик 5)",
      row_titles() == ["Видео 3", "Ролик 4", "Ролик 5"], str(row_titles()))

os.chmod(V4, stat.S_IREAD)   # read-only: os.remove -> PermissionError
os.remove(V5)                # файл «сам» исчез с диска до удаления записи
for title in ("Видео 3", "Ролик 4", "Ролик 5"):
    row_by_title(title).select_check.setChecked(True)
lib._confirm_delete = lambda count: (True, True)
lib._delete_selected()
settle()
check("с галочкой: существующий файл удалён навсегда", not os.path.isfile(V3))
check("с галочкой: запись удалённого файла убрана",
      "Видео 3" not in row_titles(), str(row_titles()))
check("занятый файл: запись осталась", row_titles() == ["Ролик 4"],
      str(row_titles()))
check("занятый файл: файл на диске цел", os.path.isfile(V4))
check("файла нет на диске: запись удалена без ошибки",
      "Ролик 5" not in row_titles())
check("InfoBar warning называет неудаляемый файл",
      notifies and notifies[-1] == ("warning", "Не удалены файлы: v4.mp4"),
      str(notifies[-1] if notifies else None))

# --- 7. «Очистить данные библиотеки»: список пуст, файлы целы ---
os.chmod(V4, stat.S_IWRITE)  # вернуть записываемость (для чистки TEMP)
lib._confirm_clear = lambda: True
lib._clear_library_data()
settle()
check("очистка: список пуст", row_titles() == [], str(row_titles()))
check("очистка: файлы на диске целы", os.path.isfile(V4))
check("очистка: InfoBar success",
      notifies and notifies[-1] == ("success", "Данные библиотеки очищены"),
      str(notifies[-1] if notifies else None))
check("очистка: кнопка неактивна при пустой истории", not lib.clear_btn.isEnabled())

# --- 8. диалог подтверждения: галочка по умолчанию выключена ---
dlg = gui.ConfirmDeleteDialog(2, window)   # MaskDialogBase требует parent
check("диалог: галочка «с файлами» выключена по умолчанию",
      not dlg.files_check.isChecked() and not dlg.delete_files)
check("диалог: предупреждение скрыто", dlg.warn_label.isHidden())
dlg.files_check.setChecked(True)
settle()
check("диалог: предупреждение «Файлы будут удалены навсегда» появилось",
      not dlg.warn_label.isHidden() and dlg.delete_files
      and dlg.warn_label.text() == "Файлы будут удалены навсегда")
dlg.deleteLater()

# --- 9. ru_records: формы слова ---
check("ru_records(1/2/5/11/21)",
      [gui.ru_records(n) for n in (1, 2, 5, 11, 21)]
      == ["1 запись", "2 записи", "5 записей", "11 записей", "21 запись"],
      str([gui.ru_records(n) for n in (1, 2, 5, 11, 21)]))

# --- итог ---
window.close()
shutil.rmtree(TMP, ignore_errors=True)

if errors:
    print()
    print(f"БИБЛИОТЕКА 1.0.3: ОШИБКИ: {len(errors)}")
    for e in errors:
        print("  -", e)
    sys.exit(1)
print()
print("БИБЛИОТЕКА 1.0.3: OK — все проверки пройдены")
sys.exit(0)


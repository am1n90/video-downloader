# -*- coding: utf-8 -*-
"""Защита от второго экземпляра: мьютекс занят/свободен, таймаут показа."""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import single_instance

PASS, FAIL = [], []


def check(name, ok, details=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS" if ok else "FAIL"), "|", name, "|", details)


# 1. Первое создание мьютекса в процессе -> "не занят"
handle1, already1 = single_instance.acquire_mutex()
check("первый acquire_mutex -> already_running=False", handle1 and not already1,
      f"handle={handle1} already={already1}")

# 2. Повторное открытие того же именованного мьютекса (тот же процесс,
#    другой хендл) -> already_running=True
handle2, already2 = single_instance.acquire_mutex()
check("второй acquire_mutex -> already_running=True", handle2 and already2,
      f"handle={handle2} already={already2}")

single_instance.kernel32.CloseHandle(handle2)
single_instance.kernel32.CloseHandle(handle1)

# 3. После освобождения обоих хендлов -> мьютекс снова свободен
handle3, already3 = single_instance.acquire_mutex()
check("после закрытия хендлов already_running=False", handle3 and not already3,
      f"handle={handle3} already={already3}")
single_instance.kernel32.CloseHandle(handle3)

# 4. send_show_request к несуществующему pipe (сервер не запущен) с
#    коротким таймаутом -> False, и укладывается в разумное время
#    (AC2: не должен зависать, если "первый экземпляр" не отвечает)
start = time.monotonic()
result = single_instance.send_show_request(timeout_ms=300)
elapsed = time.monotonic() - start
check("send_show_request без сервера -> False", result is False, f"result={result}")
check("send_show_request с таймаутом не виснет (<2с)", elapsed < 2.0,
      f"elapsed={elapsed:.2f}s")

# 5. Живой pipe-сервер получает команду SHOW
received = []
server_thread = single_instance.start_pipe_server(lambda: received.append(True))
time.sleep(0.2)  # дать серверу поднять named pipe
ok = single_instance.send_show_request(timeout_ms=1500)
time.sleep(0.3)  # дать серверу обработать соединение
check("send_show_request к живому серверу -> True", ok is True, f"ok={ok}")
check("сервер вызвал on_show callback", len(received) == 1, f"received={received}")

print(f"\nИТОГО: {len(PASS)} PASS, {len(FAIL)} FAIL")
sys.exit(1 if FAIL else 0)

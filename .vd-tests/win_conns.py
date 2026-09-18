"""Сокеты процесса через iphlpapi (быстро, ~1 мс на снимок).

netstat для этого не годится: один вызов стоит сотни миллисекунд, а
наблюдать надо окно в 0.7 с — разрушение сессии libtorrent. Нужно, чтобы
увидеть, чего именно ждёт деструктор: трекера, DHT (UDP), UPnP (роутер)
или пиров.
"""
import ctypes
import socket
from ctypes import wintypes

iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)

AF_INET = 2
TCP_TABLE_OWNER_PID_ALL = 5
UDP_TABLE_OWNER_PID = 1

TCP_STATES = {1: "CLOSED", 2: "LISTEN", 3: "SYN_SENT", 4: "SYN_RCVD",
              5: "ESTAB", 6: "FIN_WAIT1", 7: "FIN_WAIT2", 8: "CLOSE_WAIT",
              9: "CLOSING", 10: "LAST_ACK", 11: "TIME_WAIT", 12: "DELETE_TCB"}


class MIB_TCPROW_OWNER_PID(ctypes.Structure):
    _fields_ = [("dwState", wintypes.DWORD), ("dwLocalAddr", wintypes.DWORD),
                ("dwLocalPort", wintypes.DWORD),
                ("dwRemoteAddr", wintypes.DWORD),
                ("dwRemotePort", wintypes.DWORD),
                ("dwOwningPid", wintypes.DWORD)]


class MIB_UDPROW_OWNER_PID(ctypes.Structure):
    _fields_ = [("dwLocalAddr", wintypes.DWORD),
                ("dwLocalPort", wintypes.DWORD),
                ("dwOwningPid", wintypes.DWORD)]


def _table(kind, row_type, table_class):
    size = wintypes.DWORD(0)
    func = (iphlpapi.GetExtendedTcpTable if kind == "tcp"
            else iphlpapi.GetExtendedUdpTable)
    # Таблица растёт между «спросить размер» и «прочитать» — тогда вызов
    # возвращает ERROR_INSUFFICIENT_BUFFER и снимок молча пустел
    # (поймано 18.09: у медленных прогонов «снимков нет»)
    for _attempt in range(5):
        func(None, ctypes.byref(size), False, AF_INET, table_class, 0)
        size.value = int(size.value * 1.5) + 4096
        buf = ctypes.create_string_buffer(size.value)
        if func(buf, ctypes.byref(size), False, AF_INET, table_class, 0) == 0:
            break
    else:
        return []
    count = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0]
    rows_at = ctypes.addressof(buf) + ctypes.sizeof(wintypes.DWORD)
    return [row_type.from_address(rows_at + i * ctypes.sizeof(row_type))
            for i in range(count)]


def _ip(value):
    return socket.inet_ntoa(value.to_bytes(4, "little"))


def _port(value):
    return socket.ntohs(value & 0xFFFF)


def snapshot(pid):
    """{'tcp 127.0.0.1:7788 ESTAB', 'udp *:6881', …} — что открыто сейчас."""
    out = set()
    for row in _table("tcp", MIB_TCPROW_OWNER_PID, TCP_TABLE_OWNER_PID_ALL):
        if row.dwOwningPid != pid:
            continue
        state = TCP_STATES.get(row.dwState, str(row.dwState))
        if state == "LISTEN":
            out.add(f"tcp LISTEN :{_port(row.dwLocalPort)}")
        else:
            out.add(f"tcp {_ip(row.dwRemoteAddr)}:{_port(row.dwRemotePort)}"
                    f" {state}")
    for row in _table("udp", MIB_UDPROW_OWNER_PID, UDP_TABLE_OWNER_PID):
        if row.dwOwningPid != pid:
            continue
        out.add(f"udp :{_port(row.dwLocalPort)}")
    return out

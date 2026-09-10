# -*- coding: utf-8 -*-
"""BUG-10: fetch_manifest отличает офлайн (ManifestError) от «нет обновлений» (None/ dict)."""
import os, sys, json, threading, http.server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import updater

PASS, FAIL = [], []
def check(name, ok, details=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS" if ok else "FAIL"), "|", name, "|", details)

# 1. Недоступный хост -> ManifestError, а не None
try:
    updater.fetch_manifest("http://127.0.0.1:1/nothing")
    check("офлайн -> ManifestError", False, "вернулся без исключения")
except updater.ManifestError as e:
    check("офлайн -> ManifestError", True, str(e)[:60])
except Exception as e:
    check("офлайн -> ManifestError", False, repr(e))

# 2. Битый JSON -> ManifestError
payload_bad = b"not-json{"
payload_ok = json.dumps({"version": "9.9.9", "url": "http://x/y.exe"}).encode()

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = payload_bad if self.path == "/bad" else payload_ok
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass

server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_port}"

try:
    updater.fetch_manifest(base + "/bad")
    check("битый JSON -> ManifestError", False, "вернулся без исключения")
except updater.ManifestError:
    check("битый JSON -> ManifestError", True)

m = updater.fetch_manifest(base + "/ok")
check("валидный манифест -> dict", isinstance(m, dict) and m["version"] == "9.9.9")

try:
    updater.fetch_manifest("")
    check("пустой URL -> ManifestError", False, "нет исключения")
except updater.ManifestError:
    check("пустой URL -> ManifestError", True)

print()
print(f"ИТОГО: PASS={len(PASS)} FAIL={len(FAIL)}")
sys.exit(1 if FAIL else 0)

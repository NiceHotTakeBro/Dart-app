#!/bin/bash
# =============================================================================
#  Dart-Kameras: "Pi herunterfahren" am Tablet möglich machen (einmalig)
#  Aufruf in der Remote shell:
#      curl -fsSL https://nicehottakebro.github.io/Dart-app/dart-aus.sh | bash
#  Legt ein kleines Hilfsprogramm an (hört nur auf diesem Pi, Port 8770),
#  trägt es in den Kamera-Start ein und startet es gleich. Kann gefahrlos
#  mehrmals ausgeführt werden.
# =============================================================================
set -u
P="$HOME/dart-aus.py"
cat > "$P" <<'PY'
#!/usr/bin/env python3
# Dart-Kameras: kleines Hilfsprogramm, damit das Tablet den Pi sauber herunterfahren kann.
# Hört nur auf diesem Gerät (127.0.0.1:8770) und kennt nur zwei Befehle: /ping und /aus.
import http.server, subprocess, threading, os
PORT = 8770

def aus():
    if os.environ.get('DART_AUS_TEST'):
        open(os.environ['DART_AUS_TEST'], 'w').write('aus')
        return
    for cmd in (['sudo', '-n', 'shutdown', '-h', 'now'], ['systemctl', 'poweroff']):
        try:
            if subprocess.call(cmd) == 0:
                return
        except Exception:
            pass

class H(http.server.BaseHTTPRequestHandler):
    def antwort(self, code, text):
        b = text.encode()
        self.send_response(code)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.send_header('Access-Control-Allow-Local-Network', 'true')
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    def do_OPTIONS(self):
        self.antwort(204, '')
    def do_GET(self):
        self.antwort(200, 'dart-aus ok') if self.path.startswith('/ping') else self.antwort(404, '?')
    def do_POST(self):
        if self.path.startswith('/aus'):
            self.antwort(200, 'faehrt herunter')
            threading.Timer(1.5, aus).start()
        else:
            self.antwort(404, '?')
    def log_message(self, *a):
        pass

http.server.ThreadingHTTPServer(('127.0.0.1', PORT), H).serve_forever()
PY
chmod +x "$P"
echo "✓ Hilfsprogramm gespeichert ($P)"

S="$HOME/dart-start.sh"
if [ -f "$S" ]; then
  cp "$S" "$S.vor-aus" 2>/dev/null
  python3 - "$S" <<'PY'
import sys
p = sys.argv[1]; s = open(p).read(); o = s
if 'dart-aus.py' not in s:   # Hilfsprogramm beim Hochfahren mitstarten
    s = s.replace('for i in 1 2 3; do fenster',
                  '# Hilfsprogramm zum Herunterfahren vom Tablet aus\n[ -f "$HOME/dart-aus.py" ] && python3 "$HOME/dart-aus.py" >> "$D/aus.log" 2>&1 &\n\nfor i in 1 2 3; do fenster', 1)
if '&aus=1' not in s:        # der App sagen, dass es da ist
    s = s.replace('&code=$CODE"', '&code=$CODE&aus=1"', 1)
if 'LocalNetworkAccessChecks' not in s:   # Chromium darf das Hilfsprogramm auf diesem Gerät ansprechen
    s = s.replace('--disable-features=Translate,WebRtcPipeWireCamera',
                  '--disable-features=Translate,WebRtcPipeWireCamera,LocalNetworkAccessChecks,BlockInsecurePrivateNetworkRequests,PrivateNetworkAccessSendPreflights,PrivateNetworkAccessRespectPreflightResults', 1)
if s != o:
    open(p, 'w').write(s)
ok = all(k in s for k in ['dart-aus.py', '&aus=1', 'LocalNetworkAccessChecks'])
print('✓ Kamera-Start angepasst (' + p + ')' if ok else '⚠ Kamera-Start konnte nicht vollständig angepasst werden')
PY
  bash -n "$S" || { echo "FEHLER in $S – alte Fassung wird zurückgeholt"; cp "$S.vor-aus" "$S"; }
else
  echo "⚠ $S nicht gefunden – ist die Kamera-Einrichtung (dart-install.sh) schon gemacht?"
fi

# gleich starten (beim nächsten Hochfahren startet es von selbst)
pkill -f "python3 .*dart-aus.py" 2>/dev/null
nohup python3 "$P" >/dev/null 2>&1 &
sleep 1
if curl -fsS http://127.0.0.1:8770/ping >/dev/null 2>&1; then echo "✓ Hilfsprogramm läuft"; else echo "⚠ Hilfsprogramm antwortet nicht"; fi
if sudo -n true 2>/dev/null; then echo "✓ Herunterfahren ist erlaubt"; else echo "⚠ sudo verlangt ein Passwort – Herunterfahren klappt evtl. nicht"; fi
echo
echo "Fertig. Jetzt einmal neu starten mit:  sudo reboot"

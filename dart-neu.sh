#!/bin/bash
# =============================================================================
#  Dart-Kameras: neues Programm auf dem Pi einrichten oder aktualisieren
#  Aufruf in der Remote shell:
#      curl -fsSL "https://nicehottakebro.github.io/Dart-app/dart-neu.sh?$(date +%s)" | bash
#  - lädt das neue Programm (dart-pi.py) nach ~/dart-neu/
#  - legt den Umschalter an:   ~/dart-modus.sh neu    (neues Programm)
#                              ~/dart-modus.sh alt    (die drei Browser-Fenster wie bisher)
#                              ~/dart-modus.sh status
#  - an der bisherigen Einrichtung wird nichts gelöscht; dart-start.sh bekommt nur eine
#    Weiche (Sicherung: dart-start.sh.vor-neu). Kann gefahrlos mehrmals ausgeführt werden.
# =============================================================================
set -u
N="$HOME/dart-neu"; D="$HOME/.dart"; S="$HOME/dart-start.sh"; mkdir -p "$N" "$D"
BASE="${DART_BASE:-}"
[ -z "$BASE" ] && BASE=$(grep -m1 '^URL=' "$S" 2>/dev/null | sed -E 's/^URL="?([^" ]+)"?.*/\1/')
case "$BASE" in https://*|http://*) ;; *) BASE="https://nicehottakebro.github.io/Dart-app/";; esac
BASE="${BASE%/}/"

# 1) Programm laden (erst prüfen, dann ersetzen)
if curl -fsSL "${BASE}dart-pi.py?$(date +%s)" -o "$N/dart-pi.py.neu" && python3 -m py_compile "$N/dart-pi.py.neu" 2>/dev/null && grep -q "Dart-Kameras" "$N/dart-pi.py.neu"; then
  mv "$N/dart-pi.py.neu" "$N/dart-pi.py"; echo "✓ Programm geladen: Version $(grep -m1 '^VERSION' "$N/dart-pi.py" | sed -E 's/.*"([^"]+)".*/\1/')"
else
  rm -f "$N/dart-pi.py.neu"; echo "FEHLER: dart-pi.py konnte nicht geladen werden (${BASE}dart-pi.py). Liegt die Datei schon bei GitHub? Nach dem Hochladen 1–2 Minuten warten."; exit 1
fi
python3 -c "import cv2, numpy" 2>/dev/null && echo "✓ Bildverarbeitung (OpenCV) vorhanden" || { echo "FEHLER: OpenCV fehlt. Bitte zuerst:  sudo apt install -y python3-opencv python3-numpy"; exit 1; }
# Verbindungs-Bibliothek für die Brücke (liegt dann auf dem Pi; sonst wird sie aus dem Internet geladen)
if [ ! -s "$N/peerjs.min.js" ]; then curl -fsSL "https://cdn.jsdelivr.net/npm/peerjs@1.5.4/dist/peerjs.min.js" -o "$N/peerjs.min.js" 2>/dev/null || rm -f "$N/peerjs.min.js"; fi
[ -s "$N/peerjs.min.js" ] && echo "✓ Verbindungs-Bibliothek liegt auf dem Pi"

# 2) Start des neuen Programms: Programm + ein kleines Browser-Fenster (Brücke zum Tablet)
cat > "$N/start.sh" <<'SH'
#!/bin/bash
# Dart-Kameras, neues Programm: startet dart-pi.py und die Brücke; beides wird nach einem Absturz neu gestartet.
D="$HOME/.dart"; N="$HOME/dart-neu"; mkdir -p "$D"; LOG="$D/neu.log"
log(){ echo "$(date '+%F %T') $*" >> "$LOG"; }
for f in "$LOG" "$D/pi.log" "$D/bruecke.log"; do [ -f "$f" ] && [ "$(wc -c < "$f")" -gt 500000 ] && tail -n 500 "$f" > "$f.tmp" && mv "$f.tmp" "$f"; done
exec 8>"$D/lock-neu"
if ! flock -n 8; then log "läuft schon – zweiter Start abgebrochen"; exit 0; fi
BROWSER=$(command -v chromium || command -v chromium-browser)
log "Start neues Programm"
( while true; do python3 "$N/dart-pi.py" >> "$D/pi.log" 2>&1 8>&-; log "Programm beendet (Code $?) – Neustart in 3 s"; sleep 3; done ) &
for i in $(seq 1 40); do curl -fsS -m 2 http://127.0.0.1:8771/status >/dev/null 2>&1 && break; sleep 1; done
if [ -z "$BROWSER" ]; then log "FEHLER: Chromium nicht gefunden – keine Brücke zum Tablet"; wait; exit 1; fi
dir="$D/bruecke"
while true; do
  if [ -f "$dir/Default/Preferences" ]; then
    sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"[A-Za-z]*"/"exit_type":"Normal"/' "$dir/Default/Preferences"
  fi
  "$BROWSER" --user-data-dir="$dir" --app="http://127.0.0.1:8771/" \
    --autoplay-policy=no-user-gesture-required --disable-background-timer-throttling --disable-renderer-backgrounding \
    --disable-backgrounding-occluded-windows --noerrdialogs --disable-session-crashed-bubble --disable-infobars \
    --no-first-run --no-default-browser-check --password-store=basic --disable-features=Translate \
    --window-position=0,0 --window-size=430,700 >> "$D/bruecke.log" 2>&1 8>&-
  log "Brücken-Fenster wurde beendet (Code $?) – Neustart in 5 s"; sleep 5
done
SH
chmod +x "$N/start.sh"; echo "✓ Start des neuen Programms gespeichert ($N/start.sh)"

# 3) Weiche im bisherigen Start: steht der Modus auf "neu", startet das neue Programm – sonst alles wie bisher
if [ -f "$S" ]; then
  if grep -q "dart-neu/start.sh" "$S"; then echo "✓ Weiche im Kamera-Start war schon da"
  else
    cp "$S" "$S.vor-neu"
    python3 - "$S" <<'PY'
import sys
p = sys.argv[1]; s = open(p).read()
w = ('# Weiche: neues Programm (dart-pi.py) statt der drei Browser-Fenster, wenn der Modus auf "neu" steht (umschalten: ~/dart-modus.sh neu|alt)\n'
     'if [ "$(cat "$HOME/.dart/modus" 2>/dev/null)" = "neu" ] && [ -x "$HOME/dart-neu/start.sh" ]; then exec "$HOME/dart-neu/start.sh"; fi\n\n')
k = 'D="$HOME/.dart"; mkdir -p "$D"'
if k in s:
    open(p, 'w').write(s.replace(k, w + k, 1))
PY
    if grep -q "dart-neu/start.sh" "$S" && bash -n "$S"; then echo "✓ Weiche in den Kamera-Start eingebaut (Sicherung: $S.vor-neu)"
    else cp "$S.vor-neu" "$S"; echo "⚠ Weiche konnte nicht eingebaut werden – der bisherige Start bleibt unverändert"; fi
  fi
else echo "⚠ $S nicht gefunden – ist die Kamera-Einrichtung (dart-install.sh) gemacht?"; fi

# 4) Umschalter
cat > "$HOME/dart-modus.sh" <<'SH'
#!/bin/bash
# Dart-Kameras umschalten:  ~/dart-modus.sh neu | alt | status
M="${1:-status}"; D="$HOME/.dart"; mkdir -p "$D"; U=$(id -u)
if [ "$M" = "status" ]; then
  echo "Modus: $(cat "$D/modus" 2>/dev/null || echo alt)"
  curl -s -m 4 http://127.0.0.1:8771/status || echo "(das neue Programm läuft gerade nicht)"
  exit 0
fi
if [ "$M" != "neu" ] && [ "$M" != "alt" ]; then echo "Aufruf:  ~/dart-modus.sh neu   oder   ~/dart-modus.sh alt   oder   ~/dart-modus.sh status"; exit 1; fi
if [ "$M" = "neu" ] && [ ! -x "$HOME/dart-neu/start.sh" ]; then echo "Das neue Programm ist noch nicht eingerichtet (dart-neu.sh)."; exit 1; fi
# Umgebung des Desktops merken (damit das Fenster auf dem Pi-Bildschirm aufgeht)
E=()
for p in $(pgrep -u "$U" -f 'dart-start\.sh|dart-neu/start\.sh') $(pgrep -u "$U" -x 'wf-panel-pi|pcmanfm|labwc|chromium' 2>/dev/null); do
  [ -r "/proc/$p/environ" ] || continue
  if tr '\0' '\n' < "/proc/$p/environ" | grep -q '^WAYLAND_DISPLAY=\|^DISPLAY='; then
    while IFS= read -r l; do E+=("$l"); done < <(tr '\0' '\n' < "/proc/$p/environ" | grep -E '^(WAYLAND_DISPLAY|DISPLAY|XDG_RUNTIME_DIR|DBUS_SESSION_BUS_ADDRESS|XDG_SESSION_TYPE|XDG_CURRENT_DESKTOP|XAUTHORITY)=')
    break
  fi
done
if [ ${#E[@]} -eq 0 ]; then R="/run/user/$U"; W=$(ls "$R" 2>/dev/null | grep -m1 -E '^wayland-[0-9]+$'); E=("XDG_RUNTIME_DIR=$R" "WAYLAND_DISPLAY=${W:-wayland-0}"); fi
echo "$M" > "$D/modus"
echo "Beende die laufenden Kamera-Programme …"
systemctl --user stop 'dart-kameras-*' 2>/dev/null
pkill -u "$U" -f 'dart-start\.sh' 2>/dev/null; pkill -u "$U" -f 'dart-neu/start\.sh' 2>/dev/null
pkill -u "$U" -f 'dart-neu/dart-pi\.py' 2>/dev/null; pkill -u "$U" -f 'dart-aus\.py' 2>/dev/null
pkill -u "$U" -f -- "--user-data-dir=$D/" 2>/dev/null
sleep 3
pkill -9 -u "$U" -f 'dart-neu/dart-pi\.py' 2>/dev/null; pkill -9 -u "$U" -f -- "--user-data-dir=$D/" 2>/dev/null
sleep 1
# Start über die Benutzerdienste (unabhängig von dieser Remote shell); sonst losgelöst im Hintergrund
A=(); for e in "${E[@]}"; do A+=(-E "$e"); done
if ! systemd-run --user --quiet --collect --unit="dart-kameras-$(date +%s)" "${A[@]}" "$HOME/dart-start.sh" 2>/dev/null; then
  cd "$HOME"; setsid nohup env "${E[@]}" "$HOME/dart-start.sh" >/dev/null 2>&1 < /dev/null &
fi
if [ "$M" = "neu" ]; then
  echo "Neues Programm startet …"
  for i in $(seq 1 25); do sleep 1; [ "$(curl -s -m 2 http://127.0.0.1:8771/status | grep -c '^Kamera [1-3]: ok')" -ge 3 ] && [ "$i" -ge 8 ] && break; done
  echo; curl -s -m 4 http://127.0.0.1:8771/status || echo "⚠ Das neue Programm antwortet nicht. Zurück mit:  ~/dart-modus.sh alt"
  echo; echo "Zurück zu den drei Browser-Fenstern jederzeit mit:  ~/dart-modus.sh alt"
else
  echo "Die drei Kamera-Fenster starten wie bisher (dauert etwa eine halbe Minute)."
fi
SH
chmod +x "$HOME/dart-modus.sh"; echo "✓ Umschalter gespeichert ($HOME/dart-modus.sh)"
# läuft das neue Programm schon (Aktualisierung)? Dann gleich mit der neuen Fassung neu starten
if [ "$(cat "$D/modus" 2>/dev/null)" = "neu" ] && pgrep -u "$(id -u)" -f 'dart-neu/dart-pi\.py' >/dev/null; then
  pkill -u "$(id -u)" -f 'dart-neu/dart-pi\.py'; echo "✓ Neues Programm läuft schon – es startet in 3 Sekunden mit der neuen Fassung neu"
fi
echo
echo "Fertig. Umschalten auf das neue Programm:   ~/dart-modus.sh neu"
echo "Zurück zu den drei Browser-Fenstern:        ~/dart-modus.sh alt"

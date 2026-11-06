#!/bin/bash
# =============================================================================
#  Dart-Kameras auf dem Raspberry Pi starten
#  Öffnet für jede Ring-Kamera ein eigenes Fenster der Dart-App (je ein eigener
#  Rechenkern). Stürzt ein Fenster ab, wird es nach 5 Sekunden neu gestartet.
#  Wird beim Hochfahren automatisch aufgerufen (siehe dart-install.sh).
# =============================================================================

# ---- HIER ANPASSEN (dart-install.sh trägt das automatisch ein) --------------
URL="https://DEIN-NAME.github.io/DEIN-PROJEKT/"   # Adresse deiner Dart-App
CODE="1234"                                       # 4-stelliger Code vom Tablet
ZAHLEN=(11 18 2)                                  # Zahl am Rand bei Kamera 1, 2, 3
# -----------------------------------------------------------------------------

D="$HOME/.dart"; mkdir -p "$D"; LOG="$D/start.log"
log(){ echo "$(date '+%F %T') $*" >> "$LOG"; }
# Protokoll klein halten
[ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 200000 ] && tail -n 300 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"

BROWSER=$(command -v chromium || command -v chromium-browser)
if [ -z "$BROWSER" ]; then log "FEHLER: Chromium nicht gefunden"; exit 1; fi

# Nur einmal gleichzeitig laufen lassen
exec 9>"$D/lock"
if ! flock -n 9; then log "läuft schon – zweiter Start abgebrochen"; exit 0; fi

# Auf Internet warten (höchstens 2 Minuten)
HOST=$(echo "$URL" | sed -E 's#^[a-z]+://([^/]+).*#\1#')
for i in $(seq 1 60); do getent hosts "$HOST" >/dev/null 2>&1 && break; sleep 2; done
# Auf die Kameras warten (höchstens 1 Minute) – je USB-Kamera ein Eintrag (eingebaute Bausteine des Pi 5 zählen nicht)
N=0
for i in $(seq 1 30); do
  N=$(ls /dev/v4l/by-path/ 2>/dev/null | grep -c 'usb.*video-index0$')
  [ "$N" -ge 3 ] && break; sleep 2
done
log "Start: Internet $(getent hosts "$HOST" >/dev/null 2>&1 && echo ok || echo FEHLT) | Kameras gefunden: $N | Code $CODE | Zahlen ${ZAHLEN[*]}"
# Welche Kamera steckt wo (hilft, falls sich die Reihenfolge einmal vertauscht)
for p in /dev/v4l/by-path/*usb*video-index0; do [ -e "$p" ] && log "  Anschluss $(basename "$p" | sed 's/-video-index0//') -> $(readlink -f "$p")"; done

fenster(){
  local i=$1 dir="$D/kamera$i"
  while true; do
    # Hinweis "Chromium wurde nicht ordnungsgemäß beendet" nach Stromausfall verhindern
    if [ -f "$dir/Default/Preferences" ]; then
      sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"[A-Za-z]*"/"exit_type":"Normal"/' "$dir/Default/Preferences"
    fi
    "$BROWSER" \
      --user-data-dir="$dir" \
      --app="${URL}?kamera=$i&zahl=${ZAHLEN[$((i-1))]}&code=$CODE" \
      --use-fake-ui-for-media-stream \
      --autoplay-policy=no-user-gesture-required \
      --disable-background-timer-throttling \
      --disable-renderer-backgrounding \
      --disable-backgrounding-occluded-windows \
      --noerrdialogs --disable-session-crashed-bubble --disable-infobars \
      --no-first-run --password-store=basic --disable-features=Translate \
      --window-position=$(( (i-1)*430 )),0 --window-size=430,700 \
      >> "$D/kamera$i.log" 2>&1
    log "Kamera-Fenster $i wurde beendet (Code $?) – Neustart in 5 s"
    sleep 5
  done
}

for i in 1 2 3; do fenster "$i" & sleep 4; done
wait

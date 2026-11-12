#!/bin/bash
# =============================================================================
#  Dart-Kameras: WLAN stabil halten (einmalig einrichten)
#  Aufruf in der Remote shell:
#      curl -fsSL https://nicehottakebro.github.io/Dart-app/dart-wlan.sh | bash
#  1) WLAN-Stromsparen dauerhaft aus (häufigste Ursache, dass ein Raspberry Pi
#     plötzlich nicht mehr im WLAN erreichbar ist, obwohl er weiterläuft).
#  2) WLAN-Wache: prüft alle 15 Sekunden, ob der Router antwortet. Wenn nicht,
#     wird das WLAN neu gestartet, notfalls der Netzwerkdienst, im äußersten
#     Fall der Pi (höchstens einmal pro Stunde). Alles steht in ~/dart-wlan.log.
#  Kann gefahrlos mehrmals ausgeführt werden. Die Verbindung zur Remote shell
#  bleibt dabei bestehen.
# =============================================================================
set -u
U="$USER"; H="$HOME"
IF=$(iw dev 2>/dev/null | awk '/Interface/{print $2; exit}'); IF=${IF:-wlan0}

# --- 1) WLAN-Stromsparen aus ----------------------------------------------------
sudo mkdir -p /etc/NetworkManager/conf.d
printf '[connection]\nwifi.powersave = 2\n' | sudo tee /etc/NetworkManager/conf.d/99-dart-wlan-stromsparen-aus.conf >/dev/null
sudo iw dev "$IF" set power_save off 2>/dev/null
PS=$(iw dev "$IF" get power_save 2>/dev/null)
case "$PS" in *off*) echo "✓ WLAN-Stromsparen ist aus (auch nach jedem Neustart)";; *) echo "⚠ WLAN-Stromsparen: ${PS:-unbekannt}";; esac

# --- 2) WLAN-Wache ---------------------------------------------------------------
sudo tee /usr/local/bin/dart-wlan-wache.sh >/dev/null <<'SH'
#!/bin/bash
# Dart-Kameras: WLAN-Wache (läuft als Systemdienst)
L="${DART_LOG:-/var/log/dart-wlan.log}"; U="${DART_USER:-root}"; IF="${DART_IF:-wlan0}"; Z="${DART_TAKT:-15}"
M=/var/lib/dart-wlan; mkdir -p "$M"
log(){ echo "$(date '+%F %T') $*" >> "$L"; chown "$U": "$L" 2>/dev/null; }
log "---- WLAN-Wache gestartet ----"
iw dev "$IF" set power_save off 2>/dev/null
F=0; N=0; SCHARF=0
while true; do
  GW=$(ip route 2>/dev/null | awk '/^default/{print $3; exit}')
  if [ -n "$GW" ] && ping -c1 -W3 "$GW" >/dev/null 2>&1; then
    [ $F -ge 2 ] && log "WLAN wieder da (nach $F Fehlversuchen)"
    F=0; SCHARF=1
  elif [ $SCHARF -eq 1 ]; then          # erst eingreifen, wenn der Router seit dem Start schon einmal geantwortet hat
    F=$((F+1)); log "Router ${GW:-?} antwortet nicht ($F)"
    if [ $((F % 12)) -eq 4 ]; then log "-> WLAN wird neu gestartet"; nmcli radio wifi off; sleep 3; nmcli radio wifi on; iw dev "$IF" set power_save off 2>/dev/null; fi
    if [ $((F % 12)) -eq 10 ]; then log "-> Netzwerkdienst wird neu gestartet"; systemctl restart NetworkManager; fi
    if [ $F -ge 24 ]; then
      LR=$(cat "$M/neustart" 2>/dev/null || echo 0); JETZT=$(date +%s)
      if [ $((JETZT - LR)) -gt 3600 ]; then echo "$JETZT" > "$M/neustart"; log "-> Pi wird neu gestartet (WLAN kam nicht zurück)"; sync; systemctl reboot; sleep 60; fi
    fi
  fi
  N=$((N+1))
  if [ $((N % 4)) -eq 1 ]; then
    S=$(iw dev "$IF" link 2>/dev/null | awk '/signal/{printf "%s dBm ", $2} /tx bitrate/{printf "%s MBit/s", $3}')
    P=$(iw dev "$IF" get power_save 2>/dev/null | awk '{print $3}')
    log "Signal: ${S:-nicht verbunden} | Stromsparen: ${P:-?}"
  fi
  if [ "$(wc -l < "$L")" -gt 20000 ]; then tail -n 10000 "$L" > "$L.tmp" && mv "$L.tmp" "$L"; chown "$U": "$L" 2>/dev/null; fi
  sleep "$Z"
done
SH
sudo chmod +x /usr/local/bin/dart-wlan-wache.sh
printf '[Unit]\nDescription=Dart-Kameras: WLAN-Wache\nAfter=NetworkManager.service\n\n[Service]\nEnvironment=DART_LOG=%s/dart-wlan.log DART_USER=%s DART_IF=%s\nExecStart=/usr/local/bin/dart-wlan-wache.sh\nRestart=always\nRestartSec=10\n\n[Install]\nWantedBy=multi-user.target\n' "$H" "$U" "$IF" | sudo tee /etc/systemd/system/dart-wlan-wache.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable dart-wlan-wache.service >/dev/null 2>&1
sudo systemctl restart dart-wlan-wache.service
sleep 3
if systemctl is-active --quiet dart-wlan-wache.service; then echo "✓ WLAN-Wache läuft (startet ab jetzt bei jedem Hochfahren)"; tail -n 2 "$H/dart-wlan.log" 2>/dev/null; else echo "⚠ WLAN-Wache läuft nicht"; fi
echo
echo "Fertig. Das Protokoll der WLAN-Wache zeigt dir:   tail -20 ~/dart-wlan.log"

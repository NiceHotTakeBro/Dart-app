#!/bin/bash
# =============================================================================
#  Dart-Kameras: Absturz-Ursache finden (einmalig einrichten)
#  Aufruf in der Remote shell:
#      curl -fsSL https://nicehottakebro.github.io/Dart-app/dart-diag.sh | bash
#  1) Das System-Protokoll wird dauerhaft auf der Speicherkarte gespeichert
#     (Raspberry Pi OS hält es sonst nur im Arbeitsspeicher – nach einem
#     Absturz ist es weg).
#  2) Ein kleines Mess-Programm schreibt alle 10 Sekunden Spannung,
#     Temperatur, Drosselung und freien Speicher in ~/dart-status.log.
#  Kann gefahrlos mehrmals ausgeführt werden.
# =============================================================================
set -u

# --- 1) Protokoll dauerhaft speichern ----------------------------------------
sudo mkdir -p /etc/systemd/journald.conf.d /var/log/journal
printf '[Journal]\nStorage=persistent\nSystemMaxUse=200M\n' | sudo tee /etc/systemd/journald.conf.d/90-dart-dauerhaft.conf >/dev/null
sudo systemd-tmpfiles --create --prefix /var/log/journal >/dev/null 2>&1
sudo systemctl restart systemd-journald
sleep 1
sudo journalctl --flush >/dev/null 2>&1
if [ -n "$(ls -A /var/log/journal 2>/dev/null)" ]; then echo "✓ System-Protokoll wird jetzt dauerhaft gespeichert"; else echo "⚠ System-Protokoll: Ordner /var/log/journal ist noch leer"; fi

# --- 2) Mess-Programm ---------------------------------------------------------
P="$HOME/dart-status.sh"
cat > "$P" <<'SH'
#!/bin/bash
# Dart-Kameras: alle 10 s Spannung, Temperatur, Drosselung, freien Speicher und Last notieren
L="$HOME/dart-status.log"
echo "$(date '+%F %T') ---- Start (Pi hochgefahren) ----" >> "$L"
while true; do
  V=$(vcgencmd pmic_read_adc EXT5V_V 2>/dev/null | sed -n 's/.*=\([0-9.]*\)V.*/\1/p')
  T=$(vcgencmd measure_temp 2>/dev/null | sed 's/temp=//')
  D=$(vcgencmd get_throttled 2>/dev/null | sed 's/throttled=//')
  M=$(awk '/MemAvailable/{printf "%d", $2/1024}' /proc/meminfo)
  A=$(cut -d' ' -f1 /proc/loadavg)
  echo "$(date '+%F %T') 5V=${V:-?}V Temp=${T:-?} Drossel=${D:-?} frei=${M}MB Last=${A}" >> "$L"
  sync "$L" 2>/dev/null
  if [ $(wc -l < "$L") -gt 30000 ]; then tail -n 20000 "$L" > "$L.tmp" && mv "$L.tmp" "$L"; fi
  sleep 10
done
SH
chmod +x "$P"
( crontab -l 2>/dev/null | grep -v 'dart-status.sh'; echo "@reboot $P" ) | crontab -
pkill -f "dart-status.sh" 2>/dev/null
nohup "$P" >/dev/null 2>&1 &
sleep 11
if [ -s "$HOME/dart-status.log" ]; then echo "✓ Mess-Programm läuft (startet ab jetzt bei jedem Hochfahren)"; tail -n 1 "$HOME/dart-status.log"; else echo "⚠ Mess-Programm schreibt noch nichts"; fi
echo
echo "Fertig. Nach dem nächsten Absturz und Neustart bitte diese zwei Befehle:"
echo "   journalctl -b -1 -p warning --no-pager | tail -40"
echo "   grep -B 30 'Start (Pi hochgefahren)' ~/dart-status.log | tail -32"

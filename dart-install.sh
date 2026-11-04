#!/bin/bash
# =============================================================================
#  Einmalige Einrichtung der Dart-Kameras auf dem Raspberry Pi
#  Aufruf im Terminal (im Ordner, in dem die beiden Dateien liegen):
#      bash dart-install.sh
#  Fragt Adresse, Code und Zahlen ab, richtet den Autostart ein und
#  stellt Bildschirmschoner und WLAN-Stromsparen ab. Kann gefahrlos
#  mehrmals ausgeführt werden (z. B. um den Code zu ändern).
# =============================================================================
set -u
HIER="$(cd "$(dirname "$0")" && pwd)"
ZIEL="$HOME/dart-start.sh"
echo "== Dart-Kameras einrichten =="

if [ ! -f "$HIER/dart-start.sh" ]; then echo "FEHLER: dart-start.sh liegt nicht im selben Ordner wie dieses Skript."; exit 1; fi

# Bisherige Werte als Vorschlag übernehmen
alt(){ grep -m1 "^$1=" "$ZIEL" 2>/dev/null | sed -E "s/^$1=//; s/ *#.*//; s/^\"//; s/\"$//"; }
A_URL=$(alt URL); A_CODE=$(alt CODE); A_Z=$(alt ZAHLEN | tr -d '()')
[ -z "$A_URL" ] || [[ "$A_URL" == *DEIN-NAME* ]] && A_URL=""
[ -z "$A_CODE" ] && A_CODE="1234"; [ -z "$A_Z" ] && A_Z="11 18 2"

read -r -p "Adresse der Dart-App (wie auf dem Tablet, z. B. https://name.github.io/dart/) [${A_URL}]: " URL; URL=${URL:-$A_URL}
[[ -n "$URL" && ! "$URL" =~ ^https?:// ]] && URL="https://$URL"
while [[ ! "$URL" =~ ^https://[^/]+\.[^/]+ ]]; do read -r -p "Bitte die Adresse eingeben, z. B. https://name.github.io/dart/: " URL; [[ -n "$URL" && ! "$URL" =~ ^https?:// ]] && URL="https://$URL"; done
URL="${URL%%[?#]*}"   # nur die reine Adresse, ohne ?… oder #…
[[ "$URL" == */ ]] || [[ "$URL" == *.html ]] || URL="$URL/"
read -r -p "4-stelliger Code vom Tablet (Einrichtung → Weitere Kameras koppeln) [${A_CODE}]: " CODE; CODE=${CODE:-$A_CODE}
while [[ ! "$CODE" =~ ^[0-9]{4}$ ]]; do read -r -p "Der Code muss aus 4 Ziffern bestehen: " CODE; done
read -r -p "Bei welcher Zahl am Rand sitzen Kamera 1, 2 und 3? [${A_Z}]: " Z; Z=${Z:-$A_Z}
set -- $Z
while [ $# -ne 3 ] || ! [[ "$1$2$3" =~ ^[0-9]+$ ]] || [ "$1" -lt 1 ] || [ "$1" -gt 20 ] || [ "$2" -lt 1 ] || [ "$2" -gt 20 ] || [ "$3" -lt 1 ] || [ "$3" -gt 20 ]; do
  read -r -p "Bitte drei Zahlen zwischen 1 und 20 mit Leerzeichen, z. B. 11 18 2: " Z; set -- $Z; done

# Startskript mit den Werten in den Home-Ordner legen (Sonderzeichen für sed schützen)
URLE=$(printf '%s' "$URL" | sed 's/[&#\\]/\\&/g')
sed -E "s#^URL=.*#URL=\"$URLE\"   \# Adresse deiner Dart-App#; s#^CODE=.*#CODE=\"$CODE\"   \# 4-stelliger Code vom Tablet#; s#^ZAHLEN=.*#ZAHLEN=($1 $2 $3)   \# Zahl am Rand bei Kamera 1, 2, 3#" "$HIER/dart-start.sh" > "$ZIEL.neu"
bash -n "$ZIEL.neu" || { echo "FEHLER beim Schreiben von dart-start.sh"; exit 1; }
mv "$ZIEL.neu" "$ZIEL"; chmod +x "$ZIEL"
echo "✓ dart-start.sh gespeichert ($ZIEL)"

# Autostart beim Hochfahren (Desktop labwc). Die Systemvorlage wird übernommen, damit Taskleiste und Desktop bleiben.
mkdir -p "$HOME/.config/labwc"; AS="$HOME/.config/labwc/autostart"
if [ ! -f "$AS" ] && [ -f /etc/xdg/labwc/autostart ]; then cp /etc/xdg/labwc/autostart "$AS"; fi
touch "$AS"
grep -q "dart-start.sh" "$AS" || echo "$ZIEL &" >> "$AS"
echo "✓ Autostart eingerichtet ($AS)"

# Automatisch anmelden, Bildschirmschoner aus
if command -v raspi-config >/dev/null 2>&1; then
  sudo raspi-config nonint do_boot_behaviour B4 && echo "✓ Automatische Anmeldung am Desktop an"
  sudo raspi-config nonint do_blanking 1 && echo "✓ Bildschirmschoner aus"
fi
# WLAN-Stromsparen aus (stabilere Verbindung zum Tablet)
if command -v nmcli >/dev/null 2>&1; then
  nmcli -t -f NAME,TYPE connection show | awk -F: '$2=="802-11-wireless"{print $1}' | while read -r C; do
    sudo nmcli connection modify "$C" 802-11-wireless.powersave 2 && echo "✓ WLAN-Stromsparen aus ($C)"; done
fi

# Kameras prüfen
N=$(ls /dev/v4l/by-path/ 2>/dev/null | grep -c 'video-index0$')
if [ "$N" -ge 3 ]; then echo "✓ $N Kameras erkannt"; else echo "⚠ Nur $N Kamera(s) erkannt – bitte alle 3 Kameras einstecken."; fi
echo
echo "Fertig. Jetzt neu starten mit:  sudo reboot"
echo "Nach dem Neustart öffnen sich die 3 Kamera-Fenster von selbst."

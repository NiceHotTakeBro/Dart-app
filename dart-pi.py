#!/usr/bin/env python3
# =============================================================================
#  Dart-Kameras: eigenes Programm auf dem Raspberry Pi (ersetzt die drei Browser-Fenster)
#  Stufe 2: liest die drei Ring-Kameras direkt (OpenCV). Für jede Kamera läuft ein Rechenkern
#           (dart-kern.js unter Node) mit genau der Erkennung aus der Dart-App – ohne Browser,
#           ohne Video-Anzeige. Dieses Programm gibt den Kernen die Bilder und reicht ihre
#           Meldungen ans Tablet weiter.
#
#  Aufbau: Dieses Programm hört nur auf diesem Gerät (127.0.0.1:8771). Ein kleines
#  Browser-Fenster (die "Brücke", Seite /) hält die Verbindung zum Tablet und reicht
#  Nachrichten in beide Richtungen durch. Gerechnet wird nur hier und in den Kernen.
# =============================================================================
import base64, collections, glob, http.server, json, os, re, shutil, struct, subprocess, sys, threading, time
import cv2, numpy as np

VERSION = "0.5"
PORT = int(os.environ.get("DART_PORT", "8771"))
HOME = os.path.expanduser("~")
DIR = os.path.dirname(os.path.abspath(__file__))
DATEN = os.environ.get("DART_DATEN", os.path.join(HOME, ".dart"))
FAKE = os.environ.get("DART_FAKE", "")          # Test ohne Kameras: Ordner mit K1*.jpg/png, K2*, K3*
APPDIR = os.environ.get("DART_APPDIR", "")      # Test: Tablet-App unter /app/ ausliefern
TEST_INVERZ = float(os.environ.get("DART_TEST_INVERZ", "0") or 0)   # Test: Annahme vom Tablet künstlich verzögern (s)

LOG = []
LOCK = threading.Lock()

def hm():
    return time.strftime("%H:%M:%S")

def log(text):
    with LOCK:
        LOG.append(hm() + " " + text)
        if len(LOG) > 600:
            del LOG[:200]
    try:
        print(hm(), text, flush=True)
    except Exception:
        pass

def lies_start():
    """Code und Zahlen aus ~/dart-start.sh (dort stehen sie seit der ersten Einrichtung)."""
    code, zahlen = os.environ.get("DART_CODE", ""), [0, 0, 0]
    if os.environ.get("DART_ZAHLEN"):
        return code, [int(v) for v in os.environ["DART_ZAHLEN"].split()][:3]
    try:
        s = open(os.path.join(HOME, "dart-start.sh"), encoding="utf-8", errors="replace").read()
        m = re.search(r'^CODE="?(\d{4})"?', s, re.M)
        if m and not code:
            code = m.group(1)
        m = re.search(r"^ZAHLEN=\(([\d ]+)\)", s, re.M)
        if m:
            z = [int(v) for v in m.group(1).split()][:3]
            zahlen = (z + [0, 0, 0])[:3]
    except Exception as e:
        log("dart-start.sh nicht lesbar: %s" % e)
    return code, zahlen

def kamera_pfade():
    """Je USB-Kamera ein Eintrag (ohne die zusätzlichen 'usbv2'-Namen), feste Reihenfolge nach USB-Buchse."""
    p = sorted(x for x in glob.glob("/dev/v4l/by-path/*-video-index0") if "-usb-" in os.path.basename(x))
    return p[:3]


class Kamera(threading.Thread):
    """Liest eine Kamera dauernd aus. Das neueste Bild steht in .bild (BGR, 1280x720)."""
    def __init__(self, nr, pfad):
        super().__init__(daemon=True)
        self.nr, self.pfad, self.id = nr, pfad, "K%d" % nr
        self.bild, self.zeit, self.lock = None, 0.0, threading.Lock()
        self.status, self.info, self.fps, self.fehler, self.offen = "aus", "", 0.0, 0, 0
        self.takt = 0.5                                   # ohne Anforderung: so oft wird ein Bild ausgepackt (Vorschau, s)
        self.wunsch = None                                # (Breite, Funktion): nächstes Bild verkleinert an den Rechenkern

    def anfordern(self, aw, fn):
        self.wunsch = (aw, fn)

    def liefern(self, f):
        w = self.wunsch
        if not w:
            return
        self.wunsch = None
        try:
            aw = max(320, min(1280, int(w[0])))
            ah = int(round(aw * f.shape[0] / f.shape[1]))
            g = f if aw == f.shape[1] else cv2.resize(f, (aw, ah), interpolation=cv2.INTER_AREA)
            w[1](aw, ah, cv2.cvtColor(g, cv2.COLOR_BGR2RGBA).tobytes())
        except Exception as e:
            log("Kamera %d: Bild für den Rechenkern: %s" % (self.nr, e))

    def oeffnen(self):
        dev = os.path.realpath(self.pfad)
        cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
        if not cap.isOpened():
            return None, "lässt sich nicht öffnen (%s) – läuft noch ein Browser-Fenster mit dieser Kamera?" % dev
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        cap.set(cv2.CAP_PROP_FPS, 30)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        fc = int(cap.get(cv2.CAP_PROP_FOURCC))
        four = "".join(chr((fc >> 8 * i) & 255) for i in range(4)) if fc > 0 else "?"
        return cap, "%s %dx%d %s %.0f B/s" % (dev, cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT), four, cap.get(cv2.CAP_PROP_FPS))

    def run(self):
        if FAKE:
            return self.run_fake()
        while True:
            cap, info = None, ""
            try:
                cap, info = self.oeffnen()
            except Exception as e:
                info = "Fehler beim Öffnen: %s" % e
            if cap is None:
                if self.status != "fehlt" or self.info != info:
                    log("Kamera %d: %s" % (self.nr, info))
                self.status, self.info = "fehlt", info
                time.sleep(3)
                continue
            self.offen += 1
            self.status, self.info, self.fehler = "start", info, 0
            log("Kamera %d geöffnet: %s" % (self.nr, info))
            n, t0, letzt = 0, time.time(), 0.0
            try:
                while True:
                    if not cap.grab():                      # Bild abholen (noch nicht auspacken)
                        self.fehler += 1
                        if self.fehler > 30:
                            break
                        time.sleep(0.05)
                        continue
                    self.fehler = 0
                    n += 1
                    jetzt = time.time()
                    if self.wunsch or jetzt - letzt >= self.takt:   # nur auspacken, wenn ein Bild gebraucht wird (spart Rechenzeit)
                        ok, f = cap.retrieve()
                        if ok and f is not None and f.ndim == 3:
                            with self.lock:
                                self.bild, self.zeit = f, jetzt
                            self.liefern(f)
                            if self.status != "ok":
                                self.status = "ok"
                                log("Kamera %d liefert Bilder (%dx%d)" % (self.nr, f.shape[1], f.shape[0]))
                            letzt = jetzt
                    if jetzt - t0 >= 5:
                        self.fps, n, t0 = n / (jetzt - t0), 0, jetzt
            except Exception as e:
                log("Kamera %d: Fehler beim Lesen: %s" % (self.nr, e))
            try:
                cap.release()
            except Exception:
                pass
            self.status = "fehlt"
            log("Kamera %d: Verbindung verloren – neuer Versuch in 2 s" % self.nr)
            time.sleep(2)

    def run_fake(self):
        dat = sorted(glob.glob(os.path.join(FAKE, "K%d*" % self.nr)))
        bilder = [b for b in (cv2.imread(d) for d in dat) if b is not None]
        if not bilder:
            self.status, self.info = "fehlt", "keine Testbilder"
            return
        self.info, t0 = "Testbilder (%d)" % len(bilder), time.time()
        dauer, erst = float(os.environ.get("DART_FAKE_T", "1")), float(os.environ.get("DART_FAKE_T0", "0"))
        bilder = [cv2.resize(b, (1280, 720)) for b in bilder]
        while True:
            t = time.time() - t0
            i = 0 if t < erst else min(len(bilder) - 1, int((t - erst) / dauer)) if os.environ.get("DART_FAKE_T0") else int(t / dauer) % len(bilder)
            f = bilder[i]
            with self.lock:
                self.bild, self.zeit = f, time.time()
            self.status, self.fps = "ok", 10.0
            self.liefern(f)
            time.sleep(0.05)

    def aktuell(self, max_alter=3.0):
        with self.lock:
            if self.bild is None or time.time() - self.zeit > max_alter:
                return None
            return self.bild.copy()


def jpeg_url(bild, qual):
    ok, buf = cv2.imencode(".jpg", bild, [cv2.IMWRITE_JPEG_QUALITY, qual])
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode() if ok else ""

def vorschau(k, breite=480):
    f = k.aktuell()
    if f is None:
        return None
    h = int(round(breite * f.shape[0] / f.shape[1]))
    g = cv2.resize(f, (breite, h), interpolation=cv2.INTER_AREA)
    txt = "Kamera %d  %s  neu %s" % (k.nr, hm(), VERSION)
    cv2.putText(g, txt, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(g, txt, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 255), 1, cv2.LINE_AA)
    return g


def zeichne(g, o):
    """Vorschaubild fürs Tablet: Ringe, Feldgrenzen, gelbe 20, gemeldete Pfeile (Lage vom Rechenkern, Anteile 0..1)."""
    h, w = g.shape[:2]
    P = lambda p: (int(round(p[0] * w)), int(round(p[1] * h)))
    try:
        for r in o.get("rings", []):
            cv2.polylines(g, [np.array([P(p) for p in r], np.int32)], True, (60, 220, 60), 1, cv2.LINE_AA)
        for l in o.get("lines", []):
            cv2.line(g, P(l[:2]), P(l[2:4]), (60, 220, 60), 1, cv2.LINE_AA)
        for m in o.get("marks", []):
            x, y = P(m[:2])
            cv2.drawMarker(g, (x, y), (255, 0, 255), cv2.MARKER_CROSS, 14, 2, cv2.LINE_AA)
            cv2.putText(g, "%s %s" % (m[2], m[3]), (x + 8, y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 0, 255), 1, cv2.LINE_AA)
        if o.get("p20"):
            x, y = P(o["p20"])
            cv2.circle(g, (x, y), 13, (0, 0, 0), -1, cv2.LINE_AA)
            cv2.circle(g, (x, y), 12, (0, 212, 255), 2, cv2.LINE_AA)
            cv2.putText(g, "20", (x - 9, y + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 212, 255), 1, cv2.LINE_AA)
    except Exception as e:
        log("Vorschau zeichnen: %s" % e)
    return g


class Kern:
    """Ein Rechenkern (Node) für eine Kamera: bekommt Bilder, liefert Meldungen."""
    def __init__(self, Z, k):
        self.Z, self.k, self.p, self.wl = Z, k, None, threading.Lock()
        self.hb, self.cal, self.st, self.ms, self.starts, self.recimg = 0.0, False, "", 0, 0, {}
        # Ab 0.5 schreibt ein eigener Faden in den Rechenkern. Wer etwas schicken will (Tablet-Nachricht, Kamerabild),
        # legt es nur ab und ist sofort fertig – rechnet der Kern gerade, muss niemand warten (vorher konnten dabei
        # die Leitungen der Brücke volllaufen).
        self.q, self.fr, self.ev, self.t_start = collections.deque(maxlen=400), None, threading.Event(), time.time()
        threading.Thread(target=self.schreiber, daemon=True).start()
        threading.Thread(target=self.lauf, daemon=True).start()

    def lebt(self):
        return self.p is not None and self.p.poll() is None and time.time() - self.hb < 6

    def schreibe(self, art, daten):
        with self.wl:
            if art == b"F":
                self.fr = daten                            # von den Bildern zählt nur das neueste
            else:
                self.q.append(daten)
        self.ev.set()

    def schreiber(self):
        while True:
            self.ev.wait(1.0)
            self.ev.clear()
            while True:
                with self.wl:
                    if self.q:
                        art, d = b"M", self.q.popleft()
                    elif self.fr is not None:
                        art, d, self.fr = b"F", self.fr, None
                    else:
                        break
                p = self.p
                if p is None or p.poll() is not None:
                    continue
                t0 = time.time()
                try:
                    p.stdin.write(art + struct.pack("<I", len(d)) + d)
                    p.stdin.flush()
                except Exception:
                    pass
                if time.time() - t0 > 8 and time.time() - self.t_start > 60:     # (gleich nach dem Start rechnet er sich erst ein)
                    log("Kamera %d: Rechenkern nahm %.0f s lang nichts an" % (self.k.nr, time.time() - t0))

    def bild(self, w, h, rgba):
        self.schreibe(b"F", struct.pack("<HH", w, h) + rgba)

    def nachricht(self, m):
        self.schreibe(b"M", json.dumps(m).encode())

    def lauf(self):
        Z, k = self.Z, self.k
        node, app, kern = shutil.which("node") or shutil.which("nodejs"), os.path.join(DIR, "app.js"), os.path.join(DIR, "dart-kern.js")
        while True:
            if not node or not os.path.isfile(app) or not os.path.isfile(kern):
                if not self.starts:
                    log("Kamera %d: Rechenkern kann nicht starten (%s)" % (k.nr, "Node fehlt" if not node else "app.js oder dart-kern.js fehlt"))
                self.starts = 1
                time.sleep(10)
                node = shutil.which("node") or shutil.which("nodejs")
                continue
            os.makedirs(DATEN, exist_ok=True)
            with self.wl:                                  # Reste für den alten Kern verwerfen
                self.q.clear()
                self.fr = None
            try:
                err = open(os.path.join(DATEN, "kern%d.log" % k.nr), "ab")
                self.p = subprocess.Popen([node, "--max-old-space-size=700", kern, app, str(k.nr), str(Z.zahlen[k.nr - 1]), Z.code or "0000",
                                           os.path.join(DATEN, "kern%d.json" % k.nr)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err)
            except Exception as e:
                log("Kamera %d: Rechenkern-Start fehlgeschlagen: %s" % (k.nr, e))
                time.sleep(5)
                continue
            self.starts += 1
            self.t_start = time.time()
            log("Kamera %d: Rechenkern gestartet" % k.nr)
            try:
                for zeile in self.p.stdout:
                    try:
                        m = json.loads(zeile)
                    except Exception:
                        continue
                    self.von_kern(m)
            except Exception as e:
                log("Kamera %d: Rechenkern-Leitung: %s" % (k.nr, e))
            try:
                self.p.kill()
            except Exception:
                pass
            log("Kamera %d: Rechenkern beendet – Neustart in 3 s" % k.nr)
            time.sleep(3)

    def von_kern(self, m):
        Z, k, t = self.Z, self.k, m.get("t")
        if t == "_f":                                      # Kern möchte das nächste Bild
            k.anfordern(m.get("aw", 800), self.bild)
            return
        if t == "_err":
            log("Kamera %d: Kern-Fehler (%s): %s" % (k.nr, m.get("wo"), str(m.get("e"))[:300]))
            return
        if t == "_rec":                                    # Bild zu einem Testdaten-Eintrag merken + kleine Kopie ans Tablet (Sicherung)
            f = k.aktuell(5)
            if f is not None:
                self.recimg[m["n"]] = jpeg_url(f, 80)
                for alt in [n for n in self.recimg if n <= m["n"] - 22]:
                    del self.recimg[alt]
                r = dict(m.get("r") or {})
                r.update(img=jpeg_url(cv2.resize(f, (800, 450), interpolation=cv2.INTER_AREA), 70), w=800, h=450)
                Z.sende(k.nr, {"t": "rb", "id": k.id, "r": r, "z": m.get("z", 0)})
            return
        if t in ("hb", "hello"):
            self.hb, self.cal, self.st = time.time(), bool(m.get("cal")), m.get("st", "")
            self.ms = m.get("ms", self.ms)
            if k.status != "ok":
                m["st"], m["cal"] = "off", False
        elif t == "img" and isinstance(m.get("data"), str) and m["data"].startswith("@"):
            g = vorschau(k)
            if g is None:
                m = {"t": "img", "id": k.id, "data": "", "why": "Kamera liefert kein Bild (%s)" % (k.info or k.status)}
            else:
                try:
                    zeichne(g, json.loads(m["data"][1:]))
                except Exception:
                    pass
                m["data"] = jpeg_url(g, 60)
        elif t == "imgF":
            f = k.aktuell(5)
            m["data"] = jpeg_url(f, 90) if f is not None else ""
            m["w"], m["h"] = (0, 0) if f is None else (f.shape[1], f.shape[0])
            for r in m.get("rec") or []:
                if isinstance(r.get("img"), str) and r["img"].startswith("@"):
                    r["img"] = self.recimg.get(int(r["img"][1:] or 0), "")
            m["rec"] = [r for r in (m.get("rec") or []) if r.get("img")]
            m["neu"], m["kamera"] = VERSION, k.info
            with LOCK:
                m["pilog"] = LOG[-150:]                    # Protokoll dieses Programms (Brücke, Kameras, Rechenkerne)
        Z.sende(k.nr, m)


class Zentrale:
    """Verteiler: Tablet <-> Brücke <-> dieses Programm <-> Rechenkerne. Je Kamera eine eigene Verbindung zum Tablet (wie bisher je Fenster)."""
    def __init__(self, kams, code, zahlen):
        self.kams, self.code, self.zahlen = kams, code, zahlen
        self.aus, self.nr, self.cond = [], 0, threading.Condition()
        self.bruecke, self.tablet, self.verb, self.aus_zeit = 0.0, 0.0, 0, 0.0
        self.br = {"lag": 0, "vis": "?", "q": 0, "buf": 0, "maxlag": 0}  # was die Brücke über sich meldet (zur Fehlersuche)
        self.br_in = 0.0                                   # wann zuletzt etwas von der Brücke kam (/in)
        self.kerne = [] if os.environ.get("DART_OHNE_KERN") else [Kern(self, k) for k in kams]
        threading.Thread(target=self.takt, daemon=True).start()

    def sende(self, knr, m):
        with self.cond:
            self.nr += 1
            self.aus.append((self.nr, {"k": knr, "m": m}))
            if len(self.aus) > 600:                       # Brücke weg: alte Nachrichten verwerfen
                del self.aus[:300]
            self.cond.notify_all()

    def hole(self, ab, warte=8.0):
        ende = time.time() + warte
        with self.cond:
            if ab <= 0:                                    # Brücke frisch gestartet: alte Nachrichten nicht nachliefern (Tablet ist eh neu verbunden)
                ab = max(0, self.nr - 3)
            elif ab > self.nr:                             # Programm wurde neu gestartet: Zählung beginnt von vorn
                ab = 0
            while True:
                neu = [m for n, m in self.aus if n > ab]
                if neu or time.time() >= ende:
                    return self.nr, neu
                self.cond.wait(max(0.05, ende - time.time()))

    def brueckenbericht(self, arg):
        b = self.br
        try:
            lag = int(float(arg.get("lag", "0") or 0))
            b.update(lag=lag, vis=arg.get("vis", "?")[:12], q=int(arg.get("q", "0") or 0), buf=int(float(arg.get("buf", "0") or 0)))
            b["maxlag"] = max(b["maxlag"], lag)
            if lag > 3000:
                log("Brücke stockte %.1f s (Seite %s)" % (lag / 1000, b["vis"]))
        except Exception:
            pass

    def kern(self, knr):
        return self.kerne[knr - 1] if 1 <= knr <= len(self.kerne) else None

    def hallo(self, k, art="hb"):
        return {"t": art, "id": k.id, "role": "side", "cal": False, "st": "off" if k.status != "ok" else "search",
                "zahl": self.zahlen[k.nr - 1], "neu": VERSION}

    def takt(self):
        start, letzter = time.time(), 0.0
        while True:
            time.sleep(1)
            jetzt = time.time()
            # Wächter: meldet sich die Brücke 20 s lang nicht (Browser eingefroren oder abgestürzt), wird sie beendet – der Start-Wächter öffnet sie neu
            if jetzt - start > 45 and jetzt - max(self.bruecke, letzter) > 20:
                letzter = jetzt
                b = self.br
                log("Brücke meldet sich nicht – wird neu gestartet (letzte Abfrage vor %.0f s, letzte Nachricht von ihr vor %s, "
                    "zuletzt gemeldet: %s, Stocken bis %d ms, Warteschlange %d, Puffer %d)" % (
                        jetzt - self.bruecke, ("%.0f s" % (jetzt - self.br_in)) if self.br_in else "–", b["vis"], b["lag"], b["q"], b["buf"]))
                for z in bruecke_log_ende():
                    log("  Brücke: " + z)
                try:
                    subprocess.call(["pkill", "-9", "-f", "--", "--user-data-dir=" + os.path.join(DATEN, "bruecke")])
                except Exception:
                    pass
            # Wächter für die Rechenkerne: kommt von einem 45 s lang kein Lebenszeichen, hängt er – beenden, er startet dann neu
            for kn in self.kerne:
                p = kn.p
                if p is not None and p.poll() is None and kn.hb and jetzt - kn.hb > 45 and jetzt - kn.t_start > 90:
                    log("Kamera %d: Rechenkern antwortet seit %.0f s nicht – wird neu gestartet" % (kn.k.nr, jetzt - kn.hb))
                    kn.hb = jetzt
                    try:
                        p.kill()
                    except Exception:
                        pass
            if time.time() - self.bruecke < 12 and self.verb:
                for k in self.kams:                        # Kamera ohne laufenden Rechenkern meldet sich selbst (als "noch nicht bereit")
                    kn = self.kern(k.nr)
                    if not (kn and kn.lebt()):
                        self.sende(k.nr, self.hallo(k))

    def empfang(self, m, knr):
        t = m.get("t") if isinstance(m, dict) else None
        self.br_in = time.time()
        if t == "_br":                                     # die Brücke meldet etwas über sich (eingefroren, unsichtbar …)
            log("Brücke: %s (%s)" % (str(m.get("e"))[:40], str(m.get("vis"))[:20]))
            return
        k = self.kams[max(1, min(3, knr)) - 1]
        kn = self.kern(k.nr)
        if t in ("hb", "hello"):                           # Herzschlag vom Tablet: nur merken – der Rechenkern braucht ihn nicht
            self.tablet = time.time()
            return
        if t == "_open":
            log("Kamera %d: Verbindung zum Tablet steht" % k.nr)
            if not (kn and kn.lebt()):
                self.sende(k.nr, self.hallo(k, "hello"))
        if t == "_zu":
            log("Kamera %d: Verbindung zum Tablet getrennt" % k.nr)
            return
        if t == "aus":                                     # "Pi herunterfahren" (kommt je Verbindung einmal – nur einmal ausführen)
            if time.time() - self.aus_zeit > 20:
                self.aus_zeit = time.time()
                log("Herunterfahren angefordert")
                self.sende(k.nr, {"t": "ausok", "id": k.id})
                threading.Timer(1.5, herunterfahren).start()
            return
        if kn and kn.lebt():
            kn.nachricht(m)
            return
        # ohne Rechenkern: Bilder trotzdem zeigen
        if t == "snap":
            g = vorschau(k)
            self.sende(k.nr, {"t": "img", "id": k.id, "data": jpeg_url(g, 60)} if g is not None
                       else {"t": "img", "id": k.id, "data": "", "why": "Kamera liefert kein Bild (%s)" % (k.info or k.status)})
        elif t == "snapF":
            with LOCK:
                lg = LOG[-120:]
            f = k.aktuell()
            self.sende(k.nr, {"t": "imgF", "id": k.id, "data": jpeg_url(f, 90) if f is not None else "", "cal": None,
                              "zahl": self.zahlen[k.nr - 1], "w": 0 if f is None else f.shape[1], "h": 0 if f is None else f.shape[0],
                              "st": k.status, "aw": 1280, "log": lg, "rec": [], "turn": [], "neu": VERSION, "kamera": k.info})


def bruecke_log_ende(n=6):
    """Die letzten Zeilen, die der Brücken-Browser selbst geschrieben hat (aus der letzten Minute) – zur Fehlersuche."""
    p = os.path.join(DATEN, "bruecke.log")
    try:
        if time.time() - os.path.getmtime(p) > 90:
            return []
        with open(p, "rb") as f:
            f.seek(max(0, os.path.getsize(p) - 6000))
            z = [x.strip() for x in f.read().decode("utf-8", "replace").splitlines() if x.strip()]
        return [x[:220] for x in z[-n:]]
    except Exception:
        return []


def herunterfahren():
    if os.environ.get("DART_AUS_TEST"):
        open(os.environ["DART_AUS_TEST"], "w").write("aus")
        return
    for cmd in (["sudo", "-n", "shutdown", "-h", "now"], ["systemctl", "poweroff"]):
        try:
            if subprocess.call(cmd) == 0:
                return
        except Exception:
            pass


BRUECKE = r"""<!doctype html><html lang="de"><head><meta charset="utf-8"><title>Dart-Brücke</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font-family:Arial,sans-serif;margin:10px;background:#111;color:#eee}h1{font-size:18px;margin:0 0 6px}
#st{font-weight:700;padding:6px 10px;border-radius:8px;display:inline-block;background:#b7791f}#st.ok{background:#1a7f4b}
img{width:100%;border-radius:6px;display:block;margin-top:6px;background:#000;min-height:60px}.mu{color:#aaa;font-size:13px;margin-top:6px}pre{font-size:11px;white-space:pre-wrap;color:#bbb}</style></head><body>
<h1>Dart-Kameras · neues Programm __VERSION__</h1><div id="st">startet …</div>
<div class="mu">Dieses Fenster hält nur die Verbindung zum Tablet (Code __CODE__). Bitte offen lassen.</div>
<img id="b1"><img id="b2"><img id="b3"><pre id="lg"></pre>
<script>
const CODE="__CODE__",V={};let CUR=0,NTX=0,NRX=0,MSG="",LAG=0,LT=Date.now();
// Stockt diese Seite (Rechner beschäftigt, Browser bremst sie)? Wird bei jeder Abfrage mitgemeldet.
setInterval(()=>{const n=Date.now();LAG=Math.max(LAG,n-LT-500);LT=n},500);
for(const e of ["freeze","resume","visibilitychange"])document.addEventListener(e,()=>post({t:"_br",e,vis:document.visibilityState},0));
const st=()=>{const n=[1,2,3].filter(k=>V[k]&&V[k].c&&V[k].c.open).length,e=document.getElementById("st");e.textContent=n==3?"Tablet verbunden (3 Kameras)":n?"Tablet verbunden ("+n+" von 3 Kameras)":(MSG||"Suche das Tablet …");e.className=n==3?"ok":""};
// Nachrichten vom Tablet: gesammelt und immer nur EINE Sendung zur Zeit – so bleibt die Abfrage-Leitung (/out) immer frei.
const IQ=[];let INF=0;
function post(m,k){IQ.push([k,m]);if(IQ.length>600)IQ.splice(0,IQ.length-600);flush()}
async function flush(){if(INF||!IQ.length)return;INF=1;const b=IQ.splice(0,IQ.length);
 try{const ac=new AbortController(),to=setTimeout(()=>ac.abort(),10000);await fetch("/in",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({b}),signal:ac.signal});clearTimeout(to)}catch(e){}
 INF=0;if(IQ.length)setTimeout(flush,0)}
function bufMax(){let b=0;for(const k of [1,2,3]){const c=V[k]&&V[k].c;if(c&&c.open){try{b=Math.max(b,(c.dataChannel&&c.dataChannel.bufferedAmount)||0,(c.bufferSize||0)*16000)}catch(e){}}}return b}
function csend(c,o){const j=JSON.stringify(o);if(j.length<=4000){c.send(o);return}const k=Date.now().toString(36)+Math.random().toString(36).slice(2,6),n=Math.ceil(j.length/4000);for(let i=0;i<n;i++)c.send({t:"pt",k,i,n,d:j.slice(i*4000,(i+1)*4000)})}
function rx(k,m){const v=V[k];v.rx=Date.now();NRX++;if(!m||typeof m!="object")return;
 if(m.t=="pt"&&typeof m.k=="string"&&m.n>0&&m.n<8000){const B=v.pt[m.k]||(v.pt[m.k]={p:[],c:0});if(B.p[m.i]==null){B.p[m.i]=String(m.d);B.c++}if(B.c>=m.n){delete v.pt[m.k];let o=null;try{o=JSON.parse(B.p.join(""))}catch(e){}if(o)post(o,k)}return}
 post(m,k)}
function dial(k){const v=V[k];if(!v.p||v.p.destroyed||(v.c&&v.c.open))return;try{const c=v.p.connect("ldart-"+CODE,{reliable:true,serialization:"json"});if(v.c&&v.c!==c){try{v.c.close()}catch(e){}}v.c=c;
 c.on("open",()=>{v.rx=Date.now();MSG="";st();post({t:"_open"},k)});c.on("data",m=>rx(k,m));c.on("close",()=>{if(v.c===c){st();post({t:"_zu"},k)}});c.on("error",()=>{})}catch(e){}}
function mk(k){const v=V[k]=V[k]||{pt:{},rx:0};v.c=null;v.p=new Peer({debug:0});v.p.on("open",()=>dial(k));v.p.on("error",e=>{MSG=e.type=="peer-unavailable"?"Tablet nicht gefunden – ist die App dort offen? Ich versuche es weiter …":"Verbindungsproblem ("+e.type+") – versuche es weiter …";st()});v.p.on("disconnected",()=>{try{v.p.reconnect()}catch(e){}})}
function start(){if(!window.Peer){MSG="Verbindungs-Bibliothek fehlt (Internet?)";st();setTimeout(()=>location.reload(),15000);return}
 if(!/^\d{4}$/.test(CODE)){MSG="Kein Code gefunden (dart-start.sh)";st();return}for(const k of [1,2,3])mk(k);st()}
setInterval(()=>{const now=Date.now();for(const k of [1,2,3]){const v=V[k];if(!v||!v.p)continue;if(v.p.destroyed){mk(k);continue}if(v.p.disconnected){try{v.p.reconnect()}catch(e){}continue}
 if(v.c&&v.c.open&&v.rx&&now-v.rx>15000){try{v.c.close()}catch(e){}v.c=null;v.rx=now}dial(k)}st()},5000);
async function pump(){for(;;){try{const n=[1,2,3].filter(k=>V[k]&&V[k].c&&V[k].c.open).length;const ac=new AbortController(),to=setTimeout(()=>ac.abort(),12000);
 const r=await fetch("/out?c="+CUR+"&v="+n+"&lag="+LAG+"&vis="+document.visibilityState+"&q="+IQ.length+"&buf="+bufMax(),{cache:"no-store",signal:ac.signal});LAG=0;const o=await r.json();clearTimeout(to);CUR=o.c;
 if(o.v&&o.v!="__VERSION__"){location.reload();return}
 for(const e of o.m){const v=V[e.k];if(v&&v.c&&v.c.open){try{csend(v.c,e.m);NTX++}catch(x){}}}}catch(e){await new Promise(r=>setTimeout(r,1500))}}}
function ladeLib(cb){if(window.Peer)return cb();const U=["/peerjs.min.js","https://cdn.jsdelivr.net/npm/peerjs@1.5.4/dist/peerjs.min.js","https://unpkg.com/peerjs@1.5.4/dist/peerjs.min.js"];let i=0;
 const nx=()=>{if(i>=U.length)return cb();const s=document.createElement("script");s.src=U[i++];s.onload=cb;s.onerror=nx;document.head.appendChild(s)};nx()}
if(!navigator.webdriver&&!/Headless/.test(navigator.userAgent))setInterval(()=>{for(const k of [1,2,3])document.getElementById("b"+k).src="/bild?k="+k+"&t="+Date.now();fetch("/status").then(r=>r.text()).then(t=>{document.getElementById("lg").textContent=t+"\nBrücke: gesendet "+NTX+", empfangen "+NRX})},4000);
ladeLib(()=>{start();pump()});
</script></body></html>"""


class Web(http.server.BaseHTTPRequestHandler):
    Z = None
    protocol_version = "HTTP/1.1"

    def antwort(self, code, daten, typ="text/plain; charset=utf-8"):
        if isinstance(daten, str):
            daten = daten.encode()
        self.send_response(code)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(daten)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(daten)

    def do_GET(self):
        Z = self.Z
        pfad, _, q = self.path.partition("?")
        arg = dict(p.split("=", 1) for p in q.split("&") if "=" in p)
        try:
            if pfad == "/":
                return self.antwort(200, BRUECKE.replace("__CODE__", Z.code).replace("__VERSION__", VERSION), "text/html; charset=utf-8")
            if pfad == "/out":
                Z.bruecke, Z.verb = time.time(), int(arg.get("v", "0") or 0)
                Z.brueckenbericht(arg)
                c, m = Z.hole(int(arg.get("c", "0") or 0))
                return self.antwort(200, json.dumps({"c": c, "m": m, "v": VERSION}), "application/json")
            if pfad == "/status":
                return self.antwort(200, status_text(Z))
            if pfad == "/bild":
                k = Z.kams[max(1, min(3, int(arg.get("k", "1")))) - 1]
                g = vorschau(k, 640 if arg.get("gross") else 400)
                if g is None:
                    return self.antwort(503, "kein Bild")
                ok, buf = cv2.imencode(".jpg", g, [cv2.IMWRITE_JPEG_QUALITY, 70])
                return self.antwort(200, buf.tobytes(), "image/jpeg")
            if pfad == "/peerjs.min.js":
                p = os.path.join(DIR, "peerjs.min.js")
                if os.path.isfile(p) and os.path.getsize(p) > 20000:
                    return self.antwort(200, open(p, "rb").read(), "application/javascript")
                return self.antwort(404, "fehlt")
            if APPDIR and pfad.startswith("/app/"):
                p = os.path.normpath(os.path.join(APPDIR, pfad[5:] or "index.html"))
                if p.startswith(os.path.normpath(APPDIR)) and os.path.isfile(p):
                    typ = "text/html; charset=utf-8" if p.endswith(".html") else "application/json" if p.endswith(".json") else "application/octet-stream"
                    return self.antwort(200, open(p, "rb").read(), typ)
            return self.antwort(404, "?")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self.antwort(500, "Fehler: %s" % e)
            except Exception:
                pass

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", "0") or 0)
            roh = self.rfile.read(n) if n > 0 else b""
            if self.path.startswith("/in"):
                if TEST_INVERZ:                            # nur für Tests: langsame Annahme nachstellen
                    time.sleep(TEST_INVERZ)
                try:
                    q = self.path.partition("?")[2]
                    m = json.loads(roh.decode("utf-8", "replace"))
                    if isinstance(m, dict) and isinstance(m.get("b"), list):   # ab 0.5: mehrere Nachrichten in einer Sendung
                        for e in m["b"]:
                            if isinstance(e, list) and len(e) == 2:
                                self.Z.empfang(e[1], int(e[0] or 0))
                    else:
                        self.Z.empfang(m, int(dict(p.split("=", 1) for p in q.split("&") if "=" in p).get("k", "1")))
                except Exception as e:
                    log("Nachricht nicht verstanden: %s" % e)
                return self.antwort(200, "ok")
            return self.antwort(404, "?")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *a):
        pass


def status_text(Z):
    jetzt = time.time()
    z = ["Dart-Kameras neues Programm %s | %s | Code %s" % (VERSION, time.strftime("%F %T"), Z.code or "FEHLT"),
         "Brücke zum Tablet: %s | Tablet: %s" % ("läuft" if jetzt - Z.bruecke < 12 else "FEHLT",
                                                         ("verbunden (%d von 3 Kameras)" % Z.verb) if Z.verb and jetzt - Z.tablet < 8 else "nicht verbunden"),
         "Brücke: längstes Stocken %d ms | Seite %s | wartende Nachrichten %d | Sendepuffer %d" % (Z.br["maxlag"], Z.br["vis"], Z.br["q"], Z.br["buf"])]
    for k in Z.kams:
        alter = jetzt - k.zeit if k.zeit else -1
        kn = Z.kern(k.nr)
        kt = "Rechenkern FEHLT" if not kn else ("Rechenkern läuft, %s, letzte Prüfung %s ms" % ("Scheibe erkannt" if kn.cal else "sucht die Scheibe (%s)" % (kn.st or "?"), kn.ms)) if kn.lebt() else "Rechenkern startet/steht (Starts: %d)" % kn.starts
        z.append("Kamera %d (Zahl %s): %s | %.0f Bilder/s | letztes Bild vor %s | %s" % (k.nr, Z.zahlen[k.nr - 1], k.status.upper() if k.status != "ok" else "ok", k.fps,
                                                                              ("%.1f s" % alter) if alter >= 0 else "–", kt))
    try:
        z.append("Last %s | Temperatur %s" % (open("/proc/loadavg").read().split()[0],
                                             subprocess.run(["vcgencmd", "measure_temp"], capture_output=True, text=True, timeout=2).stdout.strip().replace("temp=", "") or "?"))
    except Exception:
        pass
    with LOCK:
        z += ["", "Letzte Meldungen:"] + LOG[-8:]
    return "\n".join(z) + "\n"


def app_bereiten():
    """Die Erkennung steckt in der Dart-App (index.html). Daraus wird app.js für die Rechenkerne gezogen."""
    src, ziel = os.path.join(DIR, "index.html"), os.path.join(DIR, "app.js")
    try:
        if os.path.isfile(src) and (not os.path.isfile(ziel) or os.path.getmtime(src) > os.path.getmtime(ziel)):
            m = re.findall(r"<script>(.*?)</script>", open(src, encoding="utf-8").read(), re.S)
            if len(m) == 1 and "function findDart(" in m[0]:
                open(ziel, "w", encoding="utf-8").write(m[0])
                v = re.search(r"· Version (\d+)</span>", open(src, encoding="utf-8").read())
                log("Erkennung aus der Dart-App übernommen (Version %s)" % (v.group(1) if v else "?"))
            else:
                log("index.html enthält keine Erkennung – Rechenkerne können nicht starten")
    except Exception as e:
        log("app.js: %s" % e)


def selbsttest():
    """python3 dart-pi.py --test : jede Kamera kurz öffnen, 40 Bilder lesen, Ergebnis zeigen (Browser-Fenster müssen zu sein)."""
    pf = kamera_pfade()
    print("Gefundene USB-Kameras: %d" % len(pf))
    for i, p in enumerate(pf, 1):
        k = Kamera(i, p)
        try:
            cap, info = k.oeffnen()
        except Exception as e:
            cap, info = None, str(e)
        if cap is None:
            print("Kamera %d: FEHLER %s" % (i, info))
            continue
        t0, n, f = time.time(), 0, None
        for _ in range(40):
            if cap.grab():
                n += 1
        ok, f = cap.retrieve()
        dt = time.time() - t0
        cap.release()
        if ok and f is not None:
            ziel = os.path.join(DIR, "test_k%d.jpg" % i)
            cv2.imwrite(ziel, f)
            print("Kamera %d: ok | %s | %d Bilder in %.1f s (%.0f/s) | Bild %dx%d Helligkeit %.0f | gespeichert %s" % (i, info, n, dt, n / max(dt, .01), f.shape[1], f.shape[0], float(np.mean(f)), ziel))
        else:
            print("Kamera %d: FEHLER kein Bild | %s" % (i, info))


def main():
    if "--test" in sys.argv:
        return selbsttest()
    code, zahlen = lies_start()
    app_bereiten()
    log("Start neues Programm %s | OpenCV %s | Code %s | Zahlen %s" % (VERSION, cv2.__version__, code or "FEHLT", zahlen))
    try:
        cv2.setNumThreads(2)
    except Exception:
        pass
    pf = [] if FAKE else kamera_pfade()
    for _ in range(30):                                    # nach dem Hochfahren kurz auf die Kameras warten
        if FAKE or len(pf) >= 3:
            break
        time.sleep(2)
        pf = kamera_pfade()
    if not FAKE:
        log("USB-Kameras gefunden: %d" % len(pf))
        for i, p in enumerate(pf, 1):
            log("  Kamera %d = Anschluss %s" % (i, os.path.basename(p).replace("-video-index0", "")))
    kams = [Kamera(i + 1, pf[i] if i < len(pf) else "") for i in range(3)]
    for k in kams:
        if FAKE or k.pfad:
            k.start()
            time.sleep(0.3)
        else:
            k.status, k.info = "fehlt", "nicht eingesteckt?"
    Web.Z = Zentrale(kams, code, zahlen)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Web)
    srv.daemon_threads = True
    log("bereit auf 127.0.0.1:%d" % PORT)
    srv.serve_forever()


if __name__ == "__main__":
    main()

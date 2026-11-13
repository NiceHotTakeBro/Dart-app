#!/usr/bin/env python3
# =============================================================================
#  Dart-Kameras: eigenes Programm auf dem Raspberry Pi (ersetzt die drei Browser-Fenster)
#  Stufe 1: liest die drei Ring-Kameras direkt (OpenCV) und zeigt die Bilder am Tablet
#           ("Kamerabilder ansehen", "Testdaten speichern", "Pi herunterfahren").
#  Die Pfeilerkennung kommt in der nächsten Stufe dazu.
#
#  Aufbau: Dieses Programm hört nur auf diesem Gerät (127.0.0.1:8771). Ein kleines
#  Browser-Fenster (die "Brücke", Seite /) hält die Verbindung zum Tablet und reicht
#  Nachrichten in beide Richtungen durch. Gerechnet wird nur hier.
# =============================================================================
import base64, glob, http.server, json, os, re, subprocess, sys, threading, time
import cv2, numpy as np

VERSION = "0.1"
PORT = int(os.environ.get("DART_PORT", "8771"))
HOME = os.path.expanduser("~")
DIR = os.path.dirname(os.path.abspath(__file__))
FAKE = os.environ.get("DART_FAKE", "")          # Test ohne Kameras: Ordner mit K1*.jpg/png, K2*, K3*
APPDIR = os.environ.get("DART_APPDIR", "")      # Test: Tablet-App unter /app/ ausliefern

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
        self.takt = float(os.environ.get("DART_TAKT", "0.1"))   # so oft wird ein Bild ausgepackt (s)

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
                    if jetzt - letzt >= self.takt:           # nur so oft auspacken wie nötig (spart Rechenzeit)
                        ok, f = cap.retrieve()
                        if ok and f is not None and f.ndim == 3:
                            with self.lock:
                                self.bild, self.zeit = f, jetzt
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
        self.info, i = "Testbilder (%d)" % len(bilder), 0
        while True:
            f = cv2.resize(bilder[i % len(bilder)], (1280, 720))
            with self.lock:
                self.bild, self.zeit = f, time.time()
            self.status, self.fps = "ok", 10.0
            i += 1
            time.sleep(1.0)

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


class Zentrale:
    """Nachrichten zwischen Tablet (über die Brücke) und diesem Programm – gleiche Sprache wie die bisherigen Kamera-Fenster."""
    def __init__(self, kams, code, zahlen):
        self.kams, self.code, self.zahlen = kams, code, zahlen
        self.aus, self.nr, self.cond = [], 0, threading.Condition()
        self.bruecke, self.tablet, self.verb = 0.0, 0.0, False
        threading.Thread(target=self.takt, daemon=True).start()

    def sende(self, m):
        with self.cond:
            self.nr += 1
            self.aus.append((self.nr, m))
            if len(self.aus) > 400:                       # Brücke weg: alte Nachrichten verwerfen
                del self.aus[:200]
            self.cond.notify_all()

    def hole(self, ab, warte=8.0):
        ende = time.time() + warte
        with self.cond:
            if ab > self.nr:                               # Programm wurde neu gestartet: Zählung beginnt von vorn
                ab = 0
            while True:
                neu = [m for n, m in self.aus if n > ab]
                if neu or time.time() >= ende:
                    return self.nr, neu
                self.cond.wait(max(0.05, ende - time.time()))

    def hallo(self, k, art="hb"):
        return {"t": art, "id": k.id, "role": "side", "cal": False, "st": "ok" if k.status == "ok" else "off",
                "zahl": self.zahlen[k.nr - 1], "neu": VERSION}

    def takt(self):
        while True:
            time.sleep(1)
            if time.time() - self.bruecke < 12 and self.verb:
                for k in self.kams:
                    self.sende(self.hallo(k))

    def empfang(self, m):
        t = m.get("t") if isinstance(m, dict) else None
        if t in ("hb", "hello"):
            self.tablet = time.time()
            return
        if t == "_open":                                   # Brücke hat das Tablet erreicht
            log("Tablet verbunden")
            for k in self.kams:
                self.sende(self.hallo(k, "hello"))
            return
        if t == "_zu":
            log("Verbindung zum Tablet getrennt")
            return
        if t == "snap":                                    # "Kamerabilder ansehen"
            for k in self.kams:
                g = vorschau(k)
                self.sende({"t": "img", "id": k.id, "data": jpeg_url(g, 60)} if g is not None
                           else {"t": "img", "id": k.id, "data": "", "why": "Kamera liefert kein Bild (%s)" % (k.info or k.status)})
            return
        if t == "snapF":                                   # "Testdaten speichern": volles Bild + Protokoll
            with LOCK:
                lg = LOG[-120:]
            for k in self.kams:
                f = k.aktuell()
                self.sende({"t": "imgF", "id": k.id, "data": jpeg_url(f, 90) if f is not None else "", "cal": None,
                            "zahl": self.zahlen[k.nr - 1], "w": 0 if f is None else f.shape[1], "h": 0 if f is None else f.shape[0],
                            "st": k.status, "aw": 1280, "log": lg, "rec": [], "turn": [], "neu": VERSION, "kamera": k.info})
            return
        if t == "aus":                                     # "Pi herunterfahren"
            log("Herunterfahren angefordert")
            self.sende({"t": "ausok", "id": self.kams[0].id})
            threading.Timer(1.5, herunterfahren).start()
            return
        # turn, set20, bk… : kommt mit der Pfeilerkennung

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
const CODE="__CODE__";let PEER=null,CONN=null,CUR=0,LASTRX=0,PT={},NTX=0,NRX=0;
const st=(t,ok)=>{const e=document.getElementById("st");e.textContent=t;e.className=ok?"ok":""};
const post=m=>fetch("/in",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(m)}).catch(()=>{});
function csend(o){const j=JSON.stringify(o);if(j.length<=4000){CONN.send(o);return}const k=Date.now().toString(36)+Math.random().toString(36).slice(2,6),n=Math.ceil(j.length/4000);for(let i=0;i<n;i++)CONN.send({t:"pt",k,i,n,d:j.slice(i*4000,(i+1)*4000)})}
function rx(m){LASTRX=Date.now();NRX++;if(!m||typeof m!="object")return;
 if(m.t=="pt"&&typeof m.k=="string"&&m.n>0&&m.n<8000){const B=PT[m.k]||(PT[m.k]={p:[],c:0});if(B.p[m.i]==null){B.p[m.i]=String(m.d);B.c++}if(B.c>=m.n){delete PT[m.k];let o=null;try{o=JSON.parse(B.p.join(""))}catch(e){}if(o)post(o)}return}
 post(m)}
function dial(){if(!PEER||PEER.destroyed||(CONN&&CONN.open))return;try{const c=PEER.connect("ldart-"+CODE,{reliable:true,serialization:"json"});if(CONN&&CONN!==c){try{CONN.close()}catch(e){}}CONN=c;
 c.on("open",()=>{LASTRX=Date.now();st("Tablet verbunden",true);post({t:"_open"})});c.on("data",rx);c.on("close",()=>{if(CONN===c){st("Tablet getrennt – suche …");post({t:"_zu"})}});c.on("error",()=>{})}catch(e){}}
function start(){if(!window.Peer){st("Verbindungs-Bibliothek fehlt (Internet?)");setTimeout(()=>location.reload(),15000);return}
 if(!/^\d{4}$/.test(CODE)){st("Kein Code gefunden (dart-start.sh)");return}
 PEER=new Peer({debug:0});PEER.on("open",dial);PEER.on("error",e=>{st(e.type=="peer-unavailable"?"Tablet nicht gefunden – ist die App dort offen? Ich versuche es weiter …":"Verbindungsproblem ("+e.type+") – versuche es weiter …")});
 PEER.on("disconnected",()=>{try{PEER.reconnect()}catch(e){}});st("Suche das Tablet …")}
setInterval(()=>{const now=Date.now();if(!PEER)return;if(PEER.destroyed){PEER=null;CONN=null;start();return}if(PEER.disconnected){try{PEER.reconnect()}catch(e){}return}
 if(CONN&&CONN.open&&LASTRX&&now-LASTRX>15000){try{CONN.close()}catch(e){}CONN=null;LASTRX=now;st("Tablet antwortet nicht – verbinde neu …")}dial()},5000);
async function pump(){for(;;){try{const r=await fetch("/out?c="+CUR+"&v="+(CONN&&CONN.open?1:0),{cache:"no-store"});const o=await r.json();CUR=o.c;if(CONN&&CONN.open)for(const m of o.m){try{csend(m);NTX++}catch(e){}}}catch(e){await new Promise(r=>setTimeout(r,1500))}}}
function ladeLib(cb){if(window.Peer)return cb();const U=["/peerjs.min.js","https://cdn.jsdelivr.net/npm/peerjs@1.5.4/dist/peerjs.min.js","https://unpkg.com/peerjs@1.5.4/dist/peerjs.min.js"];let i=0;
 const nx=()=>{if(i>=U.length)return cb();const s=document.createElement("script");s.src=U[i++];s.onload=cb;s.onerror=nx;document.head.appendChild(s)};nx()}
setInterval(()=>{for(const k of [1,2,3])document.getElementById("b"+k).src="/bild?k="+k+"&t="+Date.now();fetch("/status").then(r=>r.text()).then(t=>{document.getElementById("lg").textContent=t+"\nBrücke: gesendet "+NTX+", empfangen "+NRX})},3000);
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
                Z.bruecke, Z.verb = time.time(), arg.get("v") == "1"
                c, m = Z.hole(int(arg.get("c", "0") or 0))
                return self.antwort(200, json.dumps({"c": c, "m": m}), "application/json")
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
                try:
                    self.Z.empfang(json.loads(roh.decode("utf-8", "replace")))
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
         "Brücke (Browser-Fenster): %s | Tablet: %s" % ("läuft" if jetzt - Z.bruecke < 12 else "FEHLT",
                                                         "verbunden" if Z.verb and jetzt - Z.tablet < 8 else "nicht verbunden")]
    for k in Z.kams:
        alter = jetzt - k.zeit if k.zeit else -1
        z.append("Kamera %d: %s | %.0f Bilder/s | letztes Bild vor %s | %s" % (k.nr, k.status.upper() if k.status != "ok" else "ok", k.fps,
                                                                              ("%.1f s" % alter) if alter >= 0 else "–", k.info))
    try:
        z.append("Last %s | Temperatur %s" % (open("/proc/loadavg").read().split()[0],
                                             subprocess.run(["vcgencmd", "measure_temp"], capture_output=True, text=True, timeout=2).stdout.strip().replace("temp=", "") or "?"))
    except Exception:
        pass
    with LOCK:
        z += ["", "Letzte Meldungen:"] + LOG[-8:]
    return "\n".join(z) + "\n"


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

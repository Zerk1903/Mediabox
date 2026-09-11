#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MediaBox — Telefon Kumandası
=============================
Yerel ağda küçük bir HTTP sunucusu açar; telefonunuzun tarayıcısından
oynatmayı kontrol edersiniz (oynat/duraklat, atla, ses, bölüm değiştir).

GÜVENLİK
    • Sunucu yalnızca YEREL AĞDA dinler, internete açılmaz.
    • Her oturumda rastgele bir erişim anahtarı üretilir; anahtarsız
      istekler 403 döner.
    • Yalnızca oynatma komutları kabul edilir; dosya sistemine erişim yok.

KULLANIM
    s = KumandaSunucu(pencere)          # AnaPencere örneği
    s.baslat()                          # -> "http://192.168.1.5:8790/?a=xxxx"
    s.durdur()
"""

from __future__ import annotations

import http.server
import json
import secrets
import socket
import threading
import urllib.parse

VARSAYILAN_PORT = 8790


def yerel_ip() -> str:
    """
    Yerel ağdaki IP adresi (internete çıkmadan bulunur).

    Hiçbir koşulda istisna atmaz: ağ yoksa/kapalıysa "127.0.0.1" döner.
    (Ölçüldü: soket oluşturmanın kendisi bile bazı ortamlarda hata
    verebiliyor; bu yüzden socket() çağrısı da korumanın içinde.)
    """
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        try:
            if s is not None:
                s.close()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════
#  QR KODU
# ══════════════════════════════════════════════════════════════════
# NOT: Önce QR kodlayıcıyı sıfırdan yazmayı denedim ve BAŞARISIZ OLDU —
# üretilen kod gerçek bir okuyucuyla test edilince okunmadı (841 modülün
# 280'i hatalıydı). Standart QR'ın maske/biçim hesapları bu iş için fazla
# riskli. Bu yüzden: `qrcode` kütüphanesi varsa o kullanılır, yoksa QR
# hiç gösterilmez ve kullanıcı adresi elle yazar (kumanda yine çalışır).

def qr_var_mi() -> bool:
    try:
        import qrcode  # noqa: F401
        return True
    except ImportError:
        return False


def qr_svg(veri: str, kutu: int = 8) -> str:
    """QR kodunu SVG olarak döndürür; kütüphane yoksa boş dize."""
    try:
        import qrcode
    except ImportError:
        return ""
    try:
        q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                          box_size=1, border=0)
        q.add_data(veri)
        q.make(fit=True)
        m = q.get_matrix()
    except Exception:
        return ""
    n = len(m)
    kenar = 4
    en = (n + kenar * 2) * kutu
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{en}" height="{en}" '
         f'viewBox="0 0 {en} {en}"><rect width="{en}" height="{en}" fill="#fff"/>']
    for y in range(n):
        for x in range(n):
            if m[y][x]:
                p.append(f'<rect x="{(x + kenar) * kutu}" y="{(y + kenar) * kutu}" '
                         f'width="{kutu}" height="{kutu}" fill="#000"/>')
    p.append("</svg>")
    return "".join(p)


def qr_png(veri: str, kutu: int = 8) -> bytes:
    """QR kodunu PNG baytları olarak döndürür (Qt için); yoksa b''."""
    try:
        import qrcode
    except ImportError:
        return b""
    try:
        import io
        q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                          box_size=kutu, border=4)
        q.add_data(veri)
        q.make(fit=True)
        g = io.BytesIO()
        q.make_image(fill_color="black", back_color="white").save(g, format="PNG")
        return g.getvalue()
    except Exception:
        return b""


# ══════════════════════════════════════════════════════════════════
#  SUNUCU
# ══════════════════════════════════════════════════════════════════
SAYFA = """<!doctype html><html lang="tr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MediaBox Kumanda</title><style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:#0b0c10;color:#eceef4;font:15px system-ui,sans-serif;
 padding:16px;max-width:520px;margin:0 auto}
h1{font-size:15px;color:#8b90a0;font-weight:600;margin:4px 0 14px;letter-spacing:.4px}
#ad{font-size:19px;font-weight:800;margin-bottom:4px;line-height:1.3}
#alt{color:#8b90a0;font-size:12px;margin-bottom:14px}
.bar{height:5px;background:#1d2029;border-radius:3px;overflow:hidden;margin-bottom:6px}
.bar>i{display:block;height:100%;background:#E50914;width:0}
#sure{color:#8b90a0;font-size:12px;text-align:right;margin-bottom:16px}
.satir{display:flex;gap:10px;margin-bottom:10px}
button{flex:1;background:#1d2029;border:1px solid #2a2e3a;color:#eceef4;
 border-radius:11px;padding:16px 8px;font-size:15px;font-weight:600;cursor:pointer}
button:active{background:#2c313d;transform:scale(.97)}
.ana{background:#E50914;border-color:#E50914;font-size:17px;padding:20px}
.kucuk{font-size:13px;padding:12px 6px}
input[type=range]{width:100%;accent-color:#E50914;height:34px}
.etiket{color:#8b90a0;font-size:12px;margin:14px 0 4px}
#durum{margin-top:14px;color:#8b90a0;font-size:12px;min-height:16px}
</style></head><body>
<h1>◉ MEDIABOX KUMANDA</h1>
<div id="ad">—</div><div id="alt"></div>
<div class="bar"><i id="ilerleme"></i></div><div id="sure">0:00 / 0:00</div>
<div class="satir">
  <button class="kucuk" onclick="k('atla',-60)">« 60</button>
  <button class="kucuk" onclick="k('atla',-10)">« 10</button>
  <button class="kucuk" onclick="k('atla',10)">10 »</button>
  <button class="kucuk" onclick="k('atla',60)">60 »</button>
</div>
<div class="satir"><button class="ana" id="oynat" onclick="k('duraklat')">⏯ Oynat / Duraklat</button></div>
<div class="satir">
  <button onclick="k('onceki')">⏮ Önceki</button>
  <button onclick="k('sonraki')">⏭ Sonraki</button>
</div>
<div class="etiket">Ses</div>
<input type="range" min="0" max="130" value="100" id="ses" onchange="k('ses',this.value)">
<div class="satir">
  <button class="kucuk" onclick="k('sessiz')">🔇 Sessiz</button>
  <button class="kucuk" onclick="k('altyazi')">💬 Altyazı</button>
  <button class="kucuk" onclick="k('tamekran')">⛶ Tam ekran</button>
</div>
<div class="satir"><button class="kucuk" onclick="k('dur')">⏹ Durdur</button></div>
<div id="durum"></div>
<script>
const A=new URLSearchParams(location.search).get('a')||'';
function sn(s){s=Math.max(0,Math.floor(s||0));const h=Math.floor(s/3600),
 d=Math.floor(s%3600/60),n=s%60;return (h?h+':'+String(d).padStart(2,'0'):d)+':'+String(n).padStart(2,'0');}
async function k(c,v){
  try{const r=await fetch('/komut?a='+A+'&c='+c+(v!==undefined?'&v='+v:''));
      const d=await r.json(); if(d.mesaj) not(d.mesaj); yenile();}
  catch(e){ not('bağlantı yok'); }
}
function not(m){const e=document.getElementById('durum');e.textContent=m;
  clearTimeout(window._t);window._t=setTimeout(()=>e.textContent='',2500);}
async function yenile(){
  try{
    const r=await fetch('/durum?a='+A); const d=await r.json();
    document.getElementById('ad').textContent=d.ad||'—';
    document.getElementById('alt').textContent=d.alt||'';
    document.getElementById('sure').textContent=sn(d.konum)+' / '+sn(d.sure);
    document.getElementById('ilerleme').style.width=(d.sure?d.konum/d.sure*100:0)+'%';
    document.getElementById('oynat').textContent=d.duraklatildi?'▶ Oynat':'⏸ Duraklat';
  }catch(e){}
}
yenile(); setInterval(yenile,1500);
</script></body></html>"""


class _Islem(http.server.BaseHTTPRequestHandler):
    sunucu = None                    # KumandaSunucu örneği

    def log_message(self, *a):
        pass                         # konsolu kirletme

    def _json(self, veri, kod=200):
        g = json.dumps(veri, ensure_ascii=False).encode("utf-8")
        self.send_response(kod)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(g)))
        self.end_headers()
        self.wfile.write(g)

    def _yetki(self, sor) -> bool:
        return sor.get("a", [""])[0] == self.sunucu.anahtar

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        sor = urllib.parse.parse_qs(u.query)
        s = self.sunucu

        if u.path == "/":
            if not self._yetki(sor):
                self.send_response(403); self.end_headers()
                self.wfile.write(b"Erisim anahtari gerekli")
                return
            g = SAYFA.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(g)))
            self.end_headers()
            self.wfile.write(g)
            return

        if not self._yetki(sor):
            self._json({"hata": "yetkisiz"}, 403)
            return

        if u.path == "/durum":
            self._json(s.durum_al())
            return

        if u.path == "/komut":
            c = sor.get("c", [""])[0]
            v = sor.get("v", [None])[0]
            self._json(s.komut_al(c, v))
            return

        self._json({"hata": "bilinmeyen"}, 404)


class _Sunucu(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class KumandaSunucu:
    """
    Telefon kumandası sunucusu.

    `pencere` bir AnaPencere örneğidir; komutlar Qt ana iş parçacığına
    köprüyle aktarılır (yabancı iş parçacığından Qt'ye dokunmak çökertir).
    """

    def __init__(self, pencere, port: int = VARSAYILAN_PORT):
        self.pencere = pencere
        self.port = port
        self.anahtar = secrets.token_urlsafe(9)
        self.yalnizca_yerel = False        # yalnızca 127.0.0.1'e bağlanabildik mi?
        self._srv = None
        self._th = None
        self.adres = ""

    # ── yaşam döngüsü ──
    def calisiyor(self) -> bool:
        return self._srv is not None

    def baslat(self) -> str:
        """
        Sunucuyu başlatır ve erişim adresini döndürür.

        SAĞLAMLAŞTIRMA: Bu yordamın hiçbir adımı programı düşürmemeli.
        Bazı sistemlerde (güvenlik duvarı, kısıtlı ağ ad alanı, yalnızca
        IPv6 arayüz, SELinux/AppArmor kuralları) `0.0.0.0` bağlanması ya da
        `serve_forever` başlatması beklenmedik hata verebiliyor. Her adım
        ayrı ayrı korunuyor; başarısızlıkta düzgün bir RuntimeError atılır
        ve çağıran taraf kullanıcıya pencere gösterir.
        """
        if self._srv:
            return self.adres

        islem = type("_I", (_Islem,), {"sunucu": self})
        son_hata = None
        # Önce tüm arayüzler, olmazsa yalnızca yerel geri döngü denenir.
        for adres_ailesi in ("0.0.0.0", "127.0.0.1"):
            for p in range(self.port, self.port + 12):
                try:
                    self._srv = _Sunucu((adres_ailesi, p), islem)
                    self.port = p
                    break
                except Exception as e:            # OSError + beklenmedikler
                    son_hata = e
            if self._srv:
                if adres_ailesi == "127.0.0.1":
                    # Telefondan erişilemez ama program çökmez; kullanıcı bilsin
                    self.yalnizca_yerel = True
                break

        if not self._srv:
            raise RuntimeError(
                f"Ağ portu açılamadı: {son_hata}\n\n"
                "Güvenlik duvarınız 8790–8801 aralığını engelliyor olabilir.")

        try:
            self._th = threading.Thread(target=self._srv.serve_forever, daemon=True)
            self._th.start()
        except Exception as e:
            try:
                self._srv.server_close()
            except Exception:
                pass
            self._srv = None
            raise RuntimeError(f"Sunucu iş parçacığı başlatılamadı: {e}")

        try:
            ip = yerel_ip()
        except Exception:
            ip = "127.0.0.1"                      # IP bulunamazsa yine de çalış
        self.adres = f"http://{ip}:{self.port}/?a={self.anahtar}"
        return self.adres

    def durdur(self):
        if self._srv:
            try:
                self._srv.shutdown()
                self._srv.server_close()
            except Exception:
                pass
        self._srv = None
        self.adres = ""

    # ── durum / komut ──
    def durum_al(self) -> dict:
        """
        Oynatıcının anlık durumu (yalnızca sayı/metin döner).

        HTTP iş parçacığından çağrılır; buradan kaynaklanan bir hata
        sunucuyu ve dolayısıyla programı etkilememeli.
        """
        try:
            return self._durum_al()
        except Exception as e:
            return {"ad": "—", "alt": f"durum okunamadı ({type(e).__name__})",
                    "konum": 0, "sure": 0, "duraklatildi": True}

    def _durum_al(self) -> dict:
        o = getattr(self.pencere, "oynatici", None)
        if not o or not getattr(o, "mpv", None) or not o.mpv.yasiyor:
            return {"ad": "Oynatılmıyor", "alt": "", "konum": 0, "sure": 0,
                    "duraklatildi": True}
        ic = o.icerik
        ad = "—"
        alt = ""
        if ic is not None:
            ad = ic.dizi_kok_anahtari() or ic.temiz_ad() or ic.ad
            sb = ic.sezon_bolum()
            par = []
            if sb:
                par.append(f"S{sb[0]:02d}E{sb[1]:02d}")
            if len(o.liste) > 1:
                par.append(f"{o.sira + 1}/{len(o.liste)}")
            alt = "   ·   ".join(par)
        return {"ad": ad, "alt": alt,
                "konum": float(getattr(o, "_kon", 0) or 0),
                "sure": float(getattr(o, "_sur", 0) or 0),
                "duraklatildi": bool(o.mpv.ozellik("pause", False))}

    def komut_al(self, c: str, v=None) -> dict:
        """Komutu Qt ana iş parçacığında çalıştırır (hata programı düşürmez)."""
        try:
            return self._komut_al(c, v)
        except Exception as e:
            return {"hata": f"{type(e).__name__}: {e}"}

    def _komut_al(self, c: str, v=None) -> dict:
        import arayuz
        o = getattr(self.pencere, "oynatici", None)
        if not o:
            return {"hata": "oynatıcı yok"}

        def yap():
            # Ana iş parçacığında çalışır; buradaki bir hata Qt olay
            # döngüsünü bozmasın diye ayrıca korunuyor.
            try:
                _uygula()
            except Exception:
                pass

        def _uygula():
            if c == "duraklat":
                o.duraklat_degistir()
            elif c == "atla":
                o.atla(int(v or 10))
            elif c == "sonraki":
                o.komsu(1)
            elif c == "onceki":
                o.komsu(-1)
            elif c == "ses":
                o.ses.setValue(int(v or 100))
            elif c == "sessiz":
                o.sessiz()
            elif c == "tamekran":
                o.tam_ekran()
            elif c == "dur":
                o.kapat()
            elif c == "altyazi":
                # sıradaki altyazıya geç (Kapalı → 1 → 2 → Kapalı)
                n = o.a_alt.count()
                if n > 1:
                    o.a_alt.setCurrentIndex((o.a_alt.currentIndex() + 1) % n)

        arayuz.ana_is_parcasinda(yap)
        return {"ok": True, "mesaj": {"duraklat": "⏯", "sonraki": "⏭ sonraki bölüm",
                                      "onceki": "⏮ önceki bölüm", "dur": "⏹ durduruldu",
                                      "sessiz": "🔇", "tamekran": "⛶",
                                      "altyazi": "💬 altyazı değişti"}.get(c, "")}

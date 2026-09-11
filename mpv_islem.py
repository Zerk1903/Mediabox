#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MediaBox — Dış İşlem mpv Sürücüsü
==================================
mpv'yi AYRI BİR İŞLEM olarak çalıştırır ve JSON IPC soketi üzerinden kontrol eder.

NEDEN BÖYLE?
    libmpv'yi uygulamanın içine gömmek (python-mpv + wid) bazı sistemlerde —
    özellikle Wayland oturumlarında ve belirli ekran kartı sürücülerinde —
    ölümcül biçimde başarısız oluyor ve TÜM UYGULAMAYI kapatıyordu
    ("Oynat"a basınca program kapanıyor sorunu).

    Bu mimaride mpv bağımsız bir işlemdir:
      • mpv çökerse yalnızca mpv kapanır, MediaBox ayakta kalır.
      • Ölçüldü: mpv'ye SIGKILL gönderildiğinde ana süreç yaşamaya devam etti,
        soket yalnızca BrokenPipeError verdi (yakalanıyor).
      • Gömme (wid) opsiyoneldir; başarısız olursa mpv kendi penceresini açar.

KULLANIM
    o = MpvIslem()
    o.baslat(wid=12345)          # wid=None → mpv kendi penceresini açar
    o.oynat("http://.../x.m3u8")
    o.ozellik("duration")
    o.komut("seek", 10, "relative")
    o.kapat()
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid


def mpv_liste_kodla(ogeler: list[str]) -> str:
    """
    mpv liste-tipi seçenekler (örn. http-header-fields) için METİN kodlar.

    NEDEN STRING, NEDEN JSON DİZİSİ DEĞİL: Önce IPC'nin JSON dizisini
    doğrudan kabul edeceği varsayılmıştı, ama googlevideo'ya istek
    hâlâ eksik başlıkla gidip 403 ALINMAYA DEVAM ETTİ — yani bu varsayım
    bu mpv sürümünde (0.41) güvenilir değil, başlıklar sessizce
    uygulanmıyordu. mpv'nin TÜM arayüzlerde (komut satırı, config,
    property-from-string) resmî olarak belgelenen garantili yöntemi:
    liste öğeleri VİRGÜLLE ayrılır; bir öğenin içindeki GERÇEK virgül
    ÇİFTLENEREK (",,") kaçırılır. Bu yöntem sürüm bağımsız çalışır.
    """
    return ",".join(o.replace(",", ",,") for o in ogeler)


# ══════════════════════════════════════════════════════════════════
#  yt-dlp DESTEĞİ (YouTube ve benzeri siteler)
# ══════════════════════════════════════════════════════════════════
# mpv normalde bu siteleri `ytdl_hook.lua` betiğiyle açar. Ancak bazı
# dağıtım paketlerinde bu betik yoktur (ölçüldü: /usr/share/mpv/scripts/
# boş) ve mpv YouTube adresini açamaz. O durumda yt-dlp'yi doğrudan biz
# çağırıp gerçek akış adresini alıyoruz.

_YTDL_SITELER = (
    "youtube.com", "youtu.be", "youtube-nocookie.com",
    "vimeo.com", "dailymotion.com", "twitch.tv",
    "soundcloud.com", "bitchute.com", "odysee.com", "rumble.com",
    "facebook.com/watch", "twitter.com", "x.com/i/status",
)

_YTDL_ONBELLEK: dict[str, tuple[float, dict]] = {}
_YTDL_ONBELLEK_SURE = 900.0        # çözülen adresler ~15 dk geçerli


_YTDL_YOL_ONBELLEK: str | None = None


def _ytdl_surum(yol: str) -> tuple:
    """yt-dlp sürümünü (2026, 7, 4) gibi karşılaştırılabilir demete çevirir."""
    try:
        s = subprocess.run([yol, "--version"], capture_output=True, text=True,
                           timeout=10).stdout.strip()
        return tuple(int(x) for x in s.split(".")[:3])
    except Exception:
        return (0,)


def ytdl_var_mi(hizli: bool = True) -> str:
    """
    Kullanılacak yt-dlp yolunu döndürür; yoksa ''.

    HIZ UYARISI (ölçüldü): Sürüm karşılaştırması her aday için bir
    `--version` süreci başlatır — 3 kopyada 0,72 sn, mpv yoklamasıyla
    birlikte `oynat()` 2,33 sn boyunca ARAYÜZÜ DONDURUYORDU.

    Bu yüzden:
      • `hizli=True` (varsayılan): anında `shutil.which` sonucunu ya da
        daha önce hesaplanmış en iyi yolu verir. Hiç süreç başlatmaz.
      • `hizli=False`: tüm kopyaları tarayıp EN GÜNCEL sürümü seçer.
        Yalnızca arka planda (`ytdl_tara`) çağrılır.
    """
    if _YTDL_YOL_ONBELLEK:
        return _YTDL_YOL_ONBELLEK
    if hizli:
        for ad in ("yt-dlp", "yt-dlp.exe", "youtube-dl", "youtube-dl.exe"):
            y = shutil.which(ad)
            if y:
                return y
        try:
            import yt_dlp  # noqa: F401
            return f"{sys.executable} -m yt_dlp"
        except Exception:
            return ""
    return ytdl_tara()


def ytdl_tara() -> str:
    """
    Tüm yt-dlp kopyalarını tarayıp EN GÜNCEL olanı seçer ve önbelleğe alır.

    NEDEN: Sistemde birden çok kurulum olabiliyor ve eski sürüm YouTube'da
    hata veriyor (ölçüldü: 2025.04.30 → "The page needs to be reloaded",
    2026.07.04 → çalışıyor). Bu tarama YAVAŞTIR; arka planda yapılmalı.
    """
    global _YTDL_YOL_ONBELLEK

    adaylar = []
    for ad in ("yt-dlp", "yt-dlp.exe", "youtube-dl", "youtube-dl.exe"):
        for dizin in (os.environ.get("PATH") or "").split(os.pathsep):
            if not dizin:
                continue
            y = os.path.join(dizin, ad)
            if os.path.isfile(y) and os.access(y, os.X_OK) and y not in adaylar:
                adaylar.append(y)
    try:
        import yt_dlp  # noqa: F401
        adaylar.append(f"{sys.executable} -m yt_dlp")
    except Exception:
        pass

    if not adaylar:
        _YTDL_YOL_ONBELLEK = ""
        return ""

    en_iyi, en_surum = "", (0,)
    for y in adaylar:
        if y.endswith("-m yt_dlp"):
            try:
                import yt_dlp
                s = tuple(int(x) for x in yt_dlp.version.__version__.split(".")[:3])
            except Exception:
                s = (0,)
        else:
            s = _ytdl_surum(y)
        if s > en_surum:
            en_iyi, en_surum = y, s
    _YTDL_YOL_ONBELLEK = en_iyi or adaylar[0]
    return _YTDL_YOL_ONBELLEK


def ytdl_tara_arkaplan():
    """
    Sürüm taramasını VE mpv hook yoklamasını arka planda yapar.

    İkisi de süreç başlatır (ölçüldü: tarama 0,72 sn + hook 0,17 sn).
    İlk oynatmada beklenmesin diye uygulama açılırken çağrılır.
    """
    def _isle():
        try:
            if not _YTDL_YOL_ONBELLEK:
                ytdl_tara()
        except Exception:
            pass
        try:
            mpv_ytdl_hook_var()          # sonucu önbelleğe alır
        except Exception:
            pass

    threading.Thread(target=_isle, daemon=True).start()


def ytdl_komutu() -> list:
    """`ytdl_var_mi()` sonucunu subprocess için listeye çevirir."""
    y = ytdl_var_mi()
    if not y:
        return []
    return y.split(" ") if " -m " in y else [y]


_YTDL_HOOK_DURUM: bool | None = None


def mpv_ytdl_hook_var() -> bool:
    """
    mpv kendi başına yt-dlp kullanabiliyor mu (ytdl_hook betiği yüklü mü)?

    DİKKAT — ilk denemede DOSYA ARADIM VE YANILDIM: modern mpv sürümleri
    ytdl_hook.lua'yı diske koymaz, doğrudan çalıştırılabilir dosyanın
    içine gömer. `/usr/share/mpv/scripts/` boş olduğu hâlde hook çalışıyordu
    (mpv günlüğünde: "[ytdl_hook] Loading lua script @ytdl_hook.lua").

    Bu yüzden dosya değil, mpv'nin kendi çıktısı ölçülüyor: `--vo=null`
    ile hiç pencere açmadan betik listesini yazdırıp içinde ytdl_hook
    aranıyor. Sonuç önbelleğe alınır (ölçüm ~0,3 sn sürer).
    """
    global _YTDL_HOOK_DURUM
    if _YTDL_HOOK_DURUM is not None:
        return _YTDL_HOOK_DURUM

    exe = shutil.which("mpv")
    if not exe:
        _YTDL_HOOK_DURUM = False
        return False
    try:
        r = subprocess.run(
            [exe, "--no-config", "--idle=no", "--frames=0", "--vo=null",
             "--ao=null", "--msg-level=all=debug", "av://lavfi:color=c=black:d=0.1"],
            capture_output=True, text=True, timeout=15)
        _YTDL_HOOK_DURUM = "ytdl_hook" in (r.stdout + r.stderr)
    except Exception:
        # Ölçemedik: dosya yolunu son çare olarak dene
        _YTDL_HOOK_DURUM = any(os.path.exists(p) for p in (
            "/usr/share/mpv/scripts/ytdl_hook.lua",
            "/usr/lib/mpv/scripts/ytdl_hook.lua",
            os.path.expanduser("~/.config/mpv/scripts/ytdl_hook.lua"),
        ))
    return _YTDL_HOOK_DURUM


def ytdl_gerekir(url: str) -> bool:
    """Bu adres için yt-dlp çözümlemesi gerekli mi?"""
    if not url:
        return False
    u = url.lower()
    if u.startswith(("ytdl://", "ytsearch:")):
        return True
    if not u.startswith(("http://", "https://")):
        return False
    # Doğrudan medya dosyası / akışı ise gerek yok
    if any(u.split("?")[0].endswith(x) for x in
           (".mp4", ".mkv", ".avi", ".mov", ".webm", ".m3u8", ".ts",
            ".mpd", ".flv", ".mp3", ".m4a", ".aac", ".ogg", ".wav")):
        return False
    return any(s in u for s in _YTDL_SITELER)


_TANINAN_MEDYA_UZANTI = (
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".m3u8", ".m3u", ".ts",
    ".mpd", ".flv", ".mp3", ".m4a", ".aac", ".ogg", ".wav", ".wmv", ".mpeg", ".mpg",
)


def gizli_hls_mi(url: str) -> bool:
    """
    Bu adres, gerçekte bir HLS (m3u8) akışı olduğu hâlde tanınmayan bir
    uzantıyla (ör. .txt, .php, uzantısız) servis ediliyor olabilir mi?

    NEDEN GEREKLİ: Bazı kaynaklar (özellikle "master.txt" gibi adlandırılmış
    CDN adresleri) HLS master playlist'i .m3u8 yerine .txt uzantısıyla verir.
    mpv/ffmpeg format algılaması bu durumda içerik imzasına ("#EXTM3U")
    her zaman güvenmeyip "Failed to recognize file format" ile
    başarısız olabiliyor — VLC'nin daha toleranslı davranmasının sebebi bu.
    Uzantı .m3u8/.m3u olsaydı zaten sorun çıkmazdı; burada yalnızca
    TANINMAYAN bir uzantı + tipik HLS ipuçları (yol içinde "hls" geçmesi,
    dosya adının "master"/"playlist" olması, ya da hiç uzantı olmaması)
    bir arada olduğunda devreye girer.
    """
    if not url:
        return False
    u = url.split("?")[0].split("#")[0].lower()
    if not u.startswith(("http://", "https://")):
        return False
    if any(u.endswith(x) for x in _TANINAN_MEDYA_UZANTI):
        return False                      # zaten tanınan bir uzantı var
    dosya_adi = u.rsplit("/", 1)[-1]
    kok = dosya_adi.rsplit(".", 1)[0] if "." in dosya_adi else dosya_adi
    ipucu = ("/hls/" in u or "/hls" in u.rsplit("/", 1)[0]
             or kok in ("master", "playlist", "index", "chunklist")
             or dosya_adi == "")
    return ipucu


def ytdl_coz(url: str, zaman_asimi: int = 45, kalite: str = "") -> tuple[dict | None, str]:
    """
    yt-dlp ile gerçek akış adresini bulur.

    Dönüş: (sözlük | None, hata_mesajı)
      sözlük: {"url":…, "ses_url":…, "baslik":…, "sure":…, "referer":…}
    """
    exe = ytdl_var_mi()
    if not exe:
        return None, ("yt-dlp bulunamadı.\n\n"
                      "CachyOS / Arch:   sudo pacman -S yt-dlp\n"
                      "Debian / Ubuntu:  sudo apt install yt-dlp\n"
                      "Evrensel:         pip install -U yt-dlp")

    simdi = time.time()
    onb = _YTDL_ONBELLEK.get(url)
    if onb and simdi - onb[0] < _YTDL_ONBELLEK_SURE:
        return onb[1], ""

    # YouTube artık neredeyse hiç birleşik (muxed) akış vermiyor.
    # "best[...][acodec!=none]" tercihleri sessiz video-only seçimine
    # yol açıyordu. Önce video+ses ayrı birleşimi zorunlu kıl; H.264 tercih.
    bicim = kalite or (
        "bestvideo[height<=1080][vcodec^=avc]+bestaudio/"
        "bestvideo[height<=1080][vcodec!*=av01]+bestaudio/"
        "bestvideo[height<=1080]+bestaudio/"
        "best[height<=1080]/best")
    temel = ytdl_komutu() + ["--no-warnings", "--no-playlist", "-J", "-f", bicim]
    try:
        r = subprocess.run(temel + [url], capture_output=True, text=True,
                           timeout=zaman_asimi)
    except subprocess.TimeoutExpired:
        return None, f"yt-dlp {zaman_asimi} sn içinde yanıt vermedi"
    except Exception as e:
        return None, f"yt-dlp çalıştırılamadı: {e}"

    veri = None
    if r.returncode == 0 and r.stdout.strip():
        try:
            veri = json.loads(r.stdout)
        except Exception:
            veri = None

    if veri is None:
        # Birleşik biçim yoksa video+ses ayrı olabilir (YouTube'da sık).
        try:
            r2 = subprocess.run(
                ytdl_komutu() + ["--no-warnings", "--no-playlist", "-J",
                 "-f", "bestvideo[height<=1080]+bestaudio/best[acodec!=none]/best", url],
                capture_output=True, text=True, timeout=zaman_asimi)
            if r2.returncode == 0 and r2.stdout.strip():
                veri = json.loads(r2.stdout)
            else:
                hata = (r2.stderr or r.stderr or "").strip().splitlines()
                son = hata[-1] if hata else "bilinmeyen hata"
                return None, f"yt-dlp çözemedi: {son[:200]}"
        except Exception as e:
            return None, f"yt-dlp hatası: {e}"

    sonuc = _ytdl_veriden_url(veri)
    if not sonuc:
        return None, "yt-dlp yanıtında oynatılabilir akış bulunamadı"
    _YTDL_ONBELLEK[url] = (simdi, sonuc)
    return sonuc, ""


def _ytdl_veriden_url(veri: dict) -> dict | None:
    """yt-dlp JSON çıktısından oynatılacak adres(ler)i seçer."""
    if not isinstance(veri, dict):
        return None
    if veri.get("_type") == "playlist" and veri.get("entries"):
        veri = veri["entries"][0]

    baslik = veri.get("title") or ""
    sure = veri.get("duration") or 0
    ref = veri.get("webpage_url") or ""

    def _bsl(b):
        """
        yt-dlp'nin verdiği HTTP başlıkları.

        ÖNEMLİ: Bazı CDN'ler (YouTube dahil) akış adresini isteyen istemcinin
        başlıklarına bakar. Bunlar aktarılmazsa mpv "HTTP error 403 Forbidden"
        alır — ölçüldü: aynı adres Python'dan doğru başlıkla HTTP 206 verirken
        mpv'den 403 dönüyordu.
        """
        h = dict((b or {}).get("http_headers") or {})
        h.pop("Accept-Encoding", None)      # mpv kendi yönetir
        return h

    dogrudan = veri.get("url")
    # DİKKAT: yt-dlp tek bir format seçildiğinde bunu "birleşik" (ses+
    # görüntü) sanıp doğrudan kabul ediyorduk — ama YouTube artık çoğu
    # kalitede SADECE GÖRÜNTÜ içeren tek akışları da bu şekilde
    # döndürebiliyor (progressive/muxed formatları büyük ölçüde kaldırdı).
    # Ölçülen gerçek şikayet: "görüntü var, ses yok". Bu yüzden acodec
    # gerçekten "none" DEĞİLSE bu kısayolu kullan; aksi hâlde aşağıdaki
    # requested_formats/formats mantığına düşüp GERÇEK bir ses akışı ara.
    if dogrudan and (veri.get("acodec") or "none") != "none":
        return {"url": dogrudan, "ses_url": "", "baslik": baslik,
                "sure": sure, "referer": ref, "basliklar": _bsl(veri)}

    bicimler = veri.get("requested_formats") or []
    if bicimler:
        video = ses = ""
        for b in bicimler:
            if not b.get("url"):
                continue
            if b.get("vcodec") and b["vcodec"] != "none" and not video:
                video = b["url"]
            elif b.get("acodec") and b["acodec"] != "none" and not ses:
                ses = b["url"]
        if video:
            return {"url": video, "ses_url": ses, "baslik": baslik,
                    "sure": sure, "referer": ref,
                    "basliklar": _bsl(bicimler[0])}
        if ses:
            return {"url": ses, "ses_url": "", "baslik": baslik,
                    "sure": sure, "referer": ref,
                    "basliklar": _bsl(bicimler[0])}

    for b in reversed(veri.get("formats") or []):
        if b.get("url") and b.get("vcodec") not in (None, "none"):
            return {"url": b["url"], "ses_url": "", "baslik": baslik,
                    "sure": sure, "referer": ref, "basliklar": _bsl(b)}
    return None


class MpvBulunamadi(RuntimeError):
    pass


class MpvIslem:
    """mpv'yi dış işlem olarak yönetir (JSON IPC)."""

    def __init__(self, log_yolu: str | None = None):
        self.surec: subprocess.Popen | None = None
        self.sok_yolu: str = ""
        self._sok: socket.socket | None = None
        self._id = 0
        self._kilit = threading.Lock()
        self._olaylar: list[dict] = []
        self._yanitlar: dict[int, object] = {}      # request_id -> veri
        self._bekleyenler: dict[int, threading.Event] = {}
        self._dinleyici: threading.Thread | None = None
        self._calisiyor = False
        self._tampon = b""
        self._son_hata = ""
        self.gomme_notu = ""        # gömme başarısız olup ayrı pencereye geçildiyse
        self._stderr_satirlari: list[str] = []
        self._stderr_th: threading.Thread | None = None
        self.log_yolu = log_yolu
        self.gomulu = False

    # ── yardımcılar ────────────────────────────────────────────────
    @staticmethod
    def mpv_var_mi() -> str | None:
        return shutil.which("mpv")

    @property
    def yasiyor(self) -> bool:
        return self.surec is not None and self.surec.poll() is None

    @property
    def son_hata(self) -> str:
        return self._son_hata

    @staticmethod
    def _vo_argumanlari(secim: str, wid) -> list:
        """
        Video çıkış ayarları.

        SİYAH EKRAN NOTU
            Wayland oturumunda pencereye gömme (--wid) XWayland üzerinden
            olur. mpv varsayılan GPU bağlamıyla bu pencereye çizemeyip
            SİYAH kare bırakabilir. 'x11egl' bağlamı bu durumu genellikle
            çözer; olmazsa 'x11' (yazılım) her yerde çizer ama daha yavaştır.
        """
        wayland = bool(os.environ.get("WAYLAND_DISPLAY")) or \
                  os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        if secim == "gpu":
            return ["--vo=gpu"]
        if secim == "x11egl":
            return ["--vo=gpu", "--gpu-context=x11egl"]
        if secim == "x11":
            return ["--vo=x11"]
        if secim == "xv":
            return ["--vo=xv"]
        if secim == "gpu-next":
            return ["--vo=gpu-next"]
        # oto
        if wid and wayland:
            # XWayland'e gömülüyoruz: en uyumlu bağlam
            return ["--vo=gpu", "--gpu-context=x11egl"]
        return []          # mpv kendi seçsin

    @staticmethod
    def _yetim_soketleri_temizle():
        """
        Önceki çalıştırmalardan kalan soket dosyalarını siler.
        mpv zorla öldürülürse (SIGKILL) soketi arkada bırakır; birikmesinler.
        """
        try:
            gecici = tempfile.gettempdir()
            for ad in os.listdir(gecici):
                if not ad.startswith("mediabox-mpv-"):
                    continue
                yol = os.path.join(gecici, ad)
                try:
                    # Dosya adındaki PID hâlâ yaşıyorsa dokunma
                    parca = ad.split("-")
                    pid = int(parca[2]) if len(parca) > 2 else 0
                    if pid:
                        try:
                            os.kill(pid, 0)
                            continue            # süreç yaşıyor → bizim değil
                        except (ProcessLookupError, PermissionError):
                            pass
                    os.unlink(yol)
                except (OSError, ValueError):
                    pass
        except Exception:
            pass

    # ── başlatma ───────────────────────────────────────────────────
    def baslat(self, wid: int | None = None, hwdec: bool = True,
               video_cikis: str = "oto",
               ek_argumanlar: list[str] | None = None) -> bool:
        """
        mpv'yi başlatır. wid verilirse pencereye gömülmeye çalışır.
        Dönüş: True = başladı.
        """
        if self.yasiyor:
            return True

        if wid:
            self.gomme_notu = ""
        exe = self.mpv_var_mi()
        if not exe:
            raise MpvBulunamadi(
                "mpv bulunamadı.\n\n"
                "CachyOS / Arch:   sudo pacman -S mpv\n"
                "Debian / Ubuntu:  sudo apt install mpv")

        self._yetim_soketleri_temizle()
        self.sok_yolu = os.path.join(
            tempfile.gettempdir(), f"mediabox-mpv-{os.getpid()}-{uuid.uuid4().hex[:6]}.sock")
        try:
            if os.path.exists(self.sok_yolu):
                os.unlink(self.sok_yolu)
        except OSError:
            pass

        arg = [
            exe,
            f"--input-ipc-server={self.sok_yolu}",
            "--idle=yes",
            "--force-window=yes",
            "--keep-open=yes",
            "--no-osc",                    # kontroller MediaBox'ta
            "--no-input-default-bindings",
            "--no-terminal",
            f"--hwdec={'auto-safe' if hwdec else 'no'}",
            *self._vo_argumanlari(video_cikis, wid),
            # ── A/V senkron & ağ kararlılığı ─────────────────────────
            # video-sync=audio: görüntü saati sese kilitlenir; IPTV /
            # HLS / uzun yayınlarda zamanla açılan kaymayı keser.
            "--video-sync=audio",
            "--initial-audio-sync=yes",
            "--audio-buffer=2.0",
            # Ağ akışları için daha geniş önbellek (jitter / kısa kopma)
            "--cache=yes",
            "--cache-secs=30",
            "--demuxer-max-bytes=150MiB",
            "--demuxer-max-back-bytes=50MiB",
            "--demuxer-readahead-secs=20",
            "--cache-pause=yes",
            "--cache-pause-initial=yes",
            "--network-timeout=30",
            # ── CANLI YAYIN DONMASI DÜZELTMESİ ──────────────────────
            # IPTV akışlarında ağ kısa kesildiğinde (CDN yeniden yönlendirme,
            # geçici paket kaybı, sunucu tarafı bağlantı sıfırlama/hata)
            # ffmpeg'in VARSAYILANI soketi "sessizce" askıda bırakabiliyor.
            # Bu seçenekler HATA durumunda otomatik yeniden bağlanmayı sağlar.
            #
            # ÖNEMLİ — GERİ ALINAN AYAR: Buraya daha önce `reconnect_at_eof=1`
            # de eklenmişti ("dosya normal bittiğinde bile yeniden bağlan").
            # Bu, RAW/sürekli akışlarda işe yarasa da HLS (.m3u8) alt liste
            # dosyalarını KIRIYORDU: bir alt playlist (ör. audio_tur.m3u8)
            # tamamen ve normal şekilde indirildiğinde bile ffmpeg bunu
            # "hata" sayıp aynı dosyayı sonsuza kadar yeniden indirmeye
            # çalışıyor, gerçek video segmentlerine hiç geçemiyordu (ölçülen
            # belirti: mpv.log'da aynı bayt sayısında sonsuz
            # "Will reconnect ... error=End of file" döngüsü, oynatıcı
            # "Bağlanılıyor…" durumunda sonsuza kadar kilitleniyordu — HEM
            # canlı kanallarda HEM sıradan film/dizi HLS linklerinde). Bu
            # yüzden `reconnect_at_eof` kaldırıldı; yalnızca GERÇEK ağ
            # hatalarında (bağlantı kopması, zaman aşımı) yeniden bağlanan
            # aşağıdaki seçenekler kalıyor.
            "--demuxer-lavf-o=reconnect=1,reconnect_streamed=1,"
            "reconnect_delay_max=5,reconnect_on_network_error=1,"
            "http_persistent=0",
            # Geç arama sonrası senkronu bozmasın
            "--hr-seek=absolute",
            "--hr-seek-framedrop=no",
            "--user-agent=Mozilla/5.0 MediaBox/1.0",
            "--title=MediaBox — Oynatıcı",
        ]
        if self.log_yolu:
            arg.append(f"--log-file={self.log_yolu}")
        if wid:
            arg.append(f"--wid={int(wid)}")
            self.gomulu = True
        else:
            self.gomulu = False
        if ek_argumanlar:
            arg.extend(ek_argumanlar)

        try:
            self.surec = subprocess.Popen(
                arg, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                start_new_session=True)
        except Exception as e:
            self._son_hata = f"mpv başlatılamadı: {e}"
            return False

        if not self._sokete_baglan():
            # Gömme başarısızsa bir kez de gömmeden dene
            hata = self._surec_hatasi()
            self.durdur()
            if wid:
                self._son_hata = (hata or "Gömülü pencere açılamadı") + \
                                 "  → ayrı pencere deneniyor"
                return self.baslat(wid=None, hwdec=hwdec, ek_argumanlar=ek_argumanlar)
            self._son_hata = hata or "mpv IPC soketine bağlanılamadı"
            return False

        # stderr toplayıcı (mpv neden öldü sorusunun cevabı burada)
        self._stderr_th = threading.Thread(target=self._stderr_oku, daemon=True)
        self._stderr_th.start()

        # ── KRİTİK: mpv ÖLÜMÜ GECİKMELİ GELİR ─────────────────────────
        # Ölçüldü: geçersiz/uyumsuz --wid ile mpv soket bağlanıp ~0.4 sn
        # SONRA "BadWindow (invalid Window parameter)" ile ölüyor.
        # Bu yüzden yalnızca "soket bağlandı mı" bakmak yetmez; kısa bir
        # hayatta kalma süresi beklenip gerçekten ayakta mı diye bakılır.
        t0 = time.time()
        while time.time() - t0 < 1.2:
            if self.surec.poll() is not None:
                hata = self._surec_hatasi()
                self.durdur()
                if wid:
                    # Gömme başarısız → ayrı pencerede yeniden dene
                    self._son_hata = (hata or "Pencereye gömülemedi")
                    sonuc = self.baslat(wid=None, hwdec=hwdec,
                                        video_cikis=video_cikis,
                                        ek_argumanlar=ek_argumanlar)
                    if sonuc:
                        # Yedek plan TUTTU: bu bir hata değil, bilgi notudur.
                        # son_hata'da bırakılırsa sonraki kontroller bunu
                        # "oynatıcı çöktü" sanıp kullanıcıyı korkutuyordu.
                        self.gomme_notu = (hata or "Pencereye gömülemedi")
                        self._son_hata = ""
                    return sonuc
                self._son_hata = hata or "mpv beklenmedik şekilde kapandı"
                return False
            time.sleep(0.05)

        self._calisiyor = True
        self._dinleyici = threading.Thread(target=self._dinle, daemon=True)
        self._dinleyici.start()
        # HATA GÜNLÜĞÜ İÇİN DÜZELTME: `_mpv_log` (arayuz.py) tanımlıydı ama
        # hiçbir yerde mpv'den "log-message" olayı İSTENMİYORDU — bu yüzden
        # mpv-hata.log hep boş kalıyor, gerçek hatalar (403, decode hatası
        # vb.) hiç görünmüyordu (ölçüldü: googlevideo 403 alındı ama günlük
        # dosyası "henüz hata oluşmamış" diyordu). IPC üzerinden log
        # mesajlarına da abone oluyoruz; "warn" seviyesi 403 gibi CDN
        # reddetmelerini de yakalar.
        try:
            self.komut("request_log_messages", "warn", bekle=False)
        except Exception:
            pass
        # Olayları izlemeye başla.
        # track-list/sid/aid/sub-visibility de İZLENMELİ: bunlar olmadan
        # arayüzdeki ses/altyazı kutuları mpv'nin seçimini öğrenemiyordu
        # (kutu "Kapalı" gösterirken altyazı açık kalıyordu).
        for no, ad in enumerate((
                "pause", "eof-reached", "duration", "time-pos", "core-idle",
                "track-list", "sid", "aid", "sub-visibility"), start=1):
            try:
                self.komut("observe_property", no, ad, bekle=False)
            except Exception:
                pass
        return True

    def _stderr_oku(self):
        """mpv'nin hata çıktısını arka planda toplar (tanı için)."""
        sr = self.surec.stderr if self.surec else None
        if not sr:
            return
        try:
            for ham in iter(sr.readline, b""):
                satir = ham.decode("utf-8", "ignore").rstrip()
                if satir:
                    self._stderr_satirlari.append(satir)
                    del self._stderr_satirlari[80:]
        except Exception:
            pass

    def _surec_hatasi(self) -> str:
        """Toplanan stderr'den anlamlı bir özet döndürür."""
        onemli = [x for x in self._stderr_satirlari
                  if any(k in x for k in ("Error", "error", "Failed", "failed",
                                          "Cannot", "BadWindow", "not found"))]
        kaynak = onemli or self._stderr_satirlari
        return " | ".join(kaynak[-4:])[:400]

    def _sokete_baglan(self, zaman_asimi: float = 8.0) -> bool:
        t0 = time.time()
        while time.time() - t0 < zaman_asimi:
            if self.surec and self.surec.poll() is not None:
                return False                     # mpv daha açılırken öldü
            if os.path.exists(self.sok_yolu):
                try:
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    s.settimeout(2.0)
                    s.connect(self.sok_yolu)
                    self._sok = s
                    return True
                except OSError:
                    pass
            time.sleep(0.08)
        return False

    # ── iletişim ───────────────────────────────────────────────────
    def _dinle(self):
        """
        Soketin TEK okuyucusu. Olayları biriktirir, komut yanıtlarını
        bekleyen çağrılara dağıtır.

        NOT: Bu iş parçacığı Qt nesnelerine DOKUNMAZ; yalnızca veri toplar.
        Yanıtları burada dağıtmak şart — daha önce komut() de aynı soketten
        okuyordu ve ikisi birbirinin yanıtını çalıp her komut zaman aşımına
        düşüyordu (arayüz saniyelerce donuyordu).
        """
        while self._calisiyor and self._sok:
            try:
                self._sok.settimeout(0.4)
                veri = self._sok.recv(65536)
                if not veri:
                    break
                self._tampon += veri
                *satirlar, self._tampon = self._tampon.split(b"\n")
                for satir in satirlar:
                    if not satir.strip():
                        continue
                    try:
                        y = json.loads(satir)
                    except Exception:
                        continue
                    if "event" in y:
                        self._olaylar.append(y)
                        del self._olaylar[200:]
                    elif "request_id" in y:
                        rid = y.get("request_id")
                        self._yanitlar[rid] = y.get("data")
                        ev = self._bekleyenler.get(rid)
                        if ev:
                            ev.set()
            except socket.timeout:
                continue
            except (OSError, BrokenPipeError):
                break
        self._calisiyor = False
        # Bekleyen herkesi serbest bırak (asılı kalmasınlar)
        for ev in list(self._bekleyenler.values()):
            ev.set()

    def komut(self, *args, bekle: bool = True, zaman_asimi: float = 2.0):
        """
        mpv'ye komut gönderir. Yanıtı dinleyici iş parçacığı dağıtır.
        Hata durumunda None döner, ASLA istisna fırlatmaz.
        """
        if not self._sok or not self.yasiyor:
            return None
        with self._kilit:
            self._id += 1
            rid = self._id
            paket = json.dumps({"command": list(args), "request_id": rid}) + "\n"
            if bekle:
                self._bekleyenler[rid] = threading.Event()
            try:
                self._sok.sendall(paket.encode("utf-8"))
            except (OSError, BrokenPipeError) as e:
                self._son_hata = f"IPC yazma hatası: {e}"
                self._bekleyenler.pop(rid, None)
                return None
        if not bekle:
            return None
        ev = self._bekleyenler.get(rid)
        if ev is not None:
            ev.wait(zaman_asimi)
        self._bekleyenler.pop(rid, None)
        return self._yanitlar.pop(rid, None)

    def ozellik(self, ad: str, varsayilan=None):
        d = self.komut("get_property", ad)
        return varsayilan if d is None else d

    def ozellik_yaz(self, ad: str, deger) -> None:
        self.komut("set_property", ad, deger, bekle=False)

    def olaylari_al(self) -> list[dict]:
        """Biriken olayları döndürür ve listeyi boşaltır."""
        o, self._olaylar = self._olaylar, []
        return o

    # ── oynatma ────────────────────────────────────────────────────
    def oynat(self, url: str, ek_secenek: dict | None = None):
        # ÖNEMLİ: mpv TEK bir süreç olarak açık kalıyor; bir önceki
        # oynatmada set edilen özellikler (ör. "gizli HLS" tespiti için
        # zorlanan demuxer=lavf/hls) SIFIRLANMADIĞI sürece sonraki HİÇ
        # İLGİSİ OLMAYAN bir içerikte de geçerli kalıyor. Ölçülen gerçek
        # sonuç: bir .txt HLS linki oynattıktan sonra bir YouTube videosu
        # açılınca görüntü geliyor ama SES GELMİYORDU — çünkü mpv hâlâ
        # önceki linkten kalma "HLS gibi aç" zorlamasıyla çalışıyordu.
        # Bu yüzden bu iki özellik HER oynatmada AÇIKÇA sıfırlanır, sonra
        # yalnızca gerçekten gerekiyorsa ek_secenek onları tekrar zorlar.
        self.ozellik_yaz("demuxer", "")
        self.ozellik_yaz("demuxer-lavf-format", "")
        # Her yeni dosyada senkron politikasını taze tut
        try:
            self.ozellik_yaz("video-sync", "audio")
            self.ozellik_yaz("speed", 1.0)
        except Exception:
            pass

        # Ayrı ses URL'si: loadfile options sözlüğü bazı mpv sürümlerinde
        # yüklemeyi bozup SİYAH EKRAN bırakıyordu. Güvenli yol:
        #   1) video'yu normal loadfile ile aç
        #   2) ses varsa audio-add ile ekle
        ses_url = ""
        ek = dict(ek_secenek or {})
        if "audio-files" in ek:
            ses_url = ek.pop("audio-files") or ""
            if isinstance(ses_url, (list, tuple)):
                ses_url = ses_url[0] if ses_url else ""

        for k, v in ek.items():
            self.ozellik_yaz(k, v)

        self.komut("loadfile", url, "replace", bekle=False)
        self.ozellik_yaz("pause", False)

        if ses_url:
            # video yüklendikten sonra harici ses izi ekle
            try:
                self.komut("audio-add", ses_url, "select", bekle=False)
            except Exception:
                # yedek: property listesine yaz
                try:
                    self.ozellik_yaz("audio-files", ses_url)
                except Exception:
                    pass

    def oynat_cozumlu(self, url: str, ek_secenek: dict | None = None,
                      zaman_asimi: int = 45):
        """
        Gerekiyorsa adresi önce yt-dlp ile çözer, sonra oynatır.

        NEDEN: mpv paketlerinin bir kısmında `ytdl_hook.lua` betiği yoktur
        (ölçüldü: /usr/share/mpv/scripts/ boş → mpv YouTube adresini
        "Failed to recognize file format" diyerek açamıyor). Bu durumda
        yt-dlp'yi kendimiz çağırıp doğrudan akış adresini alıyoruz.

        Dönüş: (başarılı_mı, mesaj)
        """
        if not ytdl_gerekir(url):
            self.oynat(url, ek_secenek)
            return True, ""

        if mpv_ytdl_hook_var():
            # mpv kendi çözüyor — AMA hangi formatı seçeceğini mpv'nin
            # kendi varsayılanına bırakırsak YouTube'un artık çoğu
            # kalitede sunmadığı "birleşik" (ses+görüntü) akışlar yerine
            # SESSİZ bir görüntü akışı seçebiliyor (ölçülen şikayet:
            # "görüntü var, ses yok" — Python tarafındaki eşdeğer düzeltme
            # bu koşulda hiç ÇALIŞMIYORDU, çünkü mpv kendi çözünce bu dal
            # hiç ytdl_coz()'a uğramıyor). Çözüm: mpv'nin ytdl_hook betiği
            # `ytdl-format` özelliğini okur; burada AÇIKÇA "sesi kesin olan"
            # bir format zinciri veriyoruz — Python tarafındaki düzeltmeyle
            # birebir aynı mantık, ama mpv'nin kendi yoluna uygulanmış hâli.
            secenek = dict(ek_secenek or {})
            # YouTube birleşik akışları kaldırdı; [acodec!=none] tercihleri
            # sessiz video-only seçimine düşüyordu (ölçüldü: görüntü var,
            # ses yok). bestvideo+bestaudio her zaman sesi garantiler.
            secenek.setdefault("ytdl-format",
                "bestvideo[height<=1080][vcodec^=avc]+bestaudio/"
                "bestvideo[height<=1080][vcodec!*=av01]+bestaudio/"
                "bestvideo[height<=1080]+bestaudio/"
                "best[height<=1080]/best")
            self.oynat(url, secenek)
            return True, "mpv yt-dlp ile çözüyor"

        cozum, hata = ytdl_coz(url, zaman_asimi=zaman_asimi)
        if not cozum:
            return False, hata or "Adres çözümlenemedi"

        secenek = dict(ek_secenek or {})
        # Bkz. arayuz.py _ytdl_geldi(): YouTube VP9/AV1 + hwdec → siyah ekran.
        secenek.setdefault("hwdec", "no")
        if cozum.get("baslik"):
            secenek.setdefault("force-media-title", cozum["baslik"])
        if cozum.get("ses_url"):
            secenek["audio-files"] = cozum["ses_url"]
        if cozum.get("referer"):
            secenek.setdefault("referrer", cozum["referer"])
        bsl = dict(cozum.get("basliklar") or {})
        if bsl:
            ua = bsl.pop("User-Agent", "")
            if ua:
                secenek.setdefault("user-agent", ua)
            baslik_listesi = [f"{k}: {v}" for k, v in bsl.items() if k and v]
            if baslik_listesi:
                secenek["http-header-fields"] = mpv_liste_kodla(baslik_listesi)
        # self.oynat(): demuxer sıfırlama + loadfile options (ses dahil)
        self.oynat(cozum["url"], secenek)
        return True, cozum.get("baslik", "")

    def durdur(self):
        self._calisiyor = False
        try:
            if self._sok:
                self._sok.close()
        except Exception:
            pass
        self._sok = None
        if self.surec:
            try:
                self.komut("quit", bekle=False)
            except Exception:
                pass
            try:
                self.surec.terminate()
                self.surec.wait(timeout=2)
            except Exception:
                try:
                    self.surec.kill()
                except Exception:
                    pass
        self.surec = None
        # Soketi kesin sil (mpv SIGKILL ile ölmüşse kendisi silemez)
        for _ in range(3):
            try:
                if self.sok_yolu and os.path.exists(self.sok_yolu):
                    os.unlink(self.sok_yolu)
                break
            except OSError:
                time.sleep(0.05)
        self._yetim_soketleri_temizle()

    kapat = durdur

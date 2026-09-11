#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MediaBox Qt — Masaüstü Medya Merkezi
=====================================
HTML ile hiçbir ilgisi yok. Saf Python + PyQt6 + gömülü mpv.

NEDEN mpv GÖMÜLÜ?
    Tarayıcı yalnızca kendi desteklediği codec'leri oynatır; birçok M3U
    yayını (MPEG-TS, RTMP, RTSP, SRT, UDP, bazı HLS türevleri) tarayıcıda
    açılmaz. mpv altta FFmpeg kullandığı için bu kısıt yoktur.
    Ölçüldü — desteklenen protokoller:
        file, http, https, rtmp, rtsp, srt, udp, data  (+ dahili ffmpeg codec'leri)

KURULUM (CachyOS / Arch)
    sudo pacman -S python-pyqt6 mpv python-requests
    python mediabox_qt.py

    Debian/Ubuntu:
    sudo apt install python3-pyqt6 mpv python3-requests

NOT: python-mpv ARTIK GEREKMİYOR. mpv ayrı bir işlem olarak çalıştırılıyor;
     böylece mpv çökse bile uygulama kapanmıyor.

ÇALIŞTIRMA
    python mediabox_qt.py            # normal
    python mediabox_qt.py --check    # ortam kontrolü
    python mediabox_qt.py --ice-aktar mediabox_verisi.json
"""

from __future__ import annotations

# ── locale: libmpv'den ÖNCE ayarlanmalı ───────────────────────────
# Qt bazı dillerde ondalık ayıracını ',' yapar; libmpv bundan bozulur.
import locale
try:
    locale.setlocale(locale.LC_NUMERIC, "C")
except Exception:
    pass

import argparse
import hashlib
import json
import os
import re

# ── Wayland'de gömülü mpv için: Qt'yi XWayland (xcb) üzerinden başlat ──
# Wayland'de PyQt6'nın native Wayland eklentisi mpv'ye geçerli bir X11
# pencere kimliği (--wid) veremiyor; bu yüzden video her zaman ayrı bir
# pencerede açılıyordu. QT_QPA_PLATFORM=xcb ile Qt, XWayland üzerinden
# X11 penceresi gibi çalışır ve winId() mpv için kullanılabilir hâle gelir.
# Kullanıcı zaten X11 oturumundaysa bu satırın hiçbir etkisi yoktur.
if "WAYLAND_DISPLAY" in os.environ and "QT_QPA_PLATFORM" not in os.environ:
    os.environ["QT_QPA_PLATFORM"] = "xcb"
import subprocess
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

APP_ADI = "MediaBox"
APP_SURUM = "2.4"
TMDB_ANAHTAR_VARSAYILAN = "a7403e3d62a41bf85e1e53b9df4d684f"

# ══════════════════════════════════════════════════════════════════
#  VERİ KLASÖRÜ
# ══════════════════════════════════════════════════════════════════
def veri_klasoru() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    p = Path(base) / "mediabox-qt"
    p.mkdir(parents=True, exist_ok=True)
    return p

def afis_klasoru() -> Path:
    p = veri_klasoru() / "afisler"
    p.mkdir(parents=True, exist_ok=True)
    return p

DB_YOLU = veri_klasoru() / "mediabox.json"


# ══════════════════════════════════════════════════════════════════
#  TÜRKÇE METİN YARDIMCILARI
#  'İ'.lower() Python'da 'i̇' (birleşik nokta) verir ve 'i' ile eşleşmez.
#  Arama bu yüzden tek geçişli özel bir dönüşüm kullanır.
# ══════════════════════════════════════════════════════════════════
_TR_HARITA = str.maketrans({
    "İ": "i", "I": "ı", "Ş": "ş", "Ğ": "ğ", "Ü": "ü", "Ö": "ö", "Ç": "ç",
    "Â": "a", "Î": "i", "Û": "u",
})

def tr_kucuk(s: str) -> str:
    if not s:
        return ""
    return s.translate(_TR_HARITA).lower()

def tr_sadelestir(s: str) -> str:
    """Arama için: aksanları kaldır, küçült (ı/i farkını da yok say)."""
    s = tr_kucuk(s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.replace("ı", "i")


# ══════════════════════════════════════════════════════════════════
#  M3U AYRIŞTIRICI
#  HTML sürümünde düzeltilen tuzaklar burada baştan doğru yapıldı:
#   • group-title="Love, Death & Robots" gibi TIRNAK İÇİ virgüller
#   • #EXTVLCOPT satırları (user-agent/referrer) korunur
#   • tvg-id, catchup, tvg-shift gibi alanlar kaybolmaz
# ══════════════════════════════════════════════════════════════════
_ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
_SURE_RE = re.compile(r"#EXTINF:\s*(-?\d+(?:\.\d+)?)")
_VLC_RE = re.compile(r"^#EXTVLCOPT:", re.I)

def extinf_ad(satir: str) -> str:
    """Tırnak dışındaki İLK virgülden sonrası içerik adıdır."""
    tirnak = False
    for i, ch in enumerate(satir):
        if ch == '"':
            tirnak = not tirnak
        elif ch == "," and not tirnak:
            return satir[i + 1:].strip()
    return satir.split(",")[-1].strip()

_SE_RE = re.compile(r"\bS(\d{1,2})[\s._-]*E(\d{1,3})\b", re.I)

# Türkçe yayınlarda çok yaygın olan "12. Bölüm" / "12.Bolum" / "Bölüm 12"
# biçimi. Bu desen tanınmadığı için her bölüm AYRI KART oluyordu
# (ölçüldü: "Kızılcık Şerbeti 12. Bölüm" → 12 dizi yerine 12 film kartı).
# Sezon bilgisi yoksa 1. sezon varsayılır.
_TR_BOLUM_RE = re.compile(
    r"(?:\b(\d{1,2})\s*[\.\-]?\s*(?:sezon|se[zs])\b[\s._-]*)?"      # isteğe bağlı sezon
    r"\b(\d{1,3})\s*[\.\-]?\s*b[öo]l[üu]m\b",
    re.I)
# "Bölüm 12" (sayı sonda) biçimi
_TR_BOLUM_RE2 = re.compile(r"\bb[öo]l[üu]m\s*[:\-]?\s*(\d{1,3})\b", re.I)
_YIL_RE = re.compile(r"\b(19|20)\d{2}\b")
_KALITE_RE = re.compile(r"\b(2160p|4K|UHD|1080p|720p|480p|BluRay|WEB-?DL|HDTV|HDR)\b", re.I)
_DUB_RE = re.compile(r"\b(TR\s*Dublaj|Dublaj|T[üu]rk[çc]e)\b", re.I)


@dataclass
class Icerik:
    ad: str = "?"
    url: str = ""
    logo: str = ""
    grup: str = ""
    kategori: str = "movie"     # movie | series | anime | live
    kaynak: str = ""
    tvg_id: str = ""
    attrs: dict = field(default_factory=dict)
    vlcopt: list = field(default_factory=list)
    sure: str = ""
    tmdb_id: int = 0
    puan: float = 0.0
    ozet: str = ""
    afis: str = ""              # TMDB afiş yolu
    tarayici: bool = False      # True ise mpv yerine tarayıcıda açılır
                                # (sayfa içine gömülü/embed oynatıcılar için)
    alternatifler: list = field(default_factory=list)
    eklenme: float = 0.0        # time.time() — listeye eklenme anı (sıralama)
    # Önbellek alanları (bkz. _onbellek_tazele). repr/karşılaştırmaya girmez.
    _ob_imza: object = field(default=None, repr=False, compare=False)
    _ob_esl: object = field(default=None, repr=False, compare=False)
    _ob_sb: object = field(default=None, repr=False, compare=False)
    _ob_anahtar: object = field(default=None, repr=False, compare=False)
    _ob_kok: object = field(default=None, repr=False, compare=False)
                                # Aynı (sezon,bölüm) için diğer kaynaklar
                                # (bkz. _bolumleri_birlestir)

    # ── türetilmiş bilgiler ──
    def yil(self) -> str:
        m = (_YIL_RE.search(self._ad_metni())
             or _YIL_RE.search(self._grup_metni()))
        return m.group(0) if m else ""

    def kalite(self) -> str:
        m = _KALITE_RE.search(self._ad_metni())
        if not m:
            return ""
        k = m.group(0).upper().replace("WEBDL", "WEB-DL")
        return "4K" if k in ("4K", "UHD") else k

    def dublaj(self) -> bool:
        return bool(_DUB_RE.search(self._ad_metni()))

    def dil(self) -> str:
        """
        İçerik adından dil/seslendirme etiketini çıkarır.
        Örn: "Film (2020) TR Dublaj" → "TR Dublaj"
        """
        metin = f"{self._ad_metni()} {self._grup_metni()}"
        if re.search(r"\bTR\s*Dublaj\b", metin, re.I):
            return "TR Dublaj"
        if re.search(r"\b(TR\s*Altyaz[ıi]l[ıi]|Altyaz[ıi]l[ıi]|TR\s*Sub)\b", metin, re.I):
            return "TR Altyazılı"
        if re.search(r"\bT[üu]rk[çc]e\b", metin, re.I):
            return "Türkçe"
        if re.search(r"\b(ENG|[İi]ngilizce|English)\b", metin, re.I):
            return "İngilizce"
        return ""

    # ── ÖNBELLEK ──────────────────────────────────────────────────
    # Bu üç yöntem (bölüm eşleşmesi, sezon/bölüm, dizi anahtarı) düzinelerce
    # düzenli ifade çalıştırır ve arama sırasında İÇERİK BAŞINA DEFALARCA
    # çağrılır: filtreleme, gruplama, sıralama, birleştirme…
    #
    #   Ölçüm (36.000 içerik, aynı dizi 3 kaynakta):
    #       tek arama → 3,4 sn   (diziye_grupla 0,94 + _filtre içinde tekrar)
    #   Arama 220 ms gecikmeli olduğu için kullanıcı yazmaya devam ederken
    #   istekler birikiyor ve program yanıt vermez hâle geliyordu.
    #
    # Sonuç (ad, grup) çiftine göre saklanır; ad/grup değişirse kendini
    # geçersiz kılar (içerik düzenleme bunu bozmasın diye).
    def _onbellek_gecerli(self) -> bool:
        return getattr(self, "_ob_imza", None) == (self.ad, self.grup)

    def _onbellek_tazele(self):
        self._ob_imza = (self.ad, self.grup)
        self._ob_esl = None
        self._ob_sb = None
        self._ob_anahtar = None
        self._ob_kok = None

    def _ad_metni(self) -> str:
        """
        `ad` alanının GÜVENLİ metin hâli.

        ÇÖKME DÜZELTMESİ (ölçülerek bulundu): Bazı kayıtlarda `ad` None ya da
        sayı olabiliyor (bozuk M3U satırı, elle düzenleme, eski/başka sürümden
        gelen JSON). Düzenli ifadeler bunu kabul etmiyor:

            TypeError: expected string or bytes-like object, got 'NoneType'

        Bu hata arama sırasında `dizi_anahtari()` çağrılınca fırlıyor,
        Qt slotu içinde yakalanmadığı için PROGRAM KAPANIYORDU.
        """
        a = self.ad
        if isinstance(a, str):
            return a
        return "" if a is None else str(a)

    def _grup_metni(self) -> str:
        """`grup` alanının güvenli metin hâli (aynı gerekçe)."""
        g = self.grup
        if isinstance(g, str):
            return g
        return "" if g is None else str(g)

    def _bolum_eslesmesi_ham(self):
        """
        Ad içindeki bölüm işaretini bulur.

        Döndürür: (eşleşme, sezon, bölüm) ya da (None, 0, 0)
        Desteklenen biçimler:
            "Dizi S02E07"                → (2, 7)
            "Dizi 12. Bölüm"             → (1, 12)   ← Türk yayınlarında yaygın
            "Dizi 2. Sezon 5. Bölüm"     → (2, 5)
            "Dizi Bölüm 8"               → (1, 8)
        """
        ad = self._ad_metni()
        if not ad:
            return None, 0, 0
        m = _SE_RE.search(ad)
        if m:
            return m, int(m.group(1)), int(m.group(2))
        m = _TR_BOLUM_RE.search(ad)
        if m:
            sezon = int(m.group(1)) if m.group(1) else 1
            return m, sezon, int(m.group(2))
        m = _TR_BOLUM_RE2.search(ad)
        if m:
            return m, 1, int(m.group(1))
        return None, 0, 0

    def _bolum_eslesmesi(self):
        """Önbellekli sürüm (ham hesap `_bolum_eslesmesi_ham`)."""
        if not self._onbellek_gecerli():
            self._onbellek_tazele()
        if self._ob_esl is None:
            self._ob_esl = self._bolum_eslesmesi_ham()
        return self._ob_esl

    def sezon_bolum(self) -> tuple[int, int] | None:
        if not self._onbellek_gecerli():
            self._onbellek_tazele()
        if self._ob_sb is None:
            m, s, b = self._bolum_eslesmesi()
            self._ob_sb = (s, b) if m else False
        return self._ob_sb or None

    def dizi_anahtari(self) -> str:
        """Önbellekli dizi anahtarı (ham hesap `_dizi_anahtari_ham`)."""
        if not self._onbellek_gecerli():
            self._onbellek_tazele()
        if self._ob_anahtar is None:
            self._ob_anahtar = self._dizi_anahtari_ham()
        return self._ob_anahtar

    def _dizi_anahtari_ham(self) -> str:
        """
        Bölüm ekini atarak dizi adını verir.

        ÖNEMLİ: S/E'den SONRA gelen sonek de anahtarın parçasıdır.
        Aynı dizinin farklı sürümleri şöyle adlandırılabiliyor:
            "Dizi (2000) S01E01"        → "Dizi"
            "Dizi (2000) - B S01E01"    → "Dizi - B"
            "Dizi S01E01 - B"           → "Dizi - B"      ← eskiden "Dizi" oluyordu
            "Dizi S01E01 (TR Dublaj)"   → "Dizi (TR Dublaj)"
        Sonek yok sayılınca iki sürüm tek kartta birleşiyor ve her bölüm
        iki kez listeleniyordu.
        """
        m, _s, _b = self._bolum_eslesmesi()
        if not m:
            return ""
        ad = self._ad_metni()
        on = temiz_baslik(ad[: m.start()])
        son = ad[m.end():].strip(" .-_|")

        # DÜZELTME: Kalite etiketi de AYIRT EDİCİDİR.
        #   "Dizi S01E01"        → "Dizi"
        #   "Dizi S01E01 1080p"  → "Dizi - 1080p"   (eskiden "Dizi" olup birleşiyordu)
        # Eskiden kalite soneği atıldığı için 1080p sürümü ana grupla
        # karışıyor ve her bölümden iki tane görünüyordu (ölçüldü: 24 bölüm).
        kalite_etiketleri = _KALITE_RE.findall(son)
        son = _KALITE_RE.sub(" ", son)
        son = re.sub(r"\b(x265|x264|HEVC|REMUX|AAC|AC3|DUAL)\b", " ", son, flags=re.I)
        son = re.sub(r"[\(\[\]\)]", " ", son)
        son = re.sub(r"\s{2,}", " ", son).strip(" .-_|")

        parcalar = []
        if son and len(son) <= 24:
            parcalar.append(son)
        for k in kalite_etiketleri:
            k = (k[0] if isinstance(k, tuple) else k).strip()
            if k and k.upper() not in [p.upper() for p in parcalar]:
                parcalar.append(k)

        if parcalar:
            ek = " ".join(parcalar)[:28]
            on = f"{on} - {ek}" if on else ek

        # KAYNAK AYRIMI
        #   Aynı dizi M3U'da birden çok kaynaktan/kategoriden gelebiliyor
        #   (ör. "Vatanım Sensin" hem "YERLİ DİZİLER" hem "TRT DİZİLERİ"
        #   grubunda). Eskiden grup yok sayıldığı için hepsi TEK anahtara
        #   düşüyor, arama tek sonuç veriyor ve karta tıklanınca her bölüm
        #   kaynak sayısı kadar tekrar ediyordu (ölçüldü: 3 grup → 1 kart).
        #   Artık grup adı anahtarın parçası: her kaynak kendi kartını alır.
        g = self._grup_metni().strip()
        if g:
            return f"{on}  ·  {g[:28]}" if on else g[:28]
        return on

    def dizi_kok_anahtari(self) -> str:
        """Önbellekli kök ad (ham hesap `_dizi_kok_ham`)."""
        if not self._onbellek_gecerli():
            self._onbellek_tazele()
        if self._ob_kok is None:
            self._ob_kok = self._dizi_kok_ham()
        return self._ob_kok

    def _dizi_kok_ham(self) -> str:
        """
        Kaynaktan BAĞIMSIZ dizi adı.

        `dizi_anahtari()` kaynağa göre ayırır (her kaynak ayrı kart).
        Bazı yerlerde ise dizinin kendisi gerekir — örneğin TMDB eşleşmesi
        ya da "bu dizinin tüm bölümleri" araması. Orada bu kullanılır.
        """
        m, _s, _b = self._bolum_eslesmesi()
        if not m:
            return ""
        return temiz_baslik(self._ad_metni()[: m.start()])

    def temiz_ad(self) -> str:
        return temiz_baslik(self._ad_metni())

    def arama_metni(self) -> str:
        return tr_sadelestir(f"{self._ad_metni()} {self._grup_metni()}")


def temiz_baslik(t: str) -> str:
    """Yıl, kalite ve dublaj etiketlerini ayıklayıp okunur ad bırakır."""
    if not t:
        return ""
    s = _SE_RE.sub(" ", t)
    # Türkçe "12. Bölüm" / "2. Sezon 5. Bölüm" ekleri de temizlenmeli;
    # yoksa kart başlığı "Kızılcık Şerbeti 12 Bolum" gibi görünüyordu.
    s = _TR_BOLUM_RE.sub(" ", s)
    s = _TR_BOLUM_RE2.sub(" ", s)
    s = re.sub(r"[\(\[]?\b(19|20)\d{2}\b[\)\]]?", " ", s)
    s = _KALITE_RE.sub(" ", s)
    s = _DUB_RE.sub(" ", s)
    s = re.sub(r"\b(x265|x264|HEVC|DUAL|REMUX|AAC|AC3)\b", " ", s, flags=re.I)
    s = re.sub(r"[._]+", " ", s)
    s = re.sub(r"\s*[-–|]\s*$", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" -–|")
    return s or t.strip()


def kategori_tahmin(grup: str, ad: str) -> str:
    g = tr_kucuk(f"{grup} {ad}")
    if _SE_RE.search(ad) or _TR_BOLUM_RE.search(ad) or _TR_BOLUM_RE2.search(ad):
        return "anime" if "anim" in g else "series"
    if any(k in g for k in ("dizi", "series", "sezon")):
        return "series"
    if any(k in g for k in ("anime", "animasyon", "cartoon", "çizgi")):
        return "anime"
    if any(k in g for k in ("canlı", "canli", "live", "tv", "haber", "spor", "ulusal", "channel")):
        return "live"
    if any(k in g for k in ("film", "movie", "sinema")):
        return "movie"
    return "movie"


def m3u_ayristir(metin: str, kaynak: str = "", kategori: str | None = None) -> list[Icerik]:
    """M3U/M3U8 metnini Icerik listesine çevirir."""
    sonuc: list[Icerik] = []
    if not metin:
        return sonuc
    cur: Icerik | None = None
    for ham in metin.splitlines():
        satir = ham.strip()
        if not satir:
            continue
        if satir.upper().startswith("#EXTINF"):
            attrs = dict(_ATTR_RE.findall(satir)) if '"' in satir else {}
            ad = extinf_ad(satir)
            sure_m = _SURE_RE.match(satir)
            grup = attrs.pop("group-title", "")
            logo = attrs.pop("tvg-logo", "")
            tvg_ad = attrs.pop("tvg-name", "")
            cur = Icerik(
                ad=(tvg_ad or ad or "?").strip(),
                logo=logo.strip(),
                grup=grup.strip(),
                kaynak=kaynak,
                tvg_id=attrs.get("tvg-id", ""),
                attrs=attrs,
                sure=(sure_m.group(1) if sure_m and sure_m.group(1) != "-1" else ""),
                eklenme=time.time(),
            )
        elif _VLC_RE.match(satir):
            if cur:
                # Bazı listeler (özellikle vidload vb.) URL'yi EXTVLCOPT
                # satırına yapıştırır:
                #   #EXTVLCOPT:http-referrer=https://site/ https://cdn/.../master.m3u8
                # VLC toleranslıdır; mpv/MediaBox ayrı URL satırı bekler.
                # Değer içindeki ikinci http(s) adresini URL olarak ayır.
                m = re.search(
                    r'(#EXTVLCOPT:\s*[\w-]+=)(\S+)\s+(https?://\S+)',
                    satir, re.I)
                if m:
                    cur.vlcopt.append(m.group(1) + m.group(2))
                    if not cur.url:
                        cur.url = m.group(3).rstrip()
                        cur.kategori = kategori or kategori_tahmin(cur.grup, cur.ad)
                        sonuc.append(cur)
                        cur = None
                else:
                    cur.vlcopt.append(satir)
        elif satir.startswith("#"):
            continue
        else:
            if cur is None:
                ad = satir.rsplit("/", 1)[-1].split("?")[0] or satir
                cur = Icerik(ad=ad, kaynak=kaynak, eklenme=time.time())
            cur.url = satir
            cur.kategori = kategori or kategori_tahmin(cur.grup, cur.ad)
            sonuc.append(cur)
            cur = None
    # Dosya bittiğinde URL'siz kalan son kayıt (yalnızca EXTVLCOPT
    # satırından URL çıkarılmış ama yukarıda eklenmemiş olabilir)
    # zaten yukarıda append edildi; burada ekstra işlem yok.
    return sonuc


def m3u_uret(icerikler: list[Icerik]) -> str:
    """Icerik listesini geri M3U metnine çevirir (alan kaybı olmadan)."""
    satirlar = ["#EXTM3U"]
    for e in icerikler:
        if not e.url:
            continue
        p = [f'#EXTINF:{e.sure or -1}']
        if e.tvg_id:
            p.append(f'tvg-id="{e.tvg_id}"')
        if e.logo:
            p.append(f'tvg-logo="{e.logo}"')
        if e.grup:
            p.append(f'group-title="{e.grup}"')
        for k, v in (e.attrs or {}).items():
            if k not in ("tvg-id",):
                p.append(f'{k}="{v}"')
        satirlar.append(" ".join(p) + "," + e.ad)
        satirlar.extend(e.vlcopt or [])
        satirlar.append(e.url)
    return "\n".join(satirlar) + "\n"


# ══════════════════════════════════════════════════════════════════
#  ÜCRETSİZ & YASAL KAYNAKLAR
# ══════════════════════════════════════════════════════════════════
UCRETSIZ_KAYNAKLAR = [
    {"id": "film1", "ad": "Filmler (Dropbox)", "kategori": "movie", "adet": "film listesi",
     "aciklama": "Topluluk film listesi — Dropbox M3U.",
     "url": "https://www.dropbox.com/scl/fi/8svau5bmyx5qj7q4nrlqd/Filmler.m3u?rlkey=ekaq2mezh5rof42locnduo7uu&st=s9qr0bjx&dl=1"},
    {"id": "film2", "ad": "Filmler Yeni (Dropbox)", "kategori": "movie", "adet": "film listesi",
     "aciklama": "Güncel film listesi — Dropbox M3U.",
     "url": "https://www.dropbox.com/scl/fi/qz9lm3tdsldcbgsnm3w4g/Filmler_Yeni.m3u?rlkey=j1mo7gra8k5lizcb69vhbx6qn&st=5rl02f32&dl=1"},
    {"id": "diziler", "ad": "Diziler (Dropbox)", "kategori": "series", "adet": "dizi listesi",
     "aciklama": "Topluluk dizi listesi — Dropbox M3U.",
     "url": "https://www.dropbox.com/scl/fi/rdva56n1u5ys2q3m5a3im/Diziler.m3u?rlkey=bxke7127z55vinybhe0g5e6ot&st=290i7vmi&dl=1"},
    {"id": "freetv", "ad": "Free-TV (Küresel)", "kategori": "live", "adet": "~1.900 kanal",
     "aciklama": "Pluto, Samsung, Plex, Roku ve yerel yayınların derlendiği özenli liste.",
     "url": "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8"},
    {"id": "iptvtr", "ad": "iptv-org — Türkiye", "kategori": "live", "adet": "~205 kanal",
     "aciklama": "TRT, ATV, A Haber, 24 TV gibi Türkçe kanallar. Açık kaynak topluluk listesi.",
     "url": "https://iptv-org.github.io/iptv/countries/tr.m3u"},
    {"id": "pluto", "ad": "Pluto TV", "kategori": "live", "adet": "~2.844 kanal",
     "aciklama": "Paramount'un ücretsiz reklam destekli servisi. 14+ ülke.",
     "url": "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/plutotv_all.m3u"},
    {"id": "samsung", "ad": "Samsung TV Plus", "kategori": "live", "adet": "~2.572 kanal",
     "aciklama": "Samsung'un ücretsiz kanal servisi. 11+ ülke.",
     "url": "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/samsungtvplus_all.m3u"},
    {"id": "plex", "ad": "Plex Free", "kategori": "live", "adet": "~2.884 kanal",
     "aciklama": "Plex'in ücretsiz canlı kanalları. 8+ ülke.",
     "url": "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/plex_all.m3u"},
    {"id": "tubi", "ad": "Tubi", "kategori": "live", "adet": "~178 kanal",
     "aciklama": "Fox'un ücretsiz servisi (ABD ağırlıklı).",
     "url": "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/tubi_all.m3u"},
    {"id": "roku", "ad": "Roku Channel", "kategori": "live", "adet": "~351 kanal",
     "aciklama": "Roku'nun ücretsiz kanalları.",
     "url": "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/roku_all.m3u"},
    {"id": "archive", "ad": "Internet Archive — Kamu Malı Filmler", "kategori": "movie",
     "adet": "~28.000 film", "aciklama": "Telifi düşmüş klasik filmler. Tamamen yasal.",
     "url": "ia://feature_films"},
]

# ── Arayüz temaları ───────────────────────────────────────────────
TEMALAR = [
    {"id": "netflix", "ad": "Netflix Koyu",
     "vurgu": "#E50914",
            "tema": "netflix", "arka": "#0b0c10", "yuzey": "#15171e", "yuzey2": "#1d2029",
     "cizgi": "#2a2e3a", "metin": "#eceef4", "soluk": "#8b90a0"},
    {"id": "mor", "ad": "Mor Gece",
     "vurgu": "#6C5CE7", "arka": "#0d0b14", "yuzey": "#16122a", "yuzey2": "#1e1a36",
     "cizgi": "#2e2848", "metin": "#eeeaf8", "soluk": "#9a94b8"},
    {"id": "ocean", "ad": "Okyanus",
     "vurgu": "#0984E3", "arka": "#0a1018", "yuzey": "#121a24", "yuzey2": "#1a2432",
     "cizgi": "#273445", "metin": "#e8eef5", "soluk": "#8494a8"},
    {"id": "yesil", "ad": "Orman",
     "vurgu": "#00B894", "arka": "#0a1210", "yuzey": "#121c18", "yuzey2": "#1a2822",
     "cizgi": "#2a3a32", "metin": "#e8f4ee", "soluk": "#849e90"},
    {"id": "amber", "ad": "Amber",
     "vurgu": "#E17055", "arka": "#12100c", "yuzey": "#1c1814", "yuzey2": "#28221c",
     "cizgi": "#3a3228", "metin": "#f4eee8", "soluk": "#a89888"},
    {"id": "pembe", "ad": "Pembe Neon",
     "vurgu": "#FD79A8", "arka": "#120b10", "yuzey": "#1c1218", "yuzey2": "#281a22",
     "cizgi": "#3a2832", "metin": "#f8eef2", "soluk": "#b090a0"},
    {"id": "minimal", "ad": "Minimal Gri",
     "vurgu": "#74B9FF", "arka": "#101214", "yuzey": "#181a1e", "yuzey2": "#22262c",
     "cizgi": "#32363e", "metin": "#e8eaee", "soluk": "#9098a4"},
]


def tema_bul(tema_id: str) -> dict:
    for t in TEMALAR:
        if t["id"] == tema_id:
            return t
    return TEMALAR[0]


# ══════════════════════════════════════════════════════════════════
#  DEPO (kalıcı veri)
# ══════════════════════════════════════════════════════════════════
class Depo:
    def __init__(self, yol: Path = DB_YOLU):
        self.yol = yol
        self.icerikler: list[Icerik] = []
        self.kaynaklar: list[dict] = []
        self.favoriler: list[str] = []
        self.son_izlenen: list[str] = []
        self.ilerleme: dict[str, dict] = {}
        self.etiketler: dict[str, list[str]] = {}
        self.kuyruk: list[str] = []            # "sonra izle" listesi (URL)
        self.istatistik: dict[str, Any] = {}
        self.ayarlar: dict[str, Any] = {
            "tmdb_anahtar": TMDB_ANAHTAR_VARSAYILAN,
            "vurgu": "#E50914",
            "tmdb_afis": True,
            "video_modu": "oto",       # oto | gomulu | ayri
            "video_cikis": "oto",      # oto | x11egl | gpu | gpu-next | x11 | xv
            "saglayicilar": [],        # kullanıcının kendi URL şablonları
            "onizleme": True,          # çubukta gezinirken kare önizlemesi
            "shader": "",              # mpv GLSL shader dosyası (boş = kapalı)
            "yeni_bolum_bildirim": True,   # TMDB'den yeni bölüm kontrolü
            "jenerik_atla": True,      # dizi jeneriğinde "atla" düğmesi göster
            "sonraki_bolum_sn": 8,     # bölüm bitince geri sayım (0 = anında geç)
            "yenileme_saat": 6,        # URL kaynaklarını kaç saatte bir yenile (0=kapalı)
            "donanim_hizlandirma": True,
        }
        self.yukle()

    # ── kayıt / yükleme ──
    def yukle(self):
        if not self.yol.is_file():
            return
        try:
            d = json.loads(self.yol.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[MediaBox] Veri okunamadı: {e}", file=sys.stderr)
            return
        self.icerikler = _icerikleri_coz(d.get("icerikler", []))
        self.kaynaklar = d.get("kaynaklar", [])
        self.favoriler = d.get("favoriler", [])
        self.son_izlenen = d.get("son_izlenen", [])
        self.ilerleme = d.get("ilerleme", {})
        self.etiketler = d.get("etiketler", {})
        self.kuyruk = d.get("kuyruk", [])
        self.istatistik = d.get("istatistik", {})
        self.ayarlar.update(d.get("ayarlar", {}))

    def kaydet(self):
        """
        Depoyu diske yazar (atomik + fsync — ani kapanmada kayıp azalsın).

        TEK BOZUK KAYIT TÜM KAYDETMEYİ DÜŞÜRMESİN: `_icerik_sozluk` bir
        öğede beklenmedik bir hatayla patlarsa (ör. ileride başka bir alanda
        çıkabilecek benzer bir döngü/hata), o TEK öğe atlanır; favoriler,
        ilerleme ve geri kalan binlerce içerik yine de diske yazılır. Eskiden
        (asdict() döngü hatasında ölçüldüğü gibi) tek bir bozuk öğe TÜM
        kaydetme işlemini çökertip o güne ait hiçbir değişikliğin diske
        yazılmamasına yol açıyordu.
        """
        icerik_json, atlanan = [], 0
        for x in self.icerikler:
            try:
                icerik_json.append(_icerik_sozluk(x))
            except Exception as e:
                atlanan += 1
                print(f"[MediaBox] Kaydedilemeyen içerik atlandı ({x.url!r}): "
                      f"{type(e).__name__}: {e}", file=sys.stderr)
        if atlanan:
            print(f"[MediaBox] UYARI: {atlanan} içerik kaydedilemedi, atlandı.",
                  file=sys.stderr)
        d = {
            "surum": APP_SURUM,
            "icerikler": icerik_json,
            "kaynaklar": self.kaynaklar,
            "favoriler": self.favoriler,
            "son_izlenen": self.son_izlenen,
            "ilerleme": self.ilerleme,
            "etiketler": self.etiketler,
            "kuyruk": self.kuyruk,
            "istatistik": self.istatistik,
            "ayarlar": self.ayarlar,
        }
        self.yol.parent.mkdir(parents=True, exist_ok=True)
        gecici = self.yol.with_suffix(".tmp")
        metin = json.dumps(d, ensure_ascii=False)
        with open(gecici, "w", encoding="utf-8") as f:
            f.write(metin)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(gecici, self.yol)   # atomik yazma: yarım dosya kalmaz

    # ── sorgular ──
    def kategoriye_gore(self, kategori: str) -> list[Icerik]:
        if kategori == "favorites":
            fav = set(self.favoriler)
            return [e for e in self.icerikler if self.favori_mi(e)]
        if kategori == "recent":
            sira = {u: i for i, u in enumerate(self.son_izlenen)}
            return sorted([e for e in self.icerikler if e.url in sira],
                          key=lambda e: sira.get(e.url, 9999))
        if kategori == "all":
            return list(self.icerikler)
        return [e for e in self.icerikler if e.kategori == kategori]

    def ara(self, sorgu: str, kapsam: list[Icerik] | None = None) -> list[Icerik]:
        q = tr_sadelestir(sorgu).strip()
        if not q:
            return kapsam if kapsam is not None else list(self.icerikler)
        havuz = kapsam if kapsam is not None else self.icerikler
        return [e for e in havuz if q in e.arama_metni()]

    def favori_mi(self, e: Icerik) -> bool:
        # Kök ad + eski dizi_anahtari + url — hepsi kabul (geçmiş kayıtlar)
        kok = (e.dizi_kok_anahtari() or "").strip()
        k = e.dizi_anahtari()
        return (e.url in self.favoriler
                or (bool(kok) and kok in self.favoriler)
                or (bool(k) and k in self.favoriler))

    def favori_degistir(self, e: Icerik) -> bool:
        # Kalıcı anahtar: dizi kök adı (grup/kalite bağımsız), yoksa URL
        anahtar = (e.dizi_kok_anahtari() or "").strip() or e.url
        # Eski biçimdeki kayıtları da temizle
        eski_k = e.dizi_anahtari()
        for a in (anahtar, eski_k, e.url):
            if a and a in self.favoriler:
                self.favoriler = [x for x in self.favoriler if x not in (anahtar, eski_k, e.url)]
                return False
        self.favoriler.insert(0, anahtar)
        return True

    def izleme_kaydet(self, e: Icerik, konum: float, sure: float):
        if sure <= 0 or not e or not e.url:
            return
        oran = konum / sure if sure else 0
        kalan = sure - konum
        # Bitti: hem yüzde yüksek HEM kalan kısa — uzun filmde erken silme yok
        bitti = (oran > 0.97) and (kalan <= 120)
        if bitti:
            self.ilerleme.pop(e.url, None)
        elif konum < 15:
            # Yeni başlangıç: MEVCUT ilerlemeyi SİLME (kapanışta konum=0
            # gelirse yarıda kalınan film kayboluyordu).
            pass
        else:
            self.ilerleme[e.url] = {
                "t": float(konum), "d": float(sure),
                "zaman": time.time(), "ad": e.ad,
            }
        if e.url in self.son_izlenen:
            self.son_izlenen.remove(e.url)
        self.son_izlenen.insert(0, e.url)
        del self.son_izlenen[60:]
        g = time.strftime("%Y-%m-%d")
        self.istatistik.setdefault(g, 0)

    def url_tasi(self, eski_url: str, yeni_url: str):
        """URL değişince ilerleme / favori / kuyruk anahtarlarını taşı."""
        if not eski_url or not yeni_url or eski_url == yeni_url:
            return
        if eski_url in self.ilerleme and yeni_url not in self.ilerleme:
            self.ilerleme[yeni_url] = self.ilerleme.pop(eski_url)
        else:
            self.ilerleme.pop(eski_url, None)
        self.favoriler = [yeni_url if x == eski_url else x for x in self.favoriler]
        self.son_izlenen = [yeni_url if x == eski_url else x for x in self.son_izlenen]
        self.kuyruk = [yeni_url if x == eski_url else x for x in self.kuyruk]

    def izlemeye_devam(self) -> list[tuple[Icerik, dict]]:
        harita = {e.url: e for e in self.icerikler}
        cift = []
        for url, p in self.ilerleme.items():
            e = harita.get(url)
            if e:
                cift.append((e, p))
        cift.sort(key=lambda x: x[1].get("zaman", 0), reverse=True)
        return cift[:20]

    def tum_bolumler(self, icerik) -> list:
        """
        Bir içeriğin ait olduğu dizinin TÜM bölümlerini döndürür.

        "İzlemeye Devam Et" rafında yalnızca yarım kalan bölüm tutulur;
        karta tıklanınca sadece o bölüm görünüyordu. Bu yardımcı, aynı
        dizi anahtarına sahip bütün bölümleri toplayıp sıralar.
        """
        kok = (icerik.dizi_kok_anahtari() or "").strip()
        anahtar = icerik.dizi_anahtari()
        if not kok and not anahtar:
            return [icerik]
        if kok:
            nk = tr_sadelestir(kok)
            hepsi = [e for e in self.icerikler
                     if tr_sadelestir(e.dizi_kok_anahtari() or "") == nk]
        else:
            hepsi = [e for e in self.icerikler if e.dizi_anahtari() == anahtar]
        if not hepsi:
            return [icerik]
        hepsi.sort(key=lambda x: x.sezon_bolum() or (0, 0))
        # Aynı diziyi sunan birden fazla kaynak varsa (bkz. diziye_grupla)
        # aynı (sezon,bölüm) burada da tekrar edebilir; birleştir.
        return _bolumleri_birlestir(hepsi)

    def kuyrukta_mi(self, icerik) -> bool:
        return icerik.url in self.kuyruk

    def kuyruk_degistir(self, icerik) -> bool:
        """Sonra izle listesine ekler/çıkarır. Dönen: artık kuyrukta mı."""
        if icerik.url in self.kuyruk:
            self.kuyruk.remove(icerik.url)
            return False
        self.kuyruk.insert(0, icerik.url)
        return True

    def kuyruk_icerikleri(self) -> list:
        harita = {e.url: e for e in self.icerikler}
        return [harita[u] for u in self.kuyruk if u in harita]

    def diller(self) -> list[str]:
        """Listede geçen dil/dublaj etiketlerini toplar."""
        sayim = {}
        for e in self.icerikler:
            d = e.dil()
            if d:
                sayim[d] = sayim.get(d, 0) + 1
        return [k for k, _ in sorted(sayim.items(), key=lambda x: -x[1])]

    def gruplar(self, kategori: str) -> list[str]:
        g = {}
        for e in self.kategoriye_gore(kategori):
            if e.grup:
                g[e.grup] = g.get(e.grup, 0) + 1
        return [k for k, _ in sorted(g.items(), key=lambda x: -x[1])]


def _icerikleri_coz(ham: list) -> list:
    """
    JSON'dan Icerik listesi üretir — TEK BOZUK KAYIT TÜM LİSTEYİ DÜŞÜRMEZ.

    Eskiden `[Icerik(**x) for x in ...]` kullanılıyordu. Sözlükte tanınmayan
    tek bir alan (başka/ileri bir sürümden gelen kayıt, elle düzenlenmiş
    dosya) `TypeError` fırlatıp KÜTÜPHANENİN TAMAMINI kaybettiriyordu
    (ölçüldü: 6 kayıttan 1'i bozuk → 0 kayıt okundu).

    Artık: bilinmeyen alanlar atlanır, çözülemeyen kayıt tek başına atlanır,
    kalan her şey okunur.
    """
    from dataclasses import fields as _alanlar
    gecerli = {f.name for f in _alanlar(Icerik)}
    cikti, atlanan = [], 0
    for x in ham:
        if not isinstance(x, dict):
            atlanan += 1
            continue
        try:
            cikti.append(Icerik(**{k: v for k, v in x.items() if k in gecerli}))
        except Exception:
            atlanan += 1
    if atlanan:
        print(f"[MediaBox] {atlanan} bozuk kayıt atlandı", file=sys.stderr)
    return cikti


def _icerik_sozluk(e: "Icerik") -> dict:
    """
    Icerik'i diske yazılacak sözlüğe çevirir.

    ÖNEMLİ #1: `_ob_*` önbellek alanları (bkz. Icerik._onbellek_tazele) yalnızca
    çalışma anına aittir. `asdict()` bunları da dahil ediyordu; kaydedilirse
    hem dosya gereksiz büyür hem de yeniden okunduğunda bayat değerler
    içeriğin gerçek adıyla çelişir.

    ÖNEMLİ #2 (kritik çökme düzeltmesi): `alternatifler` de yalnızca çalışma
    anına ait, UI gruplama amaçlı bir alandır — kalıcı veri DEĞİLDİR ve asla
    diske yazılmamalıdır. `asdict()` bu alana da DERİNLEMESİNE dalıyor; eğer
    iki Icerik nesnesi (bir gruplama hatası sonucu) birbirini alternatif
    olarak gösteriyorsa, `asdict()` sonsuz döngüye girip
    `RecursionError` ile TÜM kaydetme işlemini çökertiyordu (ölçüldü:
    her `depo.kaydet()` çağrısı çöküyor, o andan sonra favoriler/izleme
    ilerlemesi bir daha hiç diske yazılamıyordu). Bu yüzden `alternatifler`
    asdict()'e HİÇ sokulmadan geçici olarak boşaltılıp sonra geri konuyor.
    """
    alt_yedek = e.alternatifler
    e.alternatifler = []
    try:
        d = asdict(e)
    finally:
        e.alternatifler = alt_yedek
    for k in ("_ob_imza", "_ob_esl", "_ob_sb", "_ob_anahtar", "_ob_kok"):
        d.pop(k, None)
    d.pop("alternatifler", None)
    return d


def diziye_grupla(icerikler: list[Icerik]) -> list[dict]:
    """
    Aynı dizinin bölümlerini tek karta toplar.

    GRUP ANAHTARI (düzeltme 2026-08):
        Eskiden `dizi_anahtari()` kullanılıyordu; bu anahtar group-title
        ve kalite sonekini de içeriyordu. Sonuç:
          • 1–2. sezon "Dizi Adı" grubunda
          • 3. sezon "Dizi Adı Sezon 3" grubunda
        → iki ayrı kart; kullanıcı yalnızca 3. sezon kartını görünce
        "ilk sezonlar kayboldu" sanıyordu (ölçülen şikayet).

        Artık birincil anahtar `dizi_kok_anahtari()` (yalnızca dizi adı).
        Farklı grup/kalite aynı (sezon,bölüm) için `alternatifler`e düşer;
        farklı bölümler tek listede birleşir.
    """
    gruplar: dict[str, dict] = {}
    tekler: list[dict] = []
    for e in icerikler:
        # Kök ad (kaynak/grup bağımsız); yoksa eski anahtara düş.
        kok = (e.dizi_kok_anahtari() or "").strip()
        anahtar = kok or e.dizi_anahtari()
        if anahtar:
            # Arama/eşleşme için normalize anahtar (Türkçe sade)
            norm = tr_sadelestir(anahtar)
            g = gruplar.setdefault(norm, {"baslik": kok or anahtar, "bolumler": [],
                                          "logo": e.logo,
                                          "kategori": e.kategori, "tekil": False,
                                          "kaynak": (e.grup or "").strip(),
                                          "anahtar": norm})
            g["bolumler"].append(e)
            if not g["logo"]:
                g["logo"] = e.logo
            # Başlık: daha kısa/temiz olanı tercih et
            if kok and (not g["baslik"] or len(kok) < len(g["baslik"])):
                g["baslik"] = kok
            # Kategori: series/anime öncelikli
            if e.kategori in ("series", "anime") and g["kategori"] not in ("series", "anime"):
                g["kategori"] = e.kategori
        else:
            tekler.append(e)

    # Aynı adlı filmleri tek kartta topla; farklı URL'ler alternatif olur.
    film_havuz: dict[str, list] = {}
    film_kartlari: list[dict] = []
    for e in tekler:
        ad = tr_sadelestir(e.temiz_ad() or e.ad or "") or (e.url or "")
        film_havuz.setdefault(ad, []).append(e)
    for ad, liste in film_havuz.items():
        # URL tekrarı temizle
        gorulen = set()
        temiz = []
        for x in liste:
            u = (x.url or "").strip()
            if not u or u in gorulen:
                continue
            gorulen.add(u)
            temiz.append(x)
        if not temiz:
            continue
        temsilci = temiz[0]
        if temsilci.alternatifler:
            temsilci.alternatifler = []
        for diger in temiz[1:]:
            if diger.alternatifler:
                diger.alternatifler = []
            temsilci.alternatifler.append(diger)
        n_alt = len(temsilci.alternatifler)
        kaynak_yazi = (temsilci.grup or "").strip()
        if n_alt:
            kaynak_yazi = f"{n_alt + 1} kaynak" if not kaynak_yazi else f"{kaynak_yazi} · {n_alt + 1} kaynak"
        film_kartlari.append({
            "baslik": temsilci.temiz_ad() or temsilci.ad,
            "bolumler": [temsilci],
            "logo": temsilci.logo,
            "kategori": temsilci.kategori,
            "tekil": True,
            "kaynak": kaynak_yazi,
            "anahtar": ad,
        })

    for g in gruplar.values():
        g["bolumler"].sort(key=lambda x: x.sezon_bolum() or (0, 0))
        g["bolumler"] = _bolumleri_birlestir(g["bolumler"])
        # Kaynak alanını: birden fazla grup varsa özetle
        grup_adlari = []
        for b in g["bolumler"]:
            gr = (b.grup or "").strip()
            if gr and gr not in grup_adlari:
                grup_adlari.append(gr)
            for a in (b.alternatifler or []):
                gr = (a.grup or "").strip()
                if gr and gr not in grup_adlari:
                    grup_adlari.append(gr)
        if len(grup_adlari) > 1:
            g["kaynak"] = f"{len(grup_adlari)} kaynak"
        elif grup_adlari:
            g["kaynak"] = grup_adlari[0]
    return list(gruplar.values()) + film_kartlari


def _bolumleri_birlestir(bolumler: list[Icerik]) -> list[Icerik]:
    """
    Aynı (sezon, bölüm) numarasına sahip birden fazla girişi tek
    temsilciye indirger; geri kalanları temsilcinin `alternatifler`
    listesine ekler (kaynak değiştirme desteği ileride buradan
    genişletilebilir; şimdilik yalnızca tekrarı önler).

    ÖNEMLİ — SINIRSIZ BÜYÜME HATASI (düzeltildi):
        `bolumler` içindeki Icerik nesneleri depo.icerikler'deki KALICI
        nesnelerin ta kendisi. Bu fonksiyon arama kutusuna her tuş
        vuruşunda yeniden çağrılıyor (diziye_grupla üzerinden). Eğer
        bir nesne temsilci seçildiğinde `alternatifler` listesi
        SIFIRLANMAZSA, önceki çağrılardan kalan öğelerin üzerine
        tekrar tekrar ekleme yapılır ve liste her tuş vuruşunda
        katlanarak büyür (ölçüldü: 5 harf yazınca 2 → 10 öğeye çıktı).
        Büyük kütüphanelerde bu birkaç saniyede belleği tüketip
        programın kilitlenip kapanmasına yol açıyordu. Bu yüzden bir
        nesne temsilci olarak seçildiğinde listesi önce boşaltılır.

    Sezon/bölüm numarası çözülemeyen girişler (sezon_bolum() → None)
    farklı içerikler olabileceğinden birleştirilmez, oldukları gibi
    bırakılır (yine de eski listeleri temizlenir).
    """
    sonuc: list[Icerik] = []
    konum: dict[tuple, int] = {}
    for b in bolumler:
        sb = b.sezon_bolum()
        if sb is None:
            if b.alternatifler:
                b.alternatifler = []
            sonuc.append(b)
            continue
        if sb in konum:
            if b.alternatifler:
                b.alternatifler = []
            sonuc[konum[sb]].alternatifler.append(b)
        else:
            if b.alternatifler:
                b.alternatifler = []
            konum[sb] = len(sonuc)
            sonuc.append(b)
    return sonuc


# ══════════════════════════════════════════════════════════════════
#  HTML SÜRÜMÜNDEN VERİ AKTARIMI
# ══════════════════════════════════════════════════════════════════
def html_verisi_ice_aktar(depo: Depo, json_yolu: str) -> dict:
    """
    Tarayıcıdan dışa aktarılan MediaBox yedeğini (JSON) içeri alır.
    Tarayıcıda:  Ayarlar → Yedekle  ya da konsolda:
        copy(JSON.stringify(Object.fromEntries(
          Object.keys(localStorage).filter(k=>k.startsWith('mb_'))
                .map(k=>[k, localStorage[k]]))))
    """
    p = Path(json_yolu).expanduser()
    if not p.is_file():
        return {"ok": False, "hata": f"Dosya yok: {p}"}
    try:
        ham = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "hata": f"JSON okunamadı: {e}"}

    def coz(deger):
        """localStorage değerleri metin olarak saklanır; JSON ise çöz."""
        if isinstance(deger, str):
            try:
                return json.loads(deger)
            except Exception:
                return deger
        return deger

    rapor = {"ok": True, "icerik": 0, "favori": 0, "ilerleme": 0, "kaynak": 0, "etiket": 0}

    # HTML sürümü tüm listeyi mb_data içinde tutar
    data = coz(ham.get("mb_data")) or {}
    if isinstance(data, dict):
        girdiler = data.get("entries") or data.get("icerikler") or []
        mevcut = {e.url for e in depo.icerikler}
        for g in girdiler:
            if not isinstance(g, dict):
                continue
            url = (g.get("url") or "").strip()
            if not url or url in mevcut:
                continue
            depo.icerikler.append(Icerik(
                ad=g.get("name") or g.get("ad") or "?",
                url=url,
                logo=g.get("logo") or "",
                grup=g.get("group") or g.get("grup") or "",
                kategori=g.get("category") or g.get("kategori") or "movie",
                kaynak=g.get("source") or g.get("kaynak") or "HTML aktarımı",
                tvg_id=str(g.get("tmdbId") or ""),
            ))
            mevcut.add(url)
            rapor["icerik"] += 1
        for s in (data.get("sources") or []):
            if isinstance(s, dict):
                depo.kaynaklar.append({"ad": s.get("name", "?"), "tur": s.get("type", "m3u"),
                                       "adet": s.get("count", 0)})
                rapor["kaynak"] += 1

    for u in (coz(ham.get("mb_favorites")) or []):
        if isinstance(u, str) and u not in depo.favoriler:
            depo.favoriler.append(u)
            rapor["favori"] += 1

    for u in (coz(ham.get("mb_recent")) or []):
        if isinstance(u, str) and u not in depo.son_izlenen:
            depo.son_izlenen.append(u)

    ilerleme = coz(ham.get("mb_progress")) or {}
    if isinstance(ilerleme, dict):
        for url, p2 in ilerleme.items():
            if isinstance(p2, dict) and "t" in p2 and "d" in p2:
                depo.ilerleme[url] = {"t": p2["t"], "d": p2["d"],
                                      "zaman": p2.get("at", time.time()) / 1000
                                      if p2.get("at", 0) > 1e11 else p2.get("at", time.time()),
                                      "ad": p2.get("name", "")}
                rapor["ilerleme"] += 1

    etiket = coz(ham.get("mb_tags")) or {}
    if isinstance(etiket, dict):
        for url, t in etiket.items():
            if isinstance(t, list):
                depo.etiketler[url] = t
                rapor["etiket"] += 1

    kullanici_kaynak = coz(ham.get("mb_user_sources")) or []
    if isinstance(kullanici_kaynak, list):
        for s in kullanici_kaynak:
            if isinstance(s, dict) and s.get("url"):
                depo.kaynaklar.append({"ad": s.get("name", s["url"]), "tur": "url",
                                       "url": s["url"], "adet": 0})
                rapor["kaynak"] += 1

    tema = coz(ham.get("mb_theme"))
    if isinstance(tema, str) and tema:
        depo.ayarlar["html_tema"] = tema

    depo.kaydet()
    return rapor


# ══════════════════════════════════════════════════════════════════
#  OYNATICI TANISI
#  "Oynat'a basınca kapanıyor" gibi çökmelerin sebebini bulmak için.
#  Wayland oturumunda mpv'yi pencereye gömmek (wid) çökmeye yol açabilir.
# ══════════════════════════════════════════════════════════════════
def oynatici_tanisi() -> str:
    import shutil
    sat = []
    ekle = sat.append
    ekle(f"{APP_ADI} Qt {APP_SURUM} — Oynatıcı Tanısı")
    ekle("=" * 56)
    ekle(f"Python        : {sys.version.split()[0]}")
    ekle(f"Platform      : {sys.platform}")

    oturum = os.environ.get("XDG_SESSION_TYPE", "?")
    wayland = os.environ.get("WAYLAND_DISPLAY", "")
    display = os.environ.get("DISPLAY", "")
    ekle(f"Oturum türü   : {oturum}")
    ekle(f"WAYLAND_DISPLAY: {wayland or '(yok)'}")
    ekle(f"DISPLAY (X11) : {display or '(yok)'}")
    ekle(f"Masaüstü      : {os.environ.get('XDG_CURRENT_DESKTOP', '?')}")
    ekle(f"Qt platformu  : {os.environ.get('QT_QPA_PLATFORM', '(varsayılan)')}")

    wl = bool(wayland) or oturum.lower() == "wayland"
    ekle("")
    if wl:
        ekle("⚠ WAYLAND OTURUMU")
        ekle("  mpv'yi pencere içine gömmek (wid) Wayland'de güvenilir değildir;")
        ekle("  uygulamanın kapanmasına yol açabilir.")
        ekle("  Bu yüzden 'Otomatik' modda AYRI PENCERE kullanılır.")
        ekle("  Gömülü görüntü istiyorsanız X11 oturumu açın ya da:")
        ekle("      QT_QPA_PLATFORM=xcb python mediabox_qt.py")
    else:
        ekle("✓ X11 oturumu — gömülü video destekleniyor.")

    ekle("")
    try:
        from PyQt6.QtCore import QT_VERSION_STR
        ekle(f"PyQt6 / Qt    : {QT_VERSION_STR}")
    except Exception as e:
        ekle(f"PyQt6         : YOK ({e})")

    try:
        import mpv as _m
        ekle(f"python-mpv    : {getattr(_m, '__version__', '?')}")
        try:
            t = _m.MPV(vo="null", ao="null", log_handler=lambda *a: None)
            ekle(f"libmpv        : {t.mpv_version}")
            try:
                ekle(f"Protokoller   : {', '.join(sorted(set(t.protocol_list))[:14])}")
            except Exception:
                pass
            t.terminate()
        except Exception as e:
            ekle(f"libmpv        : BAŞLATILAMADI — {type(e).__name__}: {e}")
    except Exception as e:
        ekle(f"python-mpv    : YOK ({e})")

    mpv_yol = shutil.which("mpv")
    ekle(f"mpv (komut)   : {mpv_yol or '✗ BULUNAMADI — sudo pacman -S mpv'}")
    ekle("")
    ekle("Oynatıcı mimarisi: mpv AYRI İŞLEM (JSON IPC)")
    ekle("  mpv çökse bile MediaBox kapanmaz.")
    if mpv_yol:
        try:
            r = subprocess.run([mpv_yol, "--version"], capture_output=True,
                               text=True, timeout=6)
            ilk = (r.stdout or "").splitlines()[:1]
            if ilk:
                ekle(f"  {ilk[0].strip()}")
        except Exception as e:
            ekle(f"  sürüm alınamadı: {e}")
    ekle(f"yt-dlp        : {shutil.which('yt-dlp') or 'yok (YouTube için gerekli)'}")
    ekle(f"ffmpeg        : {shutil.which('ffmpeg') or 'yok'}")

    hata_log = veri_klasoru() / "mpv-hata.log"
    ekle("")
    if hata_log.is_file():
        try:
            satirlar = hata_log.read_text(encoding="utf-8", errors="ignore").splitlines()
            ekle(f"Son mpv hataları ({hata_log}):")
            for h in satirlar[-12:]:
                ekle(f"   {h}")
        except Exception:
            pass
    else:
        ekle("mpv hata kaydı yok (henüz hata oluşmamış).")

    ekle("")
    ekle(f"Veri klasörü  : {veri_klasoru()}")
    return "\n".join(sat)


# ══════════════════════════════════════════════════════════════════
#  ORTAM KONTROLÜ
# ══════════════════════════════════════════════════════════════════
def ortam_kontrol() -> dict:
    d = {"python": sys.version.split()[0], "pyqt": None, "mpv": None,
         "libmpv": None, "requests": None, "eksik": []}
    try:
        from PyQt6.QtCore import QT_VERSION_STR
        d["pyqt"] = QT_VERSION_STR
    except Exception as e:
        d["eksik"].append(("PyQt6", str(e)))
    # Oynatıcı artık dış işlem: 'mpv' komutu yeterli, python-mpv gerekmez.
    import shutil as _sh
    yol = _sh.which("mpv")
    if yol:
        d["mpv"] = yol
        try:
            r = subprocess.run([yol, "--version"], capture_output=True,
                               text=True, timeout=6)
            ilk = (r.stdout or "").splitlines()
            d["libmpv"] = ilk[0].strip() if ilk else "?"
        except Exception:
            d["libmpv"] = "?"
    else:
        d["eksik"].append(("mpv", "mpv komutu bulunamadı"))
    try:
        import requests as _r
        d["requests"] = _r.__version__
    except Exception as e:
        d["eksik"].append(("requests", str(e)))
    return d


def youtube_tanisi(url: str) -> int:
    """
    YouTube oynatma sorununu adım adım teşhis eder.

    Her aşama ayrı ayrı denenir ki sorunun tam olarak nerede olduğu
    görülsün: yt-dlp mi, ağ mı, mpv mi?
    """
    import subprocess
    import time as _t

    print()
    print(f"MediaBox Qt {APP_SURUM} — YouTube tanısı")
    print("─" * 58)
    print(f"  Adres: {url}")
    print()

    # 1 — yt-dlp bulundu mu, sürümü ne?
    try:
        from mpv_islem import ytdl_var_mi, ytdl_komutu, mpv_ytdl_hook_var
    except Exception as e:
        print(f"  ✗ mpv_islem yüklenemedi: {e}")
        return 1
    y = ytdl_var_mi()
    print(f"  1) yt-dlp        : {y or '✗ BULUNAMADI'}")
    if not y:
        print("     → sudo pacman -S yt-dlp")
        return 1
    try:
        sv = subprocess.run(ytdl_komutu() + ["--version"], capture_output=True,
                            text=True, timeout=20).stdout.strip()
        print(f"     sürüm        : {sv}")
        yil = int((sv or "0").split(".")[0])
        if yil and yil < 2026:
            print("     ⚠ ESKİ — YouTube sık değişir:  pip install -U yt-dlp")
    except Exception:
        pass
    print(f"  2) mpv ytdl_hook : {'var' if mpv_ytdl_hook_var() else 'yok'}")

    # 3 — çözümleme
    from mpv_islem import ytdl_coz
    t0 = _t.time()
    c, hata = ytdl_coz(url, zaman_asimi=90)
    print(f"  3) Çözümleme     : {_t.time() - t0:.1f} sn", end="  ")
    if not c:
        print("✗ BAŞARISIZ")
        print(f"     {(hata or '')[:300]}")
        return 1
    print("✓")
    print(f"     başlık       : {c['baslik'][:46]}")
    import re as _re
    m = _re.search(r"itag=(\d+)", c["url"] or "")
    print(f"     biçim (itag) : {m.group(1) if m else '?'}"
          f"{'  ← AV1/4K, takılabilir' if m and m.group(1) in ('401', '571', '402') else ''}")
    print(f"     ayrı ses     : {'evet' if c.get('ses_url') else 'hayır (birleşik)'}")

    # 4 — akış adresine erişim
    try:
        import requests
        bsl = dict(c.get("basliklar") or {})
        r = requests.get(c["url"], timeout=25, stream=True,
                         headers={**bsl, "Range": "bytes=0-2047"})
        n = len(r.raw.read(2048))
        r.close()
        print(f"  4) Akışa erişim  : HTTP {r.status_code}, {n} bayt", end="  ")
        if r.status_code in (200, 206) and n > 100:
            print("✓")
        else:
            print("✗")
            if r.status_code == 403:
                print("     → CDN bu bağlantıyı reddediyor (IP/bölge engeli ya da")
                print("       yt-dlp sürümü eski). VPN veya güncelleme deneyin.")
            return 1
    except Exception as e:
        print(f"  4) Akışa erişim  : ✗ {type(e).__name__}: {str(e)[:70]}")
        return 1

    # 5 — mpv gerçekten oynatıyor mu?
    try:
        from mpv_islem import MpvIslem
        o = MpvIslem(log_yolu=str(veri_klasoru() / "yt-tani.log"))
        if not o.baslat(wid=None, hwdec=False, ek_argumanlar=["--ao=null"]):
            print(f"  5) mpv           : ✗ başlatılamadı ({o.son_hata()})")
            return 1
        ek = {}
        if c.get("ses_url"):
            ek["audio-files"] = c["ses_url"]
        # DÜZELTME: Referer hiç ayarlanmıyordu — googlevideo.com'un çoğu
        # imzalı adresi bu başlık olmadan 403 Forbidden döndürüyor (asıl
        # bug buydu). Ayrıca `http-header-fields` artık virgülle değil,
        # filtresiz bir JSON dizisi olarak gönderiliyor (bkz. arayuz.py).
        if c.get("referer"):
            ek["referrer"] = c["referer"]
        b2 = dict(c.get("basliklar") or {})
        ua = b2.pop("User-Agent", "")
        if ua:
            ek["user-agent"] = ua
        baslik_listesi = [f"{k}: {v}" for k, v in b2.items() if k and v]
        if baslik_listesi:
            from mpv_islem import mpv_liste_kodla
            ek["http-header-fields"] = mpv_liste_kodla(baslik_listesi)
        o.oynat(c["url"], ek)
        oynadi = 0.0
        for _ in range(25):
            _t.sleep(1)
            k = o.ozellik("time-pos")
            if k and k > 0.5:
                oynadi = k
                break
        o.kapat()
        if oynadi:
            print(f"  5) mpv oynatma   : ✓ çalışıyor (konum {oynadi:.1f} sn)")
            print()
            print("  ✓ YouTube oynatma SORUNSUZ.")
            return 0
        print("  5) mpv oynatma   : ✗ 25 sn içinde başlamadı")
        print(f"     Ayrıntı: {veri_klasoru() / 'yt-tani.log'}")
        return 1
    except Exception as e:
        print(f"  5) mpv           : ✗ {type(e).__name__}: {str(e)[:70]}")
        return 1


def ortam_raporu() -> bool:
    d = ortam_kontrol()
    print(f"\n{APP_ADI} Qt {APP_SURUM} — ortam kontrolü")
    print("─" * 58)
    print(f"  Python     : {d['python']}")
    print(f"  PyQt6/Qt   : {d['pyqt'] or '✗ yok'}")
    print(f"  mpv        : {d['mpv'] or '✗ yok'}")
    print(f"  mpv sürümü : {d['libmpv'] or '✗ yok'}")
    print(f"  requests   : {d['requests'] or '✗ yok'}")
    # YouTube ve benzeri siteler yt-dlp gerektirir; eksikse burada görünsün.
    try:
        from mpv_islem import ytdl_var_mi, mpv_ytdl_hook_var
        y = ytdl_var_mi()
        if y:
            import subprocess as _sp
            try:
                sv = _sp.run([y, "--version"], capture_output=True, text=True,
                             timeout=10).stdout.strip()
            except Exception:
                sv = ""
            print(f"  yt-dlp     : {y}{('  (' + sv + ')') if sv else ''}")
            try:
                # Eski sürüm YouTube'da hata verir; kullanıcı bilsin.
                yil = int((sv or "0").split(".")[0])
                if yil and yil < 2026:
                    print("               ⚠ eski sürüm — YouTube/fragman "
                          "çalışmayabilir:  pip install -U yt-dlp")
            except Exception:
                pass
        else:
            print("  yt-dlp     : ✗ yok  → YouTube bağlantıları açılmaz")
        print(f"  mpv ytdl   : {'var (gömülü)' if mpv_ytdl_hook_var() else 'yok'}")
    except Exception:
        pass
    try:
        from onizleme import ffmpeg_var_mi
        print(f"  Önizleme   : {'hazır' if ffmpeg_var_mi() else '✗ ffmpeg yok'}")
        from shader import YERLESIK, shader_klasoru
        print(f"  Shader     : {len(YERLESIK)} yerleşik  ·  {shader_klasoru()}")
    except Exception:
        pass
    try:
        from kumanda import qr_var_mi, yerel_ip
        if qr_var_mi():
            try:
                from kumanda import qr_png
                qr_durum = "QR hazır" if qr_png("test") else \
                    "QR için Pillow gerekli → sudo pacman -S python-pillow"
            except Exception:
                qr_durum = "QR denenemedi"
        else:
            qr_durum = "QR yok → sudo pacman -S python-qrcode python-pillow"
        print(f"  Kumanda    : http://{yerel_ip()}:8790  ({qr_durum})")
    except Exception:
        pass
    print(f"  Veri       : {veri_klasoru()}")
    if d["eksik"]:
        print("\n  ⚠ Eksikler:")
        for ad, hata in d["eksik"]:
            print(f"      {ad}: {hata.splitlines()[0][:80]}")
        print("\n  CachyOS / Arch:")
        print("      sudo pacman -S python-pyqt6 mpv python-requests")
        print("\n  Debian / Ubuntu:")
        print("      sudo apt install python3-pyqt6 mpv python3-requests")
        return False
    print("\n  ✓ Her şey hazır.")
    return True


# ══════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser(
        description=f"{APP_ADI} Qt — masaüstü medya merkezi",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Örnekler:\n"
               "  python mediabox_qt.py\n"
               "  python mediabox_qt.py --check\n"
               "  python mediabox_qt.py --ice-aktar yedek.json\n")
    ap.add_argument("--check", action="store_true", help="Ortamı kontrol et, pencere açma")
    ap.add_argument("--tani", action="store_true",
                    help="Oynatıcı tanısı (çökme sorunları için)")
    ap.add_argument("--yt-tani", metavar="URL", nargs="?", const="AUTO",
                    help="YouTube oynatma sorununu adım adım teşhis et")
    ap.add_argument("--ice-aktar", metavar="JSON", help="HTML sürümünden veri aktar")
    ap.add_argument("--m3u", metavar="DOSYA", help="Açılışta bir M3U yükle")
    a = ap.parse_args()

    if a.tani:
        print(oynatici_tanisi())
        return 0

    if getattr(a, "yt_tani", None):
        url = a.yt_tani if a.yt_tani != "AUTO" else \
            "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
        return youtube_tanisi(url)

    if a.check:
        return 0 if ortam_raporu() else 1

    if a.ice_aktar:
        depo = Depo()
        r = html_verisi_ice_aktar(depo, a.ice_aktar)
        if not r.get("ok"):
            print(f"✗ {r.get('hata')}", file=sys.stderr)
            return 1
        print(f"✓ Aktarıldı: {r['icerik']} içerik, {r['favori']} favori, "
              f"{r['ilerleme']} izleme kaydı, {r['kaynak']} kaynak, {r['etiket']} etiket")
        return 0

    d = ortam_kontrol()
    if d["eksik"]:
        ortam_raporu()
        return 1

    from arayuz import uygulamayi_baslat   # ikinci parça
    return uygulamayi_baslat(a)


if __name__ == "__main__":
    sys.exit(main())

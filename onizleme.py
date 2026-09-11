#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MediaBox — Zaman Çubuğu Önizlemesi ("nerede kalmıştım")
========================================================
Kullanıcı ilerleme çubuğunun üzerine geldiğinde o saniyedeki kareyi
küçük bir balonda gösterir.

NASIL ÇALIŞIR
    ffmpeg ile TEK kare çıkarılır. Hız için `-ss` girdi dosyasından
    ÖNCE verilir (anahtar kareye atlar):

        ölçüm:  -ss girdiden önce  → 0,111 sn
                -ss girdiden sonra → 0,660 sn   (6 kat yavaş)

    Sonuçlar bellekte önbelleğe alınır; aynı saniye ikinci kez
    istendiğinde ffmpeg hiç çalıştırılmaz.

SINIRLAR
    • Ağ akışlarında (HLS/DASH) kare çıkarmak yavaş olabilir; bu yüzden
      uzak adreslerde önizleme varsayılan olarak KAPALIDIR.
    • ffmpeg yoksa modül sessizce devre dışı kalır.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time

# Aynı anda kaç ffmpeg çalışsın (çubukta hızlı gezinirken birikmesin)
_EN_FAZLA_ES_ZAMANLI = 2
_kilit = threading.Semaphore(_EN_FAZLA_ES_ZAMANLI)


def ffmpeg_var_mi() -> str:
    return shutil.which("ffmpeg") or ""


def yerel_mi(url: str) -> bool:
    """Dosya yerel mi? (uzak akışlarda önizleme pahalı olur)"""
    if not url:
        return False
    u = url.lower()
    if u.startswith("file://"):
        return True
    if "://" in u:
        return False
    return os.path.exists(url)


class OnizlemeUretici:
    """
    Belirli saniyelerin karesini üretir ve önbellekte tutar.

    Kullanım:
        ur = OnizlemeUretici(en=200)
        ur.ayarla("/yol/film.mkv", 5400)
        yol = ur.iste(1230, geri_cagir)     # hazırsa yol döner, yoksa None
    """

    def __init__(self, en: int = 200, adim: int = 5):
        self.en = en
        self.adim = max(1, adim)          # kaç saniyede bir kare (yuvarlama)
        self.kaynak = ""
        self.sure = 0.0
        self._onbellek: dict[int, str] = {}
        self._bekleyen: set[int] = set()
        self._klasor = ""
        self._kapali = not bool(ffmpeg_var_mi())

    # ── yaşam döngüsü ──
    def ayarla(self, kaynak: str, sure: float):
        """Yeni dosyaya geçildi: önbelleği temizle."""
        if kaynak == self.kaynak:
            self.sure = sure or self.sure
            return
        self.temizle()
        self.kaynak = kaynak or ""
        self.sure = sure or 0.0

    def kullanilabilir(self) -> bool:
        return (not self._kapali) and bool(self.kaynak) and yerel_mi(self.kaynak)

    def temizle(self):
        self._onbellek.clear()
        self._bekleyen.clear()
        if self._klasor and os.path.isdir(self._klasor):
            shutil.rmtree(self._klasor, ignore_errors=True)
        self._klasor = ""

    def _klasoru_al(self) -> str:
        if not self._klasor:
            self._klasor = tempfile.mkdtemp(prefix="mediabox-onizleme-")
        return self._klasor

    # ── üretim ──
    def _yuvarla(self, saniye: float) -> int:
        """Yakın saniyeleri aynı kareye eşle (gereksiz üretimi önler)."""
        s = max(0, int(saniye))
        return (s // self.adim) * self.adim

    def hazir_mi(self, saniye: float) -> str:
        """Önbellekte varsa dosya yolu, yoksa ''."""
        return self._onbellek.get(self._yuvarla(saniye), "")

    def iste(self, saniye: float, geri_cagir=None) -> str:
        """
        Kareyi ister. Hazırsa yolu hemen döner.
        Değilse arka planda üretilir ve `geri_cagir(saniye, yol)` çağrılır.
        """
        if not self.kullanilabilir():
            return ""
        anahtar = self._yuvarla(saniye)
        hazir = self._onbellek.get(anahtar)
        if hazir:
            return hazir
        if anahtar in self._bekleyen:
            return ""
        self._bekleyen.add(anahtar)
        threading.Thread(target=self._uret, args=(anahtar, geri_cagir),
                         daemon=True).start()
        return ""

    def _uret(self, saniye: int, geri_cagir):
        yol = os.path.join(self._klasoru_al(), f"k{saniye}.jpg")
        with _kilit:
            # Kullanıcı çoktan başka yere gitmiş olabilir; yine de üretmek
            # ucuz (≈0,1 sn) ve önbelleğe girer.
            try:
                subprocess.run(
                    [ffmpeg_var_mi(), "-y", "-loglevel", "error",
                     "-ss", str(saniye), "-i", self.kaynak,
                     "-frames:v", "1", "-vf", f"scale={self.en}:-1",
                     "-q:v", "6", yol],
                    capture_output=True, timeout=12)
            except Exception:
                yol = ""
        self._bekleyen.discard(saniye)
        if yol and os.path.exists(yol) and os.path.getsize(yol) > 100:
            self._onbellek[saniye] = yol
            if geri_cagir:
                try:
                    geri_cagir(saniye, yol)
                except Exception:
                    pass

    def onbellek_boyutu(self) -> int:
        return len(self._onbellek)

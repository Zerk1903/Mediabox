#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MediaBox — TMDB Detay Sayfası, Oyuncu Sayfası ve Sağlayıcı Yöneticisi
=======================================================================
anapencere.py tarafından kullanılır.

İÇERİK
    ZenginDetay      : arka plan görseli + afiş + özet + puan + tür +
                       oyuncu kadrosu + benzer içerikler + bölüm listesi
    OyuncuSayfasi    : oyuncu biyografisi + filmografi
    SaglayiciYonetici: kullanıcının kendi URL şablonlarını yönetmesi
    TmdbEsleDialog   : yanlış eşleşmeyi elle düzeltme
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize
from PyQt6.QtGui import QPixmap, QFont, QPainter, QColor, QLinearGradient, QBrush
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                             QScrollArea, QFrame, QListWidget, QListWidgetItem,
                             QDialog, QLineEdit, QComboBox, QMessageBox, QGridLayout,
                             QCheckBox, QApplication, QSizePolicy, QTextEdit,
                             QPlainTextEdit, QInputDialog, QFileDialog, QMenu)

from tmdb import afis_url, gorsel_indir, AFIS_BOY, AFIS_BUYUK, ARKA_BOY, KISI_BOY
from arayuz import (R_ARKA, R_YUZEY, R_YUZEY2, R_CIZGI, R_METIN, R_SOLUK, R_VURGU,
                    KART_EN, KART_BOY, sure_yaz, renk_uret)
import arayuz


# ══════════════════════════════════════════════════════════════════
#  ARKA PLANDA TMDB SORGUSU
# ══════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════
#  ÇALIŞAN İŞ PARÇACIĞI KÜTÜĞÜ
#  Bir QThread çalışırken Python tarafındaki son referansı kaybolursa
#  Qt "Destroyed while thread is still running" deyip UYGULAMAYI ÇÖKERTİR.
#  (Ölçüldü: liste yeniden atanınca çöküyor.) Bu yüzden başlayan her işçi
#  burada tutulur, bitince kendini çıkarır.
# ══════════════════════════════════════════════════════════════════
_CALISANLAR: set = set()
_BEKLEYENLER: list = []
EN_FAZLA_ISCI = 12          # aynı anda çalışacak QThread sayısı


def _isci_kuyrugu_isle():
    """Yer açıldıkça bekleyen işçileri başlatır."""
    while _BEKLEYENLER and len(_CALISANLAR) < EN_FAZLA_ISCI:
        isci = _BEKLEYENLER.pop(0)
        try:
            if isci.isRunning():
                continue
            _CALISANLAR.add(isci)
            isci.start()
        except RuntimeError:
            # Nesne çoktan silinmiş (sayfa değişmiş) — atla.
            _CALISANLAR.discard(isci)


def _isci_bitti(isci):
    _CALISANLAR.discard(isci)
    _isci_kuyrugu_isle()


def isci_baslat(isci: QThread):
    """
    İşçiyi kütüğe alıp başlatır; bitince kütükten düşer.

    ÇÖKME KORUMASI — İŞ PARÇACIĞI SAYISI SINIRI
        Her TMDB/görsel isteği ayrı bir QThread açıyordu ve sayı sınırsızdı.
        Arama sırasında yüzlerce kart oluşup silindiği için bu sayı hızla
        patlıyor (ölçüldü: gerçek arama akışında AYNI ANDA 138 QThread,
        toplam 493 işçi). Her QThread işletim sisteminden yığın alanı ve
        çekirdek kaynağı ister; sınır aşılınca `start()` sessizce başarısız
        olur ya da uygulama bir anda kapanır — kullanıcının gördüğü
        "sonuçlar gelirken program yok oldu" tam olarak budur.

        Artık en fazla EN_FAZLA_ISCI kadar işçi çalışır; gerisi kuyrukta
        bekler ve yer açıldıkça sırayla başlar.
    """
    try:
        isci.finished.connect(lambda i=isci: _isci_bitti(i))
    except Exception:
        pass
    if len(_CALISANLAR) < EN_FAZLA_ISCI:
        _CALISANLAR.add(isci)
        try:
            isci.start()
        except RuntimeError:
            _CALISANLAR.discard(isci)
    else:
        # Kuyruk da sınırsız büyümesin: en eski bekleyenler düşürülür
        # (kullanıcı çoktan başka sayfaya geçmiştir).
        if len(_BEKLEYENLER) > 300:
            del _BEKLEYENLER[:150]
        _BEKLEYENLER.append(isci)
    return isci


class TmdbIsci(QThread):
    """Tek bir içerik için TMDB verisini arka planda çeker."""
    hazir = pyqtSignal(object)      # detay sözlüğü ya da None
    hata = pyqtSignal(str)

    def __init__(self, istemci, ad: str, yil: str, dizi: bool, tmdb_id: int = 0):
        super().__init__()
        self.istemci, self.ad, self.yil, self.dizi = istemci, ad, yil, dizi
        self.tmdb_id = tmdb_id

    def run(self):
        try:
            if not self.istemci or not self.istemci.hazir:
                self.hata.emit("TMDB anahtarı girilmemiş")
                return
            kid = self.tmdb_id
            if not kid:
                bulunan = self.istemci.ara(self.ad, self.yil, self.dizi)
                if not bulunan:
                    self.hata.emit("TMDB'de eşleşme bulunamadı")
                    return
                kid = bulunan.get("id")
            d = self.istemci.detay(kid, self.dizi)
            if d:
                self.hazir.emit(d)
            else:
                self.hata.emit(self.istemci.son_hata or "Detay alınamadı")
        except Exception as e:
            self.hata.emit(f"{type(e).__name__}: {e}")


class GorselIsci(QThread):
    """Görseli arka planda indirir."""
    hazir = pyqtSignal(str, bytes)

    def __init__(self, url: str, klasor, etiket: str = ""):
        super().__init__()
        self.url, self.klasor, self.etiket = url, klasor, etiket

    def run(self):
        v = gorsel_indir(self.url, self.klasor)
        if v:
            self.hazir.emit(self.etiket, v)


# ══════════════════════════════════════════════════════════════════
#  BÖLÜM SATIRI (zengin bölüm rehberi)
# ══════════════════════════════════════════════════════════════════
class BolumSatiri(QFrame):
    """
    Tek bir bölümü gösteren satır: kare görsel, numara, Türkçe ad, özet,
    puan, yayın tarihi ve izleme ilerlemesi.

    TMDB bilgisi yoksa (eşleşme kurulamadıysa) dosya adına düşer — satır
    yine de çalışır, sadece görsel/özet boş kalır.
    """
    GORSEL_EN, GORSEL_BOY = 152, 86

    def __init__(self, icerik, sira: int, bilgi: dict | None, depo, klasor,
                 oynat_cb, parent=None):
        super().__init__(parent)
        self.icerik, self.sira, self.bilgi = icerik, sira, bilgi or {}
        self.depo, self.klasor, self._oynat_cb = depo, klasor, oynat_cb
        self._px = None
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setFixedHeight(self.GORSEL_BOY + 18)
        self.setStyleSheet(
            f"QFrame{{background:{R_YUZEY};border:1px solid {R_CIZGI};border-radius:10px;}}")

        y = QHBoxLayout(self)
        y.setContentsMargins(9, 9, 14, 9)
        y.setSpacing(14)

        self.g_etiket = QLabel()
        self.g_etiket.setFixedSize(self.GORSEL_EN, self.GORSEL_BOY)
        self.g_etiket.setStyleSheet(
            f"background:{R_YUZEY2};border:0;border-radius:7px;")
        self.g_etiket.setAlignment(Qt.AlignmentFlag.AlignCenter)
        y.addWidget(self.g_etiket)

        sag = QVBoxLayout()
        sag.setSpacing(3)
        sag.setContentsMargins(0, 2, 0, 2)

        sb = icerik.sezon_bolum()
        no = f"S{sb[0]:02d}E{sb[1]:02d}" if sb else f"#{sira + 1}"
        ad = (self.bilgi.get("name") or "").strip()
        if not ad:
            from mediabox_qt import temiz_baslik
            ad = temiz_baslik(icerik.ad) or icerik.ad
        ust = QLabel(f"<span style='color:{R_SOLUK};font-weight:700'>{no}</span>"
                     f"&nbsp;&nbsp; <b>{ad[:70]}</b>")
        ust.setStyleSheet("font-size:13px;border:0;background:transparent;")
        sag.addWidget(ust)

        par = []
        if self.bilgi.get("air_date"):
            par.append(self.bilgi["air_date"])
        if self.bilgi.get("vote_average"):
            par.append(f"★ {self.bilgi['vote_average']:.1f}")
        if self.bilgi.get("runtime"):
            par.append(f"{self.bilgi['runtime']} dk")
        if icerik.kalite():
            par.append(icerik.kalite())
        if par:
            m = QLabel("   ·   ".join(par))
            m.setStyleSheet(f"color:{R_SOLUK};font-size:11px;border:0;background:transparent;")
            sag.addWidget(m)

        oz = (self.bilgi.get("overview") or "").strip()
        if oz:
            o = QLabel(oz[:190] + ("…" if len(oz) > 190 else ""))
            o.setWordWrap(True)
            o.setStyleSheet("color:#b9bec9;font-size:11px;border:0;background:transparent;")
            o.setMaximumHeight(32)
            sag.addWidget(o)
        sag.addStretch()
        y.addLayout(sag, 1)

        b = QPushButton("▶")
        b.setFixedSize(38, 38)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        v = depo.ayarlar.get("vurgu", R_VURGU)
        b.setStyleSheet(
            f"QPushButton{{background:{v};border:0;border-radius:19px;"
            f"color:#fff;font-size:14px;font-weight:700;}}"
            f"QPushButton:hover{{background:#f6121d;}}")
        b.clicked.connect(lambda: self._oynat_cb())
        y.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)

        yol = self.bilgi.get("still_path")
        if yol:
            self._isci = GorselIsci(afis_url(yol, "w300"), klasor)
            self._isci.hazir.connect(self._gorsel_geldi)
            isci_baslat(self._isci)

    def _gorsel_geldi(self, _e, veri):
        px = QPixmap()
        if px.loadFromData(veri) and not px.isNull():
            try:
                self.g_etiket.setPixmap(px.scaled(
                    self.GORSEL_EN, self.GORSEL_BOY,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation))
            except RuntimeError:
                pass

    def mouseDoubleClickEvent(self, e):
        self._oynat_cb()

    def enterEvent(self, e):
        self._hover = True
        self.setStyleSheet(
            f"QFrame{{background:{R_YUZEY2};border:1px solid #3a4050;border-radius:10px;}}")

    def leaveEvent(self, e):
        self._hover = False
        self.setStyleSheet(
            f"QFrame{{background:{R_YUZEY};border:1px solid {R_CIZGI};border-radius:10px;}}")

    def paintEvent(self, e):
        super().paintEvent(e)
        # İzleme ilerlemesi: satırın altında ince çubuk
        pr = self.depo.ilerleme.get(self.icerik.url)
        if not (pr and pr.get("d")):
            return
        oran = max(0.02, min(1.0, pr["t"] / pr["d"]))
        p = QPainter(self)
        r = self.rect()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 120))
        p.drawRect(1, r.height() - 4, r.width() - 2, 3)
        p.setBrush(QColor(self.depo.ayarlar.get("vurgu", R_VURGU)))
        p.drawRect(1, r.height() - 4, int((r.width() - 2) * oran), 3)
        if oran > 0.92:
            p.setPen(QColor(120, 200, 140))
            f = QFont(); f.setPointSize(8); f.setBold(True); p.setFont(f)
            p.drawText(r.adjusted(0, 0, -58, 0),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "✓")
        p.end()


# ══════════════════════════════════════════════════════════════════
#  OYUNCU KARTI
# ══════════════════════════════════════════════════════════════════
class OyuncuKarti(QFrame):
    def __init__(self, kisi: dict, klasor, tiklandi=None, parent=None):
        super().__init__(parent)
        self.kisi, self._tiklandi = kisi, tiklandi
        self._px = None
        self.setFixedSize(112, 178)
        if tiklandi:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        yol = kisi.get("profile_path")
        if yol:
            self._isci = GorselIsci(afis_url(yol, KISI_BOY), klasor)
            self._isci.hazir.connect(self._geldi)
            isci_baslat(self._isci)

    def _geldi(self, _e, veri):
        px = QPixmap()
        if px.loadFromData(veri) and not px.isNull():
            self._px = px
            self.update()

    def mousePressEvent(self, e):
        if self._tiklandi and e.button() == Qt.MouseButton.LeftButton:
            self._tiklandi(self.kisi)

    def paintEvent(self, e):
        from PyQt6.QtGui import QPainterPath
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        foto = r.adjusted(0, 0, 0, -46)
        pp = QPainterPath()
        pp.addRoundedRect(float(foto.x()), float(foto.y()),
                          float(foto.width()), float(foto.height()), 9, 9)
        p.setClipPath(pp)
        if self._px:
            ol = self._px.scaled(foto.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                 Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(foto.x() - (ol.width() - foto.width()) // 2, foto.y(), ol)
        else:
            p.fillRect(foto, renk_uret(self.kisi.get("name", "?")))
            p.setPen(QColor(255, 255, 255, 45))
            f = QFont(); f.setPointSize(19); f.setBold(True); p.setFont(f)
            bas = "".join(w[0] for w in (self.kisi.get("name") or "?").split()[:2]).upper()
            p.drawText(foto, Qt.AlignmentFlag.AlignCenter, bas or "?")
        p.setClipping(False)

        p.setPen(QColor(R_METIN))
        f = QFont(); f.setPointSize(8); f.setBold(True); p.setFont(f)
        ad = p.fontMetrics().elidedText(self.kisi.get("name", ""),
                                        Qt.TextElideMode.ElideRight, r.width() - 6)
        p.drawText(3, foto.bottom() + 15, ad)

        rol = self.kisi.get("character") or self.kisi.get("job") or ""
        if rol:
            p.setPen(QColor(R_SOLUK))
            f2 = QFont(); f2.setPointSize(7); p.setFont(f2)
            rr = p.fontMetrics().elidedText(rol, Qt.TextElideMode.ElideRight, r.width() - 6)
            p.drawText(3, foto.bottom() + 30, rr)
        p.end()


# ══════════════════════════════════════════════════════════════════
#  ZENGİN DETAY SAYFASI
# ══════════════════════════════════════════════════════════════════
class ZenginDetay(QWidget):
    """
    Arka plan görseli, afiş, özet, puan, tür, oyuncu kadrosu ve
    benzer içeriklerle tam detay sayfası.
    """
    oyuncu_ac = pyqtSignal(dict)
    icerik_ac = pyqtSignal(dict)

    def __init__(self, depo, istemci, gorsel_klasor, oynat_cb, geri_cb, parent=None):
        super().__init__(parent)
        self.depo, self.istemci = depo, istemci
        self.klasor = gorsel_klasor
        self._oynat, self._geri = oynat_cb, geri_cb
        self.grup = None
        self.detay_veri = None
        self._arka_px = None
        self._isciler = []

        ana = QVBoxLayout(self); ana.setContentsMargins(0, 0, 0, 0); ana.setSpacing(0)

        ust = QHBoxLayout(); ust.setContentsMargins(20, 12, 20, 6)
        b = QPushButton("‹  Geri"); b.setFixedSize(90, 32)
        b.clicked.connect(lambda: self._geri())
        ust.addWidget(b); ust.addStretch()
        self.b_tmdb = QPushButton("🔍  TMDB eşleşmesini düzelt")
        self.b_tmdb.setFixedHeight(32); self.b_tmdb.clicked.connect(self._esle_ac)
        ust.addWidget(self.b_tmdb)
        ana.addLayout(ust)

        self.kaydir = QScrollArea(); self.kaydir.setWidgetResizable(True)
        self.kaydir.setStyleSheet("background:transparent;border:0;")
        self.kaydir.viewport().setStyleSheet("background:transparent;")
        self.ic = QWidget()
        self.ic.setStyleSheet("background:transparent;")
        self.iv = QVBoxLayout(self.ic)
        self.iv.setContentsMargins(0, 0, 0, 30); self.iv.setSpacing(18)
        self.kaydir.setWidget(self.ic)
        ana.addWidget(self.kaydir, 1)

    @staticmethod
    def _golge(w, yaricap: int = 12):
        """Metni parlak arka planda okunur kılar."""
        from PyQt6.QtWidgets import QGraphicsDropShadowEffect
        ef = QGraphicsDropShadowEffect(w)
        ef.setBlurRadius(yaricap); ef.setOffset(0, 2)
        ef.setColor(QColor(0, 0, 0, 235))
        w.setGraphicsEffect(ef)

    # ── arka plan çizimi ───────────────────────────────────────────
    def paintEvent(self, e):
        p = QPainter(self)
        r = self.rect()
        if self._arka_px:
            ol = self._arka_px.scaled(QSize(r.width(), int(r.width() * 0.56)),
                                      Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                      Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(0, 0, ol)
            # Üst kısım açık kalsın ki afiş görünsün; yazı alanı aşağıda koyulaşır
            g = QLinearGradient(0, 0, 0, ol.height())
            g.setColorAt(0.0, QColor(11, 12, 16, 120))
            g.setColorAt(0.55, QColor(11, 12, 16, 205))
            g.setColorAt(0.85, QColor(11, 12, 16, 246))
            g.setColorAt(1.0, QColor(11, 12, 16, 255))
            p.fillRect(0, 0, r.width(), ol.height(), QBrush(g))
            # Yazıların bulunduğu SOL bölge ayrıca koyulaştırılır; aksi halde
            # açık renkli afişlerde (örn. Joker) metin okunmuyordu.
            g2 = QLinearGradient(0, 0, r.width() * 0.78, 0)
            g2.setColorAt(0.0, QColor(11, 12, 16, 216))
            g2.setColorAt(0.55, QColor(11, 12, 16, 150))
            g2.setColorAt(1.0, QColor(11, 12, 16, 0))
            p.fillRect(0, 0, r.width(), ol.height(), QBrush(g2))
            p.fillRect(0, ol.height(), r.width(), r.height() - ol.height(), QColor(R_ARKA))
        else:
            p.fillRect(r, QColor(R_ARKA))
        p.end()

    # ── fragman (trailer) ──────────────────────────────────────────
    def _fragman_iste(self, tmdb_id: int, dizi: bool):
        """Fragman listesini arka planda getirir."""
        nesil = getattr(self, "_fragman_nesil", 0) + 1
        self._fragman_nesil = nesil

        class _Isci(QThread):
            hazir = pyqtSignal(int, object)

            def __init__(self, ist, tid, dz, n):
                super().__init__()
                self.ist, self.tid, self.dz, self.n = ist, tid, dz, n

            def run(self):
                try:
                    from fragman import fragmanlari_getir
                    self.hazir.emit(self.n, fragmanlari_getir(self.ist, self.tid, self.dz))
                except Exception:
                    self.hazir.emit(self.n, [])

        i = _Isci(self.istemci, tmdb_id, dizi, nesil)
        i.hazir.connect(self._fragman_geldi)
        self._isciler.append(i)
        isci_baslat(i)

    def _fragman_geldi(self, nesil: int, liste):
        # Kullanıcı başka içeriğe geçmişse bu sonuç bayattır
        if nesil != getattr(self, "_fragman_nesil", 0):
            return
        try:
            self._fragmanlar = liste or []
            n = len(self._fragmanlar)
            if not hasattr(self, "b_fragman"):
                return          # sayfa henüz çizilmemiş
            self.b_fragman.setVisible(n > 0)
            if n:
                ilk = self._fragmanlar[0]
                self.b_fragman.setText("▶  Fragman" + (f"  ({n})" if n > 1 else ""))
                self.b_fragman.adjustSize()
                self.b_fragman.setFixedHeight(42)
                self.b_fragman.setToolTip(
                    f"{ilk['ad']}\n(Oynatıcıda açılır — sağ tık: tümünü listele)")
        except RuntimeError:
            pass          # sayfa değişmiş

    def _fragman_ac(self):
        """
        Fragmanı programın kendi oynatıcısında açar.

        Birden çok fragman varsa seçim menüsü gösterilir; tek fragman
        varsa doğrudan oynatılır.
        """
        liste = getattr(self, "_fragmanlar", []) or []
        if not liste:
            QMessageBox.information(self, "Fragman yok",
                                    "Bu yapım için TMDB'de fragman bulunamadı.")
            return
        if len(liste) == 1:
            self._fragman_oynat(liste[0])
            return

        from fragman import etiket
        m = QMenu(self)
        m.addAction("▶  " + etiket(liste[0])).setData(0)
        if len(liste) > 1:
            m.addSeparator()
        for i, f in enumerate(liste[1:12], start=1):
            m.addAction(etiket(f)).setData(i)
        sec = m.exec(self.b_fragman.mapToGlobal(
            self.b_fragman.rect().bottomLeft()))
        if sec is not None and sec.data() is not None:
            self._fragman_oynat(liste[sec.data()])

    def _fragman_oynat(self, f: dict):
        """Seçilen fragmanı oynatıcıya gönderir (yt-dlp ile çözülür)."""
        from mediabox_qt import Icerik
        from mpv_islem import ytdl_var_mi
        if not ytdl_var_mi():
            c = QMessageBox(self)
            c.setWindowTitle("yt-dlp gerekli")
            c.setText("Fragmanı oynatmak için yt-dlp gerekiyor.")
            c.setInformativeText(
                "CachyOS / Arch:   sudo pacman -S yt-dlp\n"
                "Debian / Ubuntu:  sudo apt install yt-dlp\n\n"
                "Alternatif olarak tarayıcıda açabilirsiniz.")
            b_tar = c.addButton("Tarayıcıda aç", QMessageBox.ButtonRole.AcceptRole)
            c.addButton("Kapat", QMessageBox.ButtonRole.RejectRole)
            c.exec()
            if c.clickedButton() is b_tar:
                import webbrowser
                webbrowser.open(f["url"])
            return

        baslik = self.l_bas.text() or "Fragman"
        oge = Icerik(
            ad=f"{baslik} — {f['ad']}"[:90],
            url=f["url"],
            kategori="movie",
            grup="Fragman",
        )
        # Fragman izleme geçmişini kirletmesin
        oge.tmdb_id = 0
        self._oynat(oge, [oge], 0)

    # ── izlemeye devam ─────────────────────────────────────────────
    def _devam_bolumu(self, grup: dict):
        """
        Oynat'a basılınca hangi bölüm açılmalı? (içerik, sıra) döndürür.

        Öncelik:
          1. Kartın taşıdığı "_devam" bölümü (İzlemeye Devam rafından gelir)
          2. İlerleme kaydı olan EN SON bölüm (yarısı izlenmişse o, bitmişse
             bir sonraki)
          3. Hiç izlenmemiş ilk bölüm
          4. İlk bölüm
        """
        bolumler = grup.get("bolumler") or []
        if not bolumler:
            return None, 0

        hedef = grup.get("_devam")
        if hedef is not None:
            for i, b in enumerate(bolumler):
                if b.url == hedef.url:
                    return b, i

        ilerleme = getattr(self.depo, "ilerleme", {}) or {}
        en_son_i, en_son_z = -1, -1.0
        for i, b in enumerate(bolumler):
            p = ilerleme.get(b.url)
            if not p:
                continue
            z = p.get("zaman", p.get("z", 0)) or 0
            if z >= en_son_z:
                en_son_z, en_son_i = z, i

        if en_son_i >= 0:
            p = ilerleme.get(bolumler[en_son_i].url) or {}
            t, d = p.get("t", 0) or 0, p.get("d", 0) or 0
            # %92'den fazlası izlendiyse bölüm bitmiş sayılır → sonraki bölüm
            if d and t / d > 0.92 and en_son_i + 1 < len(bolumler):
                return bolumler[en_son_i + 1], en_son_i + 1
            return bolumler[en_son_i], en_son_i

        for i, b in enumerate(bolumler):
            if b.url not in ilerleme:
                return b, i
        return bolumler[0], 0

    # ── içerik ─────────────────────────────────────────────────────
    def goster(self, grup: dict):
        self.grup = grup
        self.detay_veri = None
        self._arka_px = None
        self._temizle()
        self._iskelet(grup)
        self.update()

        ilk = grup["bolumler"][0]
        dizi = (not grup["tekil"]) or ilk.kategori in ("series", "anime")
        if self.istemci and self.istemci.hazir:
            isci = TmdbIsci(self.istemci, grup["baslik"], ilk.yil(), dizi,
                            tmdb_id=getattr(ilk, "tmdb_id", 0) or 0)
            isci.hazir.connect(self._tmdb_geldi)
            isci.hata.connect(self._tmdb_hata)
            self._isciler.append(isci)
            isci_baslat(isci)
        else:
            self._bilgi.setText("ℹ TMDB anahtarı girilmemiş — afiş ve oyuncu bilgisi yok.\n"
                                "   ⚙ → TMDB API anahtarı")

    def _temizle(self):
        for i in reversed(range(self.iv.count())):
            it = self.iv.takeAt(i)
            w = it.widget() if it else None
            if w:
                # deleteLater ertelenir; koparmazsak eski içerik üstte kalıyor
                w.hide(); w.setParent(None); w.deleteLater()

    def _iskelet(self, grup):
        """TMDB gelmeden önce yerel bilgilerle sayfayı kur."""
        ilk = grup["bolumler"][0]
        ust = QWidget()
        ust.setStyleSheet("background:transparent;")   # backdrop görünsün
        uh = QHBoxLayout(ust)
        uh.setContentsMargins(28, 22, 28, 0); uh.setSpacing(24)

        self.afis = QLabel(); self.afis.setFixedSize(200, 300)
        self.afis.setStyleSheet(f"background:{R_YUZEY2};border-radius:12px;")
        self.afis.setScaledContents(True)
        uh.addWidget(self.afis, 0, Qt.AlignmentFlag.AlignTop)

        sag = QVBoxLayout(); sag.setSpacing(9)
        self.l_bas = QLabel(grup["baslik"])
        self.l_bas.setStyleSheet("font-size:30px;font-weight:800;background:transparent;")
        self.l_bas.setWordWrap(True)
        self._golge(self.l_bas, 18)
        sag.addWidget(self.l_bas)

        self.l_meta = QLabel("")
        self.l_meta.setStyleSheet("color:#d6dae4;font-size:12.5px;background:transparent;")
        self._golge(self.l_meta, 10)
        sag.addWidget(self.l_meta)

        self.l_tur = QLabel("")
        self.l_tur.setStyleSheet(f"color:{R_METIN};font-size:12px;background:transparent;")
        self._golge(self.l_tur, 10)
        sag.addWidget(self.l_tur)

        self.l_ozet = QLabel("")
        self.l_ozet.setWordWrap(True); self.l_ozet.setMaximumWidth(720)
        self.l_ozet.setStyleSheet("font-size:13px;background:transparent;line-height:1.5;")
        self._golge(self.l_ozet, 10)
        sag.addWidget(self.l_ozet)

        self._bilgi = QLabel("⏳ TMDB'den bilgiler alınıyor…")
        self._bilgi.setStyleSheet(f"color:{R_SOLUK};font-size:11.5px;background:transparent;")
        sag.addWidget(self._bilgi)

        dug = QHBoxLayout(); dug.setSpacing(9)

        # ── İZLEMEYE DEVAM DÜZELTMESİ ──────────────────────────────
        # Eskiden Oynat her zaman bolumler[0]'ı (S01E01) açıyordu; yarım
        # kalan bölümden devam edilemiyordu (ölçüldü: S02E04'te kalınmışken
        # S01E01 açılıyordu). Artık:
        #   1) kart "_devam" taşıyorsa o bölüm,
        #   2) yoksa ilerleme kaydı olan en son bölüm,
        #   3) o da yoksa ilk izlenmemiş bölüm seçilir.
        bas_ic, bas_sira = self._devam_bolumu(grup)
        sb = bas_ic.sezon_bolum()
        if bas_ic is not grup["bolumler"][0] and sb:
            b1 = QPushButton(f"▶  S{sb[0]:02d}E{sb[1]:02d} devam et")
            b1.setFixedSize(206, 42)      # metin sığmalı (ölçüldü: 178 kesiyordu)
        else:
            b1 = QPushButton("▶  Oynat")
            b1.setFixedSize(132, 42)
        b1.setStyleSheet(f"background:{self.depo.ayarlar.get('vurgu', R_VURGU)};"
                         "border:0;border-radius:8px;font-weight:700;font-size:14px;")
        b1.clicked.connect(
            lambda _=False, i=bas_ic, s=bas_sira: self._oynat(i, grup["bolumler"], s))
        fav = self.depo.favori_mi(ilk)
        self.b_fav = QPushButton("♥  Favorilerde" if fav else "♡  Favorilere ekle")
        self.b_fav.setFixedSize(158, 42)
        self.b_fav.clicked.connect(self._fav)
        b3 = QPushButton("📋  Bağlantı"); b3.setFixedSize(112, 42)

        def _baglanti_kopyala():
            QApplication.clipboard().setText(ilk.url)
            # Düğme sessizce kopyalıyordu; tıklayınca hiçbir geri bildirim
            # olmaması "çalışmıyor" hissi veriyordu. Artık kısa süreliğine
            # onay metni gösterip eski hâline dönüyor.
            b3.setText("✓  Kopyalandı"); b3.setEnabled(False)
            QTimer.singleShot(1400, lambda: (b3.setText("📋  Bağlantı"), b3.setEnabled(True)))

        b3.clicked.connect(_baglanti_kopyala)
        # ▶ Fragman — TMDB bilgisi gelince görünür olur (bkz. _tmdb_geldi)
        self.b_fragman = QPushButton("▶  Fragman")
        # Sabit genişlik metni kesiyordu (ölçüldü: "Fragman  (27" ).
        # Yükseklik sabit, genişlik içeriğe göre.
        self.b_fragman.setFixedHeight(42)
        self.b_fragman.setMinimumWidth(150)
        self.b_fragman.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_fragman.setToolTip("Fragmanı programın oynatıcısında izle")
        self.b_fragman.clicked.connect(self._fragman_ac)
        self.b_fragman.setVisible(False)
        self.b_oto = QPushButton("⚡  Otomatik URL ekle"); self.b_oto.setFixedSize(178, 42)
        self.b_oto.clicked.connect(self._oto_url)
        dug.addWidget(b1)
        dug.addWidget(self.b_fragman)
        dug.addWidget(self.b_fav); dug.addWidget(b3)
        dug.addWidget(self.b_oto); dug.addStretch()
        sag.addLayout(dug)
        sag.addStretch()
        uh.addLayout(sag, 1)
        self.iv.addWidget(ust)

        par = []
        if ilk.yil(): par.append(ilk.yil())
        if ilk.grup: par.append(ilk.grup)
        if ilk.kalite(): par.append(ilk.kalite())
        if not grup["tekil"]: par.append(f"{len(grup['bolumler'])} bölüm")
        self.l_meta.setText("   ·   ".join(par))

        if not grup["tekil"]:
            self._bolum_listesi(grup)

    def _bolum_listesi(self, grup):
        """
        Zengin bölüm rehberi: sezon sekmeleri + kare görsel + Türkçe ad/özet.

        Eskiden satırlar yalnızca "S02E04 · Breaking Bad" yazıyordu; hangi
        bölüm olduğu anlaşılmıyordu. TMDB sezon ucundan bölüm adı, özeti,
        puanı, yayın tarihi ve kare görseli çekiliyor (ölçüldü: Türkçe geliyor,
        sezon başına ~0,28 sn, önbellekten 0 sn).
        """
        bolumler = grup["bolumler"]
        bl = QLabel(f"Bölümler  ({len(bolumler)})")
        bl.setStyleSheet("font-size:17px;font-weight:700;padding-left:28px;background:transparent;")
        self.iv.addWidget(bl)

        # Sezonlara ayır
        sezonlar: dict[int, list] = {}
        for i, b in enumerate(bolumler):
            sb = b.sezon_bolum()
            sezonlar.setdefault(sb[0] if sb else 0, []).append((i, b))

        self._bolum_kap = QWidget()
        self._bolum_kap.setStyleSheet("background:transparent;")
        kv = QVBoxLayout(self._bolum_kap)
        kv.setContentsMargins(28, 0, 28, 0)
        kv.setSpacing(10)

        # Sezon sekmeleri (birden çok sezon varsa)
        self._sezon_dugmeleri = {}
        if len(sezonlar) > 1:
            sat = QHBoxLayout(); sat.setSpacing(8)
            for s in sorted(sezonlar):
                d = QPushButton(f"Sezon {s}" if s else "Diğer")
                d.setCheckable(True)
                d.setCursor(Qt.CursorShape.PointingHandCursor)
                d.setFixedHeight(30)
                d.clicked.connect(lambda _=False, sn=s: self._sezon_goster(sn))
                self._sezon_dugmeleri[s] = d
                sat.addWidget(d)
            sat.addStretch()
            kv.addLayout(sat)

        self._bolum_liste_kap = QVBoxLayout()
        self._bolum_liste_kap.setSpacing(8)
        kv.addLayout(self._bolum_liste_kap)
        self.iv.addWidget(self._bolum_kap)

        self._sezonlar = sezonlar
        self._grup_bolumler = bolumler
        self._bolum_bilgi = {}          # (s,e) -> TMDB bölüm sözlüğü

        # Hangi sezon açılsın? Yarım kalan bölüm hangisindeyse o.
        acik = min(sezonlar)
        devam_ic, _ = self._devam_bolumu(grup)
        if devam_ic is not None:
            sb = devam_ic.sezon_bolum()
            if sb and sb[0] in sezonlar:
                acik = sb[0]
        self._sezon_goster(acik)

        # TMDB bölüm bilgisini arka planda çek
        tid = getattr(bolumler[0], "tmdb_id", 0) or 0
        if self.istemci and self.istemci.hazir and tid:
            self._bolum_bilgi_iste(tid, [s for s in sezonlar if s])

    def _sezon_goster(self, sezon_no: int):
        """Seçili sezonun bölümlerini çizer."""
        for s, d in getattr(self, "_sezon_dugmeleri", {}).items():
            aktif = (s == sezon_no)
            d.setChecked(aktif)
            v = self.depo.ayarlar.get("vurgu", R_VURGU)
            d.setStyleSheet(
                f"QPushButton{{background:{v if aktif else R_YUZEY2};"
                f"border:1px solid {v if aktif else R_CIZGI};border-radius:8px;"
                f"padding:5px 14px;font-weight:{'700' if aktif else '500'};"
                f"color:{'#fff' if aktif else R_METIN};}}")
        self._acik_sezon = sezon_no

        while self._bolum_liste_kap.count():
            it = self._bolum_liste_kap.takeAt(0)
            w = it.widget() if it else None
            if w:
                w.hide(); w.setParent(None); w.deleteLater()

        for sira, b in self._sezonlar.get(sezon_no, []):
            sb = b.sezon_bolum()
            bilgi = self._bolum_bilgi.get(sb) if sb else None
            sat = BolumSatiri(b, sira, bilgi, self.depo, self.klasor,
                              lambda i=sira: self._oynat(self._grup_bolumler[i],
                                                         self._grup_bolumler, i))
            self._bolum_liste_kap.addWidget(sat)

    def _bolum_bilgi_iste(self, tmdb_id: int, sezonlar: list):
        """TMDB bölüm bilgilerini arka planda getirir."""
        if not sezonlar:
            return

        class _Isci(QThread):
            hazir = pyqtSignal(object)

            def __init__(self, ist, tid, sz):
                super().__init__()
                self.ist, self.tid, self.sz = ist, tid, sz

            def run(self):
                try:
                    self.hazir.emit(self.ist.bolum_haritasi(self.tid, self.sz))
                except Exception:
                    self.hazir.emit({})

        i = _Isci(self.istemci, tmdb_id, sezonlar)
        i.hazir.connect(self._bolum_bilgi_geldi)
        self._isciler.append(i)
        isci_baslat(i)

    def _bolum_bilgi_geldi(self, harita: dict):
        if not harita:
            return
        try:
            self._bolum_bilgi = harita
            self._sezon_goster(getattr(self, "_acik_sezon", min(self._sezonlar)))
        except RuntimeError:
            pass          # sayfa değişmiş

    # ── TMDB sonucu ────────────────────────────────────────────────
    def _tmdb_geldi(self, d: dict):
        self.detay_veri = d
        self._bilgi.setVisible(False)

        baslik = d.get("title") or d.get("name") or ""
        if baslik:
            self.l_bas.setText(baslik)

        # Fragmanları arka planda getir (düğme ancak fragman varsa görünür)
        self._fragmanlar = []
        tid = d.get("id") or 0
        if tid:
            self._fragman_iste(tid, bool(d.get("name") and not d.get("title")))

        par = []
        tar = (d.get("release_date") or d.get("first_air_date") or "")[:4]
        if tar: par.append(tar)
        puan = d.get("vote_average") or 0
        if puan: par.append(f"★ {puan:.1f}   ({d.get('vote_count', 0)} oy)")
        sure = d.get("runtime") or (d.get("episode_run_time") or [0])[0]
        if sure: par.append(f"{int(sure)} dk")
        if d.get("number_of_seasons"): par.append(f"{d['number_of_seasons']} sezon")
        ulke = [c.get("iso_3166_1") for c in (d.get("production_countries") or [])][:2]
        if ulke: par.append("/".join(ulke))
        self.l_meta.setText("   ·   ".join(par))

        tur = [g["name"] for g in (d.get("genres") or [])]
        self.l_tur.setText("  ".join(f"[ {t} ]" for t in tur) if tur else "")

        ozet = d.get("overview") or ""
        self.l_ozet.setText(ozet if ozet else "Özet bulunamadı.")

        if d.get("poster_path"):
            self._gorsel_iste(afis_url(d["poster_path"], AFIS_BUYUK), "afis")
        if d.get("backdrop_path"):
            self._gorsel_iste(afis_url(d["backdrop_path"], ARKA_BOY), "arka")

        oyuncular = (d.get("credits") or {}).get("cast") or []
        if oyuncular:
            self._oyuncu_rafi(oyuncular[:18])
        ekip = (d.get("credits") or {}).get("crew") or []
        yonetmen = [c["name"] for c in ekip if c.get("job") == "Director"][:2]
        if yonetmen:
            y = QLabel("🎬  Yönetmen:  " + ", ".join(yonetmen))
            y.setStyleSheet(f"color:{R_METIN};font-size:12.5px;padding-left:28px;"
                            "background:transparent;")
            self._golge(y, 10)
            self.iv.insertWidget(1, y)

        benzer = (d.get("recommendations") or {}).get("results") or []
        if benzer:
            self._benzer_rafi(benzer[:14])

    def _tmdb_hata(self, mesaj: str):
        self._bilgi.setText(f"ℹ {mesaj}")
        self._bilgi.setVisible(True)

    def _gorsel_iste(self, url, etiket):
        isci = GorselIsci(url, self.klasor, etiket)
        isci.hazir.connect(self._gorsel_geldi)
        self._isciler.append(isci)
        isci_baslat(isci)

    def _gorsel_geldi(self, etiket, veri):
        px = QPixmap()
        if not px.loadFromData(veri) or px.isNull():
            return
        if etiket == "afis":
            self.afis.setPixmap(px)
        elif etiket == "arka":
            self._arka_px = px
            self.update()

    def _oyuncu_rafi(self, oyuncular):
        bas = QLabel("Oyuncu Kadrosu")
        bas.setStyleSheet("font-size:17px;font-weight:700;padding-left:28px;background:transparent;")
        self.iv.addWidget(bas)
        kaydir = QScrollArea(); kaydir.setWidgetResizable(True)
        kaydir.setFixedHeight(200)
        kaydir.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        kaydir.setStyleSheet("background:transparent;border:0;")
        ic = QWidget(); ic.setStyleSheet("background:transparent;")
        h = QHBoxLayout(ic)
        h.setContentsMargins(28, 0, 28, 0); h.setSpacing(12)
        for k in oyuncular:
            h.addWidget(OyuncuKarti(k, self.klasor, lambda kk: self.oyuncu_ac.emit(kk)))
        h.addStretch()
        kaydir.setWidget(ic)
        self.iv.addWidget(kaydir)

    def _benzer_rafi(self, ogeler):
        bas = QLabel("Benzer İçerikler")
        bas.setStyleSheet("font-size:17px;font-weight:700;padding-left:28px;background:transparent;")
        self.iv.addWidget(bas)
        kaydir = QScrollArea(); kaydir.setWidgetResizable(True)
        kaydir.setFixedHeight(KART_BOY + 26)
        kaydir.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        kaydir.setStyleSheet("background:transparent;border:0;")
        ic = QWidget(); ic.setStyleSheet("background:transparent;")
        h = QHBoxLayout(ic)
        h.setContentsMargins(28, 0, 28, 0); h.setSpacing(14)
        for o in ogeler:
            h.addWidget(TmdbKart(o, self.klasor, lambda oo: self.icerik_ac.emit(oo)))
        h.addStretch()
        kaydir.setWidget(ic)
        self.iv.addWidget(kaydir)

    def _fav(self):
        ilk = self.grup["bolumler"][0]
        yeni = self.depo.favori_degistir(ilk)
        self.b_fav.setText("♥  Favorilerde" if yeni else "♡  Favorilere ekle")
        self.depo.kaydet()

    def _esle_ac(self):
        if not self.grup:
            return
        d = TmdbEsleDialog(self.istemci, self.grup, self.klasor, self)
        if d.exec() == QDialog.DialogCode.Accepted and d.secilen:
            ilk = self.grup["bolumler"][0]
            for b in self.grup["bolumler"]:
                b.tmdb_id = d.secilen.get("id", 0)
            self.depo.kaydet()
            self.goster(self.grup)

    def _oto_url(self):
        """Sağlayıcı şablonundan otomatik URL üretir."""
        if not self.detay_veri:
            QMessageBox.information(self, "Bilgi",
                                    "Önce TMDB bilgisi yüklenmeli.\n"
                                    "TMDB anahtarı girili değilse: ⚙ → TMDB API anahtarı")
            return
        d = OtoUrlDialog(self.depo, self.detay_veri, self.grup, self)
        if d.exec() == QDialog.DialogCode.Accepted and d.uretilen:
            ilk = self.grup["bolumler"][0]
            ilk.url = d.uretilen
            ilk.tarayici = getattr(d, "tarayicida", False)
            self.depo.kaydet()
            QMessageBox.information(self, "URL güncellendi",
                                    f"Yeni bağlantı:\n{d.uretilen}")


# ══════════════════════════════════════════════════════════════════
#  TMDB KARTI (yerel listede olmayan içerikler)
# ══════════════════════════════════════════════════════════════════
class TmdbKart(QFrame):
    """
    TMDB içeriği kartı.

    HTML sürümündeki gibi kart üzerinde iki rozet vardır:
      • sağ üst : "✓ BENDE" (listende var) ya da "+ URL" (yok)
      • sol üst : "⚡ OTO"  → otomatik URL ekleme (yalnızca listende yoksa)
    """
    def __init__(self, oge: dict, klasor, tiklandi=None, parent=None,
                 depo=None, oto_url_cb=None, bende_mi=None, en: int = 0,
                 puan_goster: bool = False, gizle_cb=None):
        super().__init__(parent)
        self.oge, self._tiklandi = oge, tiklandi
        self.depo, self._oto_url = depo, oto_url_cb
        # “Bunları da İzle” rafı: puan sağ ALTTA küçük yazı yerine sol üstte
        # belirgin bir rozet olarak gösterilir (rafın amacı puan).
        self._puan_rozet = bool(puan_goster)
        self._gizle_cb = gizle_cb
        self._px = None
        self._hover = False
        self.setMouseTracking(True)
        # Listede var mı? (dış fonksiyon verilirse ona sorulur)
        self.bende = False
        if bende_mi:
            try:
                self.bende = bool(bende_mi(oge))
            except Exception:
                self.bende = False
        # `en` verilirse 2:3 afiş oranı korunarak ölçeklenir (yan yana 10 kart).
        if en and en != KART_EN:
            self.setFixedSize(en, round(en * KART_BOY / KART_EN))
        else:
            self.setFixedSize(KART_EN, KART_BOY)
        if tiklandi:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        yol = oge.get("poster_path")
        if yol:
            self._isci = GorselIsci(afis_url(yol, AFIS_BOY), klasor)
            self._isci.hazir.connect(self._geldi)
            isci_baslat(self._isci)

    def _geldi(self, _e, veri):
        px = QPixmap()
        if px.loadFromData(veri) and not px.isNull():
            self._px = px; self.update()

    def enterEvent(self, e):
        self._hover = True; self.update()

    def leaveEvent(self, e):
        self._hover = False; self.update()

    def _oto_kutusu(self):
        """Sol üstteki '⚡ OTO' rozetinin dikdörtgeni."""
        from PyQt6.QtCore import QRect
        # Puan rozeti sol üstü kullanıyorsa OTO rozeti onun altına iner,
        # yoksa iki rozet üst üste biniyordu.
        return QRect(6, 28 if self._puan_rozet else 6, 52, 18)

    def _gizle_kutusu(self):
        """
        '×' (öneriyi gizle) düğmesinin dikdörtgeni — sağ ÜST köşe.

        Önce sağ alta konmuştu ama orada başlık/puan yazısının üstüne
        biniyordu (ekran görüntüsüyle görüldü). Sağ üstte “+ URL” rozeti
        var; × onun ALTINA alındı ve 22 px'e büyütüldü (küçük hedefe
        isabet ettirmek zordu).
        """
        from PyQt6.QtCore import QRect
        return QRect(self.width() - 28, 30, 22, 22)

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        nokta = e.position().toPoint()
        # Önce '×' gizle düğmesi mi? (yalnızca “Bunları da İzle” rafında)
        if self._gizle_cb and self._gizle_kutusu().contains(nokta):
            self._gizle_cb(self.oge)
            return
        # Sonra '⚡ OTO' rozeti mi tıklandı?
        if (not self.bende) and self._oto_url and \
                self._oto_kutusu().contains(nokta):
            self._oto_url(self.oge)
            return
        if self._tiklandi:
            self._tiklandi(self.oge)

    def paintEvent(self, e):
        from PyQt6.QtGui import QPainterPath
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        pp = QPainterPath()
        pp.addRoundedRect(0.0, 0.0, float(r.width()), float(r.height()), 10, 10)
        p.setClipPath(pp)
        baslik = self.oge.get("title") or self.oge.get("name") or "?"
        if self._px:
            ol = self._px.scaled(r.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                 Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(-(ol.width() - r.width()) // 2, 0, ol)
        else:
            p.fillRect(r, renk_uret(baslik))
            p.setPen(QColor(255, 255, 255, 40))
            f = QFont(); f.setPointSize(24); f.setBold(True); p.setFont(f)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter,
                       "".join(w[0] for w in baslik.split()[:2]).upper() or "?")

        g = QLinearGradient(0, r.height() * 0.5, 0, r.height())
        g.setColorAt(0, QColor(0, 0, 0, 0)); g.setColorAt(1, QColor(0, 0, 0, 235))
        p.fillRect(r, QBrush(g))
        p.setPen(QColor(255, 255, 255))
        f = QFont(); f.setPointSize(9); f.setBold(True); p.setFont(f)
        met = p.fontMetrics().elidedText(baslik, Qt.TextElideMode.ElideRight, r.width() - 14)
        p.drawText(r.adjusted(7, 0, -7, -20),
                   Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft, met)
        yil = (self.oge.get("release_date") or self.oge.get("first_air_date") or "")[:4]
        puan = self.oge.get("vote_average") or 0
        alt = "   ·   ".join(x for x in (yil, f"★ {puan:.1f}" if puan else "") if x)
        if alt:
            p.setPen(QColor(198, 202, 212))
            f2 = QFont(); f2.setPointSize(8); p.setFont(f2)
            p.drawText(r.adjusted(7, 0, -7, -6),
                       Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft, alt)

        # ── Rozetler (HTML sürümündeki gibi) ──
        fr = QFont(); fr.setPointSize(7); fr.setBold(True); p.setFont(fr)
        # Sağ üst: bende var mı?
        et = "✓ BENDE" if self.bende else "+ URL"
        w = p.fontMetrics().horizontalAdvance(et) + 14
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 190, 90, 225) if self.bende else QColor(255, 255, 255, 55))
        p.drawRoundedRect(r.right() - w - 6, 6, w, 18, 5, 5)
        p.setPen(QColor(255, 255, 255))
        p.drawText(r.right() - w - 6, 6, w, 18, Qt.AlignmentFlag.AlignCenter, et)

        # Sol üst: PUAN rozeti (“Bunları da İzle” rafı) — altın sarısı
        if self._puan_rozet and puan:
            from PyQt6.QtCore import QRect
            et2 = f"★ {puan:.1f}"
            fp = QFont(); fp.setPointSize(8); fp.setBold(True); p.setFont(fp)
            w2 = p.fontMetrics().horizontalAdvance(et2) + 14
            kp = QRect(6, 6, w2, 19)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(245, 197, 24, 240))
            p.drawRoundedRect(kp, 5, 5)
            p.setPen(QColor(26, 22, 0))
            p.drawText(kp, Qt.AlignmentFlag.AlignCenter, et2)
            p.setFont(fr)

        # Sol üst: otomatik URL ekle (yalnızca listende YOKSA)
        if (not self.bende) and self._oto_url:
            k = self._oto_kutusu()
            p.setPen(Qt.PenStyle.NoPen)
            g3 = QLinearGradient(k.x(), k.y(), k.x() + k.width(), k.y())
            g3.setColorAt(0, QColor(155, 0, 255, 235)); g3.setColorAt(1, QColor(58, 0, 255, 235))
            p.setBrush(QBrush(g3))
            p.drawRoundedRect(k, 5, 5)
            p.setPen(QColor(255, 255, 255))
            p.drawText(k, Qt.AlignmentFlag.AlignCenter, "⚡ OTO")

        # Sağ alt: '×' öneriyi gizle (yalnızca fare kart üzerindeyken)
        if self._gizle_cb and self._hover:
            kg = self._gizle_kutusu()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 200))
            p.drawEllipse(kg)
            p.setPen(QColor(255, 255, 255, 235))
            fx = QFont(); fx.setPointSize(10); fx.setBold(True); p.setFont(fx)
            p.drawText(kg, Qt.AlignmentFlag.AlignCenter, "×")
            p.setFont(fr)

        if self._hover:
            from PyQt6.QtGui import QPen
            p.setPen(QPen(QColor(255, 255, 255, 170), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -2, -2), 10, 10)
        p.end()


# ══════════════════════════════════════════════════════════════════
#  TMDB RAFI — SARMALANAN IZGARA
# ══════════════════════════════════════════════════════════════════
class TmdbRaf(QWidget):
    """
    Ana sayfadaki TMDB keşif rafı (Trend, Vizyon, En İyiler…).

    arayuz.Raf ile aynı davranış: yan yana en çok SUTUN afiş, sığmayan
    alt satıra geçer. Kaydırma oku YOKTUR (kullanıcı isteği).
    """
    SUTUN = 10
    SATIR = 2

    def __init__(self, baslik: str, ogeler: list, klasor, tiklandi=None,
                 depo=None, oto_url_cb=None, bende_mi=None, parent=None,
                 puan_goster: bool = False, gizle_cb=None):
        super().__init__(parent)
        self.setAutoFillBackground(True)
        self.setStyleSheet(f"background:{R_ARKA};")
        self._ogeler = ogeler
        self._klasor, self._tiklandi = klasor, tiklandi
        self._depo, self._oto, self._bende = depo, oto_url_cb, bende_mi
        self._puan_goster = bool(puan_goster)
        self._gizle_cb = gizle_cb
        self._acik = False
        self._son_sutun = 0
        self._son_kart_en = KART_EN
        self._bosluk = 14

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 4, 0, 12)
        v.setSpacing(8)

        ust = QHBoxLayout()
        ust.setContentsMargins(28, 0, 28, 0)
        b = QLabel(baslik)
        b.setStyleSheet("font-size:16px;font-weight:700;background:transparent;")
        sy = QLabel(f"{len(ogeler)} içerik")
        sy.setStyleSheet(f"color:{R_SOLUK};font-size:11px;background:transparent;")
        ust.addWidget(b)
        ust.addWidget(sy)
        ust.addStretch()
        self.b_tumu = QPushButton("")
        self.b_tumu.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_tumu.setStyleSheet(
            f"QPushButton{{background:transparent;border:0;color:{R_SOLUK};"
            f"font-size:12px;font-weight:600;padding:4px 2px;}}"
            f"QPushButton:hover{{color:{R_METIN};}}")
        self.b_tumu.clicked.connect(self._ac_kapa)
        self.b_tumu.setVisible(False)
        ust.addWidget(self.b_tumu)
        v.addLayout(ust)

        self.ic = QWidget()
        self.ic.setStyleSheet("background:transparent;")
        self.izgara = QGridLayout(self.ic)
        self.izgara.setContentsMargins(28, 0, 28, 0)
        self.izgara.setSpacing(self._bosluk)
        self.izgara.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        v.addWidget(self.ic)

        self._ciz(*self._yerlesim_hesapla())

    def _yerlesim_hesapla(self) -> tuple[int, int]:
        """(sütun, kart genişliği) — arayuz.Raf ile aynı kural."""
        alt_sinir = 108
        en = self.width()
        if en <= 0 and self.parent() is not None:
            en = self.parent().width()
        if en <= 0:
            en = 1600
        kullanilabilir = max(1, en - 56)
        sutun = self.SUTUN
        while sutun > 1:
            kart = (kullanilabilir - (sutun - 1) * self._bosluk) // sutun
            if kart >= alt_sinir:
                return sutun, int(min(kart, KART_EN))
            sutun -= 1
        return 1, int(min(kullanilabilir, KART_EN))

    def _sutun_hesapla(self) -> int:
        return self._yerlesim_hesapla()[0]

    def _ciz(self, sutun: int, kart_en: int = 0):
        self._son_sutun = sutun
        self._son_kart_en = kart_en or KART_EN
        while self.izgara.count():
            it = self.izgara.takeAt(0)
            w = it.widget() if it else None
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()

        toplam = len(self._ogeler)
        sinir = toplam if self._acik else min(toplam, sutun * self.SATIR)
        kart_en = self._son_kart_en
        for i, o in enumerate(self._ogeler[:sinir]):
            self.izgara.addWidget(
                TmdbKart(o, self._klasor, self._tiklandi, depo=self._depo,
                         oto_url_cb=self._oto, bende_mi=self._bende, en=kart_en,
                         puan_goster=self._puan_goster, gizle_cb=self._gizle_cb),
                i // sutun, i % sutun)
        for c in range(sutun):
            self.izgara.setColumnStretch(c, 0)
        self.izgara.setColumnStretch(sutun, 1)

        gizli = toplam - sinir
        if gizli > 0:
            self.b_tumu.setText(f"Tümünü göster  ({gizli} tane daha)  ▾")
            self.b_tumu.setVisible(True)
        elif self._acik and toplam > sutun * self.SATIR:
            self.b_tumu.setText("Daralt  ▴")
            self.b_tumu.setVisible(True)
        else:
            self.b_tumu.setVisible(False)

    def _ac_kapa(self):
        self._acik = not self._acik
        self._ciz(*self._yerlesim_hesapla())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        sutun, kart_en = self._yerlesim_hesapla()
        if sutun != self._son_sutun or kart_en != self._son_kart_en:
            self._ciz(sutun, kart_en)


# ══════════════════════════════════════════════════════════════════
#  OYUNCU SAYFASI
# ══════════════════════════════════════════════════════════════════
class KisiIsci(QThread):
    """Oyuncu bilgisini arka planda çeker (Qt'ye sinyalle döner)."""
    hazir = pyqtSignal(object)

    def __init__(self, istemci, kisi_id):
        super().__init__()
        self.istemci, self.kisi_id = istemci, kisi_id

    def run(self):
        try:
            d = self.istemci.kisi(self.kisi_id)
            if d:
                self.hazir.emit(d)
        except Exception:
            pass


class OyuncuSayfasi(QWidget):
    icerik_ac = pyqtSignal(dict)

    def __init__(self, istemci, gorsel_klasor, geri_cb, parent=None,
                 depo=None, oto_url_cb=None, bende_mi=None):
        super().__init__(parent)
        self.istemci, self.klasor, self._geri = istemci, gorsel_klasor, geri_cb
        # Kartlarda "✓ BENDE" / "+ URL" / "⚡ OTO" rozetlerinin çıkabilmesi
        # için depo ve geri çağrımlar gerekir; eskiden verilmiyordu ve
        # filmografi kartları rozetsiz görünüyordu.
        self.depo = depo
        self._oto_url = oto_url_cb
        self._bende_mi = bende_mi
        self._isciler = []
        ana = QVBoxLayout(self); ana.setContentsMargins(0, 0, 0, 0)
        ust = QHBoxLayout(); ust.setContentsMargins(20, 12, 20, 6)
        b = QPushButton("‹  Geri"); b.setFixedSize(90, 32)
        b.clicked.connect(lambda: self._geri())
        ust.addWidget(b); ust.addStretch()
        ana.addLayout(ust)
        self.kaydir = QScrollArea(); self.kaydir.setWidgetResizable(True)
        self.ic = QWidget(); self.iv = QVBoxLayout(self.ic)
        self.iv.setContentsMargins(28, 0, 28, 26); self.iv.setSpacing(16)
        self.kaydir.setWidget(self.ic)
        ana.addWidget(self.kaydir, 1)

    def goster(self, kisi: dict):
        for i in reversed(range(self.iv.count())):
            it = self.iv.takeAt(i)
            w = it.widget() if it else None
            if w:
                w.hide(); w.setParent(None); w.deleteLater()

        ust = QHBoxLayout(); ust.setSpacing(22)
        self.foto = QLabel(); self.foto.setFixedSize(170, 255)
        self.foto.setStyleSheet(f"background:{R_YUZEY2};border-radius:12px;")
        self.foto.setScaledContents(True)
        ust.addWidget(self.foto, 0, Qt.AlignmentFlag.AlignTop)
        sag = QVBoxLayout(); sag.setSpacing(8)
        self.l_ad = QLabel(kisi.get("name", "?"))
        self.l_ad.setStyleSheet("font-size:26px;font-weight:800;")
        sag.addWidget(self.l_ad)
        self.l_bilgi = QLabel(""); self.l_bilgi.setStyleSheet(f"color:{R_SOLUK};font-size:12px;")
        sag.addWidget(self.l_bilgi)
        self.l_bio = QLabel("Yükleniyor…"); self.l_bio.setWordWrap(True)
        self.l_bio.setMaximumWidth(760)
        self.l_bio.setStyleSheet("font-size:12.5px;")
        sag.addWidget(self.l_bio); sag.addStretch()
        ust.addLayout(sag, 1)
        sar = QWidget(); sar.setStyleSheet("background:transparent;")
        sar.setLayout(ust)
        self.iv.addWidget(sar)

        if kisi.get("profile_path"):
            i = GorselIsci(afis_url(kisi["profile_path"], "w342"), self.klasor, "foto")
            i.hazir.connect(lambda _e, v: self._foto(v))
            self._isciler.append(i); isci_baslat(i)

        # DİKKAT: ham threading.Thread + QTimer.singleShot KULLANMA.
        # Yabancı iş parçacığından QTimer.singleShot geri çağrımı hiç
        # tetiklenmiyor (ölçüldü) — bu yüzden QThread + sinyal kullanılır.
        if self.istemci and self.istemci.hazir and kisi.get("id"):
            self._kisi_isci = KisiIsci(self.istemci, kisi["id"])
            self._kisi_isci.hazir.connect(self._doldur)
            self._isciler.append(self._kisi_isci)
            isci_baslat(self._kisi_isci)
        else:
            self.l_bio.setText("TMDB anahtarı girilmemiş.")

    def _foto(self, veri):
        px = QPixmap()
        if px.loadFromData(veri) and not px.isNull():
            self.foto.setPixmap(px)

    def _doldur(self, d):
        par = []
        if d.get("birthday"): par.append(f"Doğum: {d['birthday']}")
        if d.get("place_of_birth"): par.append(d["place_of_birth"])
        if d.get("known_for_department"): par.append(d["known_for_department"])
        self.l_bilgi.setText("   ·   ".join(par))
        bio = (d.get("biography") or "").strip()
        self.l_bio.setText(bio[:1400] + ("…" if len(bio) > 1400 else "") if bio
                           else "Biyografi bulunamadı.")

        # FİLMOGRAFİ — oyuncunun TÜM yapımları (yalnızca sizde olanlar değil).
        # Oyunculuk + yönetmenlik/yapımcılık birleştirilir, tekrarlar ayıklanır.
        krediler = d.get("combined_credits") or {}
        filmo = list(krediler.get("cast") or [])
        for f in (krediler.get("crew") or []):
            if (f.get("job") or "") in ("Director", "Writer", "Producer",
                                        "Screenplay", "Executive Producer"):
                filmo.append(f)
        # Aynı yapım birden çok görevle gelebiliyor → tekilleştir
        gorulen, benzersiz = set(), []
        for f in filmo:
            anahtar = (f.get("id"), f.get("media_type"))
            if anahtar in gorulen:
                continue
            gorulen.add(anahtar)
            benzersiz.append(f)
        filmo = [f for f in benzersiz if f.get("poster_path")]
        filmo.sort(key=lambda x: x.get("popularity", 0) or 0, reverse=True)
        if not filmo:
            return

        self._filmo = filmo
        self._filmo_acik = False

        ust2 = QHBoxLayout()
        bas = QLabel(f"Filmografi  ({len(filmo)})")
        bas.setStyleSheet("font-size:17px;font-weight:700;")
        ust2.addWidget(bas)
        ipucu = QLabel("Sizde olmayanlarda  ⚡ OTO  ile bağlantı üretebilirsiniz")
        ipucu.setStyleSheet(f"color:{R_SOLUK};font-size:11px;")
        ust2.addWidget(ipucu)
        ust2.addStretch()
        self.b_filmo_tumu = QPushButton("")
        self.b_filmo_tumu.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_filmo_tumu.setStyleSheet(
            f"QPushButton{{background:transparent;border:0;color:{R_SOLUK};"
            f"font-size:12px;font-weight:600;padding:4px 2px;}}"
            f"QPushButton:hover{{color:{R_METIN};}}")
        self.b_filmo_tumu.clicked.connect(self._filmo_ac_kapa)
        ust2.addWidget(self.b_filmo_tumu)
        self.iv.addLayout(ust2)

        self._filmo_kap = QWidget()
        self._filmo_izgara = QGridLayout(self._filmo_kap)
        self._filmo_izgara.setContentsMargins(0, 0, 0, 0)
        self._filmo_izgara.setSpacing(14)
        self.iv.addWidget(self._filmo_kap)
        self._filmo_ciz()
        self.iv.addStretch()

    # ── filmografi ızgarası ────────────────────────────────────────
    def _filmo_ciz(self):
        """Filmografi kartlarını çizer (rozetlerle birlikte)."""
        izg = self._filmo_izgara
        while izg.count():
            it = izg.takeAt(0)
            w = it.widget() if it else None
            if w:
                w.hide(); w.setParent(None); w.deleteLater()

        sutun = 7
        toplam = len(self._filmo)
        sinir = toplam if self._filmo_acik else min(toplam, sutun * 3)
        for i, f in enumerate(self._filmo[:sinir]):
            izg.addWidget(
                TmdbKart(f, self.klasor, lambda o: self.icerik_ac.emit(o),
                         depo=self.depo, oto_url_cb=self._oto_url,
                         bende_mi=self._bende_mi),
                i // sutun, i % sutun)

        gizli = toplam - sinir
        if gizli > 0:
            self.b_filmo_tumu.setText(f"Tümünü göster  ({gizli} tane daha)  ▾")
            self.b_filmo_tumu.setVisible(True)
        elif self._filmo_acik and toplam > sutun * 3:
            self.b_filmo_tumu.setText("Daralt  ▴")
            self.b_filmo_tumu.setVisible(True)
        else:
            self.b_filmo_tumu.setVisible(False)

    def _filmo_ac_kapa(self):
        self._filmo_acik = not self._filmo_acik
        self._filmo_ciz()


# ══════════════════════════════════════════════════════════════════
#  TMDB EŞLEŞTİRME DÜZELTME
# ══════════════════════════════════════════════════════════════════
class TmdbEsleDialog(QDialog):
    def __init__(self, istemci, grup, klasor, parent=None):
        super().__init__(parent)
        self.istemci, self.grup, self.klasor = istemci, grup, klasor
        self.secilen = None
        self.setWindowTitle("TMDB eşleşmesini düzelt")
        self.resize(680, 520)
        v = QVBoxLayout(self)
        v.addWidget(QLabel("Doğru yapımı bulmak için arayın:"))
        h = QHBoxLayout()
        self.giris = QLineEdit(grup["baslik"])
        self.tur = QComboBox(); self.tur.addItems(["Film", "Dizi"])
        if not grup["tekil"]:
            self.tur.setCurrentIndex(1)
        b = QPushButton("Ara"); b.setFixedWidth(80)
        h.addWidget(self.giris, 1); h.addWidget(self.tur); h.addWidget(b)
        v.addLayout(h)
        self.liste = QListWidget()
        v.addWidget(self.liste, 1)
        alt = QHBoxLayout()
        self.durum = QLabel(""); self.durum.setStyleSheet(f"color:{R_SOLUK};")
        bi = QPushButton("İptal"); bs = QPushButton("Bunu kullan")
        bs.setStyleSheet(f"background:{R_VURGU};border:0;border-radius:8px;"
                         "font-weight:700;padding:8px 18px;")
        alt.addWidget(self.durum); alt.addStretch(); alt.addWidget(bi); alt.addWidget(bs)
        v.addLayout(alt)
        b.clicked.connect(self._ara)
        self.giris.returnPressed.connect(self._ara)
        bi.clicked.connect(self.reject)
        bs.clicked.connect(self._sec)
        self.liste.itemDoubleClicked.connect(lambda _: self._sec())
        QTimer.singleShot(120, self._ara)

    def _ara(self):
        ad = self.giris.text().strip()
        if not ad or not (self.istemci and self.istemci.hazir):
            self.durum.setText("TMDB anahtarı gerekli")
            return
        self.durum.setText("Aranıyor…")
        QApplication.processEvents()
        dizi = self.tur.currentIndex() == 1
        import requests
        try:
            r = requests.get(
                f"https://api.themoviedb.org/3/search/{'tv' if dizi else 'movie'}",
                params={"api_key": self.istemci.anahtar, "language": self.istemci.dil,
                        "query": ad}, timeout=12)
            sonuc = r.json().get("results", []) if r.status_code == 200 else []
        except Exception as e:
            self.durum.setText(f"Hata: {type(e).__name__}")
            return
        self.liste.clear()
        for s in sonuc[:25]:
            baslik = s.get("title") or s.get("name") or "?"
            yil = (s.get("release_date") or s.get("first_air_date") or "")[:4]
            puan = s.get("vote_average") or 0
            oz = (s.get("overview") or "")[:90]
            it = QListWidgetItem(f"{baslik}  ({yil})   ★ {puan:.1f}\n{oz}…")
            it.setData(Qt.ItemDataRole.UserRole, s)
            self.liste.addItem(it)
        self.durum.setText(f"{len(sonuc)} sonuç")

    def _sec(self):
        it = self.liste.currentItem()
        if it:
            self.secilen = it.data(Qt.ItemDataRole.UserRole)
            self.accept()


# ══════════════════════════════════════════════════════════════════
#  SAĞLAYICI YÖNETİCİSİ
#  Kullanıcının KENDİ URL şablonlarını tanımladığı yer.
#  Hazır sağlayıcı listesi GELMİYOR — bkz. anapencere.py açıklaması.
# ══════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════
#  HAZIR ŞABLONLAR
# ══════════════════════════════════════════════════════════════════
# HTML sürümü + ek embed sağlayıcılar.
# “Otomatik URL ekle” listeden seçim veya “Hepsini dene” sunar.
HAZIR_SAGLAYICILAR = [
    # ── Doğrudan / HLS ──
    {
        "ad": "VidMody",
        "tpl": "https://vidmody.com/vs/{IMDB}",
        "tip": "movie",
        "aktif": True,
        "tarayici": False,
        "aciklama": "Doğrudan HLS — mpv ile oynatılır.",
    },
    {
        "ad": "VidMody TV",
        "tpl": "https://vidmody.com/vs/{IMDB}/{S}/{E}",
        "tip": "tv",
        "aktif": True,
        "tarayici": False,
        "aciklama": "Doğrudan HLS — mpv ile oynatılır.",
    },
    # ── Embed (tarayıcı) ──
    {
        "ad": "SmashyStream",
        "tpl": "https://embed.smashystream.com/playere.php?imdb={IMDB}",
        "tip": "movie",
        "aktif": True,
        "tarayici": True,
        "aciklama": "Embed oynatıcı — tarayıcıda açılır.",
    },
    {
        "ad": "SmashyStream TV",
        "tpl": "https://embed.smashystream.com/playere.php?imdb={IMDB}&season={S}&episode={E}",
        "tip": "tv",
        "aktif": True,
        "tarayici": True,
        "aciklama": "Embed oynatıcı — tarayıcıda açılır.",
    },
    {
        "ad": "VidLink.pro",
        "tpl": "https://vidlink.pro/movie/{TMDB}?primaryColor=e50914&secondaryColor=170d27&iconColor=eefdec&autoplay=true",
        "tip": "movie",
        "aktif": True,
        "tarayici": True,
        "aciklama": "Embed oynatıcı — tarayıcıda açılır.",
    },
    {
        "ad": "VidLink.pro TV",
        "tpl": "https://vidlink.pro/tv/{TMDB}/{S}/{E}?primaryColor=e50914&secondaryColor=170d27&iconColor=eefdec&autoplay=true",
        "tip": "tv",
        "aktif": True,
        "tarayici": True,
        "aciklama": "Embed oynatıcı — tarayıcıda açılır.",
    },
    {
        "ad": "AutoEmbed",
        "tpl": "https://player.autoembed.cc/embed/movie/{TMDB}",
        "tip": "movie",
        "aktif": True,
        "tarayici": True,
        "aciklama": "Embed — TMDB kimliği kullanır.",
    },
    {
        "ad": "AutoEmbed TV",
        "tpl": "https://player.autoembed.cc/embed/tv/{TMDB}/{S}/{E}",
        "tip": "tv",
        "aktif": True,
        "tarayici": True,
        "aciklama": "Embed — TMDB kimliği kullanır.",
    },
    {
        "ad": "2Embed",
        "tpl": "https://www.2embed.cc/embed/{IMDB}",
        "tip": "movie",
        "aktif": True,
        "tarayici": True,
        "aciklama": "Embed — IMDb kimliği kullanır.",
    },
    {
        "ad": "2Embed TV",
        "tpl": "https://www.2embed.cc/embedtv/{IMDB}&s={S}&e={E}",
        "tip": "tv",
        "aktif": True,
        "tarayici": True,
        "aciklama": "Embed — IMDb + sezon/bölüm.",
    },
    {
        "ad": "MultiEmbed",
        "tpl": "https://multiembed.mov/?video_id={IMDB}",
        "tip": "movie",
        "aktif": True,
        "tarayici": True,
        "aciklama": "Embed — IMDb kimliği kullanır.",
    },
    {
        "ad": "MultiEmbed TV",
        "tpl": "https://multiembed.mov/?video_id={IMDB}&s={S}&e={E}",
        "tip": "tv",
        "aktif": True,
        "tarayici": True,
        "aciklama": "Embed — IMDb + sezon/bölüm.",
    },
    {
        "ad": "Embed.su",
        "tpl": "https://embed.su/embed/movie/{TMDB}",
        "tip": "movie",
        "aktif": True,
        "tarayici": True,
        "aciklama": "Embed — TMDB kimliği kullanır.",
    },
    {
        "ad": "Embed.su TV",
        "tpl": "https://embed.su/embed/tv/{TMDB}/{S}/{E}",
        "tip": "tv",
        "aktif": True,
        "tarayici": True,
        "aciklama": "Embed — TMDB + sezon/bölüm.",
    },
    {
        "ad": "VidSrc",
        "tpl": "https://vidsrc.to/embed/movie/{IMDB}",
        "tip": "movie",
        "aktif": False,
        "tarayici": True,
        "aciklama": "Embed (varsayılan kapalı).",
    },
    {
        "ad": "VidSrc TV",
        "tpl": "https://vidsrc.to/embed/tv/{IMDB}/{S}/{E}",
        "tip": "tv",
        "aktif": False,
        "tarayici": True,
        "aciklama": "Embed (varsayılan kapalı).",
    },
    # ── Yasal / açık ──
    {
        "ad": "Internet Archive — arama",
        "tpl": "https://archive.org/search?query={TITLE}+AND+mediatype%3Amovies",
        "tip": "movie",
        "aktif": True,
        "tarayici": True,
        "aciklama": "Kamuya açık filmler.",
    },
    {
        "ad": "Internet Archive — doğrudan video",
        "tpl": "https://archive.org/download/{IA_ID}/{IA_DOSYA}",
        "tip": "movie",
        "aktif": True,
        "tarayici": False,
        "aciklama": "IA öğe + dosya adı biliniyorsa.",
    },
    {
        "ad": "Wikimedia Commons — arama",
        "tpl": "https://commons.wikimedia.org/w/index.php?search={TITLE}+filetype%3Avideo",
        "tip": "movie",
        "aktif": True,
        "tarayici": True,
        "aciklama": "Özgür lisanslı video arşivi.",
    },
    {
        "ad": "TMDB — resmî fragman sayfası",
        "tpl": "https://www.themoviedb.org/movie/{TMDB}/videos",
        "tip": "movie",
        "aktif": True,
        "tarayici": True,
        "aciklama": "Resmî fragmanlar.",
    },
    {
        "ad": "TMDB — dizi fragman sayfası",
        "tpl": "https://www.themoviedb.org/tv/{TMDB}/videos",
        "tip": "tv",
        "aktif": True,
        "tarayici": True,
        "aciklama": "Dizi fragmanları.",
    },
]


def saglayicilari_hazirla(depo) -> list:
    """
    Liste boşsa varsayılanları yazar; eksik HTML/embed sağlayıcıları pasif ekler.
    """
    mevcut = depo.ayarlar.setdefault("saglayicilar", [])
    if not isinstance(mevcut, list):
        mevcut = []
        depo.ayarlar["saglayicilar"] = mevcut

    _OTO = {
        "VidMody", "VidMody TV", "SmashyStream", "SmashyStream TV",
        "VidLink.pro", "VidLink.pro TV", "AutoEmbed", "AutoEmbed TV",
        "2Embed", "2Embed TV", "MultiEmbed", "MultiEmbed TV",
        "Embed.su", "Embed.su TV", "VidSrc", "VidSrc TV",
    }

    if not mevcut:
        for s in HAZIR_SAGLAYICILAR:
            mevcut.append({
                "ad": s["ad"], "tpl": s["tpl"], "tip": s["tip"],
                "aktif": bool(s.get("aktif", True)),
                "tarayici": bool(s.get("tarayici", False)),
            })
        depo.kaydet()
        return mevcut

    adlar = {s.get("ad") for s in mevcut}
    degisti = False
    for s in HAZIR_SAGLAYICILAR:
        if s["ad"] in adlar or s["ad"] not in _OTO:
            continue
        mevcut.append({
            "ad": s["ad"], "tpl": s["tpl"], "tip": s["tip"],
            "aktif": False,  # mevcut kullanıcıda pasif
            "tarayici": bool(s.get("tarayici", False)),
        })
        degisti = True
    if degisti:
        depo.kaydet()
    return mevcut


YER_TUTUCULAR = [
    ("{IMDB}", "IMDb kimliği", "tt1375666"),
    ("{TMDB}", "TMDB kimliği", "27205"),
    ("{TITLE}", "Yapım adı", "Inception"),
    ("{YEAR}", "Yıl", "2010"),
    ("{S}", "Sezon numarası", "1"),
    ("{E}", "Bölüm numarası", "1"),
    ("{S2}", "Sezon (2 haneli)", "01"),
    ("{E2}", "Bölüm (2 haneli)", "01"),
    ("{IA_ID}", "Internet Archive öğe kimliği", "charlie_chaplin_film_fest"),
    ("{IA_DOSYA}", "Internet Archive dosya adı", "film_512kb.mp4"),
]


def saglayici_degerleri(detay: dict, sezon: str = "1", bolum: str = "1") -> dict:
    """TMDB detayından yer tutucu sözlüğü üretir."""
    imdb = (detay.get("external_ids") or {}).get("imdb_id") or ""
    baslik = detay.get("title") or detay.get("name") or ""
    yil = (detay.get("release_date") or detay.get("first_air_date") or "")[:4]
    s = (sezon or "1").strip() or "1"
    e = (bolum or "1").strip() or "1"
    return {
        "IMDB": imdb,
        "TMDB": str(detay.get("id") or ""),
        "TITLE": baslik,
        "YEAR": yil,
        "S": s,
        "E": e,
        "S2": s.zfill(2),
        "E2": e.zfill(2),
        "IA_ID": "",
        "IA_DOSYA": "",
    }


def sablon_doldur(tpl: str, degerler: dict) -> str:
    from urllib.parse import quote
    u = tpl
    for k, val in degerler.items():
        s = str(val or "")
        if k == "TITLE":
            s = quote(s, safe="")
        u = u.replace("{" + k + "}", s)
    return u


def sablon_ayristir(metin: str) -> list[dict]:
    """
    Serbest metinden sağlayıcı listesi çıkarır.

    Kabul edilen biçimler (satır başına bir kayıt):
        Ad | https://adres/{IMDB} | film
        Ad ; https://adres/{IMDB} ; dizi
        Ad , https://adres/{IMDB}
        https://adres/{IMDB}                  (ad adresten türetilir)
    Ayrıca "Dışa aktar" ile alınan JSON metni de doğrudan yapıştırılabilir.
    """
    metin = (metin or "").strip()
    if not metin:
        return []

    # 1) JSON denemesi
    try:
        veri = json.loads(metin)
        if isinstance(veri, dict):
            veri = veri.get("saglayicilar", [])
        if isinstance(veri, list):
            cikti = []
            for s in veri:
                if not isinstance(s, dict):
                    continue
                tpl = (s.get("tpl") or s.get("url") or "").strip()
                if not tpl:
                    continue
                cikti.append({
                    "ad": (s.get("ad") or s.get("name") or _ad_uret(tpl)).strip(),
                    "tpl": tpl,
                    "tip": "tv" if str(s.get("tip") or s.get("type") or "").lower()
                                   in ("tv", "dizi", "series") else "movie",
                    "aktif": bool(s.get("aktif", s.get("enabled", True))),
                    "tarayici": bool(s.get("tarayici", False)),
                })
            if cikti:
                return cikti
    except Exception:
        pass

    # 2) Satır satır ayrıştırma
    cikti = []
    for ham in metin.splitlines():
        satir = ham.strip()
        if not satir or satir.startswith("#"):
            continue
        parca = None
        for ayrac in ("|", ";", "\t"):
            if ayrac in satir:
                parca = [p.strip() for p in satir.split(ayrac)]
                break
        if parca is None:
            if "," in satir and "http" in satir:
                i = satir.index("http")
                onek = satir[:i].strip(" ,\t")
                parca = [onek, satir[i:].strip()] if onek else [satir[i:].strip()]
            else:
                parca = [satir]

        adres = ""
        ad = ""
        tip = ""
        for p in parca:
            if p.lower().startswith(("http://", "https://")):
                adres = p
            elif p.lower() in ("film", "movie", "dizi", "tv", "series"):
                tip = p.lower()
            elif p and not ad:
                ad = p
        if not adres:
            continue
        if not tip:
            # Sezon/bölüm yer tutucusu varsa dizi kalıbıdır.
            tip = "tv" if ("{S}" in adres or "{E}" in adres
                           or "{S2}" in adres or "{E2}" in adres) else "movie"
        cikti.append({
            "ad": ad or _ad_uret(adres),
            "tpl": adres,
            "tip": "tv" if tip in ("dizi", "tv", "series") else "movie",
            "aktif": True,
            "tarayici": False,
        })
    return cikti


def _ad_uret(adres: str) -> str:
    """Adresten okunabilir bir ad türetir."""
    try:
        from urllib.parse import urlparse
        a = urlparse(adres).netloc or adres
        a = a.replace("www.", "").split(":")[0]
        return a[:40] or "Sağlayıcı"
    except Exception:
        return "Sağlayıcı"


class HazirSablonDialog(QDialog):
    """Yasal/açık kaynak şablonlarından seçip ekleme."""

    def __init__(self, depo, parent=None):
        super().__init__(parent)
        self.depo = depo
        self.eklenen = 0
        self.setWindowTitle("Hazır şablonlar")
        self.resize(720, 470)
        v = QVBoxLayout(self)
        v.setSpacing(10)

        b = QLabel(
            "Aşağıdakiler <b>yasal / kamuya açık</b> kaynaklardır. Kullanmak "
            "istediklerinizi işaretleyip ekleyin.<br>"
            "<span style='color:#8b90a0'>Korsan yayın yapan embed servisleri "
            "bilinçli olarak listelenmez; kendi kaynağınızı “Toplu ekle” ya da "
            "“Sihirbaz” ile tanımlayabilirsiniz.</span>")
        b.setWordWrap(True)
        v.addWidget(b)

        self.liste = QListWidget()
        for s in HAZIR_SAGLAYICILAR:
            tip = "Dizi" if s["tip"] == "tv" else "Film"
            ek = "   ↗ tarayıcıda açılır" if s.get("tarayici") else ""
            it = QListWidgetItem(
                f"[{tip}]  {s['ad']}{ek}\n        {s['tpl']}\n        {s['aciklama']}")
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Unchecked)
            it.setData(Qt.ItemDataRole.UserRole, s)
            self.liste.addItem(it)
        v.addWidget(self.liste, 1)

        alt = QHBoxLayout()
        b_hepsi = QPushButton("Tümünü işaretle")
        b_iptal = QPushButton("İptal")
        b_ekle = QPushButton("Seçilenleri ekle")
        b_ekle.setStyleSheet(f"background:{R_VURGU};border:0;border-radius:8px;"
                             "font-weight:700;padding:8px 18px;")
        alt.addWidget(b_hepsi)
        alt.addStretch()
        alt.addWidget(b_iptal)
        alt.addWidget(b_ekle)
        v.addLayout(alt)

        b_iptal.clicked.connect(self.reject)
        b_ekle.clicked.connect(self._ekle)
        b_hepsi.clicked.connect(self._hepsi)

    def _hepsi(self):
        for i in range(self.liste.count()):
            self.liste.item(i).setCheckState(Qt.CheckState.Checked)

    def _ekle(self):
        mevcut = self.depo.ayarlar.setdefault("saglayicilar", [])
        var = {s.get("tpl") for s in mevcut}
        for i in range(self.liste.count()):
            it = self.liste.item(i)
            if it.checkState() != Qt.CheckState.Checked:
                continue
            s = it.data(Qt.ItemDataRole.UserRole)
            if s["tpl"] in var:
                continue
            mevcut.append({"ad": s["ad"], "tpl": s["tpl"], "tip": s["tip"],
                           "aktif": True, "tarayici": bool(s.get("tarayici"))})
            self.eklenen += 1
        self.depo.kaydet()
        self.accept()


class TopluEkleDialog(QDialog):
    """Serbest metin/JSON yapıştırarak toplu sağlayıcı ekleme."""

    def __init__(self, depo, parent=None):
        super().__init__(parent)
        self.depo = depo
        self.eklenen = 0
        self.setWindowTitle("Toplu sağlayıcı ekle")
        self.resize(760, 520)
        v = QVBoxLayout(self)
        v.setSpacing(10)

        b = QLabel(
            "Sağlayıcı listenizi buraya yapıştırın. Her satır bir kayıttır:<br>"
            "<code>Ad | https://adres/{IMDB} | film</code><br>"
            "<code>Ad | https://adres/{IMDB}/{S}/{E} | dizi</code><br>"
            "Tip yazmazsanız, adreste {S}/{E} varsa <b>dizi</b> sayılır.<br>"
            "“Dışa aktar” ile alınan <b>JSON</b> metni de doğrudan yapıştırılabilir.")
        b.setWordWrap(True)
        v.addWidget(b)

        self.metin = QPlainTextEdit()
        self.metin.setPlaceholderText(
            "Kendi Sunucum | https://sunucum.example/izle/{IMDB} | film\n"
            "Kendi Sunucum TV | https://sunucum.example/dizi/{IMDB}/{S}/{E} | dizi")
        self.metin.setStyleSheet(
            f"background:{R_YUZEY2};border:1px solid {R_CIZGI};border-radius:8px;"
            f"padding:8px;font-family:monospace;font-size:12px;")
        v.addWidget(self.metin, 1)

        self.onizleme = QLabel("—")
        self.onizleme.setWordWrap(True)
        self.onizleme.setStyleSheet(f"color:{R_SOLUK};font-size:12px;")
        v.addWidget(self.onizleme)

        alt = QHBoxLayout()
        b_coz = QPushButton("Denetle")
        b_iptal = QPushButton("İptal")
        self.b_ekle = QPushButton("Ekle")
        self.b_ekle.setStyleSheet(f"background:{R_VURGU};border:0;border-radius:8px;"
                                  "font-weight:700;padding:8px 18px;")
        alt.addWidget(b_coz)
        alt.addStretch()
        alt.addWidget(b_iptal)
        alt.addWidget(self.b_ekle)
        v.addLayout(alt)

        b_coz.clicked.connect(self._denetle)
        b_iptal.clicked.connect(self.reject)
        self.b_ekle.clicked.connect(self._ekle)
        self.metin.textChanged.connect(self._denetle)

    def _denetle(self):
        try:
            kayit = sablon_ayristir(self.metin.toPlainText())
        except Exception as e:
            self.onizleme.setText(f"Çözümlenemedi: {e}")
            return []
        if not kayit:
            self.onizleme.setText("—")
            return []
        ozet = "  ·  ".join(
            f"{k['ad']} [{'Dizi' if k['tip'] == 'tv' else 'Film'}]" for k in kayit[:6])
        fazla = f"  (+{len(kayit) - 6})" if len(kayit) > 6 else ""
        self.onizleme.setText(f"<b>{len(kayit)} kayıt bulundu:</b>  {ozet}{fazla}")
        return kayit

    def _ekle(self):
        kayit = self._denetle()
        if not kayit:
            QMessageBox.warning(self, "Boş", "Çözümlenebilir kayıt bulunamadı.")
            return
        mevcut = self.depo.ayarlar.setdefault("saglayicilar", [])
        var = {s.get("tpl") for s in mevcut}
        for k in kayit:
            if k["tpl"] in var:
                continue
            mevcut.append(k)
            var.add(k["tpl"])
            self.eklenen += 1
        self.depo.kaydet()
        self.accept()


class SihirbazDialog(QDialog):
    """
    Adım adım sağlayıcı ekleme.

    Kullanıcı çalışan bir adresi yapıştırır; program IMDb/TMDB kimliğini,
    sezon/bölüm numaralarını adres içinde bulup yer tutucuya çevirir.
    """

    def __init__(self, depo, parent=None):
        super().__init__(parent)
        self.depo = depo
        self.eklenen = 0
        self.setWindowTitle("Sağlayıcı sihirbazı")
        self.resize(760, 560)
        v = QVBoxLayout(self)
        v.setSpacing(10)

        v.addWidget(QLabel(
            "<b>1. Adım —</b> Bilinen bir yapımın <b>çalışan</b> adresini yapıştırın."))
        self.ornek = QLineEdit()
        self.ornek.setPlaceholderText("https://sunucum.example/izle/tt1375666")
        v.addWidget(self.ornek)

        h = QHBoxLayout()
        h.addWidget(QLabel("Bu adres hangi yapıma ait?"))
        self.imdb = QLineEdit()
        self.imdb.setPlaceholderText("IMDb (tt1375666)")
        self.tmdb = QLineEdit()
        self.tmdb.setPlaceholderText("TMDB (27205)")
        self.sezon = QLineEdit()
        self.sezon.setPlaceholderText("Sezon")
        self.sezon.setFixedWidth(70)
        self.bolum = QLineEdit()
        self.bolum.setPlaceholderText("Bölüm")
        self.bolum.setFixedWidth(70)
        h.addWidget(self.imdb)
        h.addWidget(self.tmdb)
        h.addWidget(self.sezon)
        h.addWidget(self.bolum)
        v.addLayout(h)

        b_coz = QPushButton("2. Adım — Kalıbı otomatik bul")
        b_coz.setStyleSheet(f"background:{R_YUZEY2};border:1px solid {R_CIZGI};"
                            "border-radius:8px;padding:8px;")
        v.addWidget(b_coz)

        v.addWidget(QLabel("<b>3. Adım —</b> Bulunan kalıp (elle düzenleyebilirsiniz):"))
        self.kalip = QLineEdit()
        self.kalip.setStyleSheet("font-family:monospace;")
        v.addWidget(self.kalip)

        h2 = QHBoxLayout()
        h2.addWidget(QLabel("Ad:"))
        self.ad = QLineEdit()
        h2.addWidget(self.ad, 1)
        h2.addWidget(QLabel("Tür:"))
        self.tip = QComboBox()
        self.tip.addItems(["Film", "Dizi"])
        h2.addWidget(self.tip)
        self.tarayici = QCheckBox("Tarayıcıda aç")
        self.tarayici.setToolTip(
            "İşaretlenirse bağlantı oynatıcıya değil, tarayıcıya gönderilir.\n"
            "Sayfa içine gömülü oynatıcılar (embed) için gereklidir.")
        h2.addWidget(self.tarayici)
        v.addLayout(h2)

        self.durum = QLabel("")
        self.durum.setWordWrap(True)
        self.durum.setStyleSheet(f"color:{R_SOLUK};font-size:12px;")
        v.addWidget(self.durum)

        v.addWidget(QLabel("<b>4. Adım —</b> Deneme (Inception / Breaking Bad S1B1):"))
        self.deneme = QLineEdit()
        self.deneme.setReadOnly(True)
        self.deneme.setStyleSheet("font-family:monospace;")
        v.addWidget(self.deneme)

        alt = QHBoxLayout()
        self.b_test = QPushButton("Adresi test et")
        b_iptal = QPushButton("İptal")
        self.b_kaydet = QPushButton("Kaydet")
        self.b_kaydet.setStyleSheet(f"background:{R_VURGU};border:0;border-radius:8px;"
                                    "font-weight:700;padding:8px 18px;")
        alt.addWidget(self.b_test)
        alt.addStretch()
        alt.addWidget(b_iptal)
        alt.addWidget(self.b_kaydet)
        v.addLayout(alt)

        b_coz.clicked.connect(self._coz)
        b_iptal.clicked.connect(self.reject)
        self.b_kaydet.clicked.connect(self._kaydet)
        self.b_test.clicked.connect(self._test)
        self.kalip.textChanged.connect(self._onizle)

    def _coz(self):
        ham = self.ornek.text().strip()
        if not ham:
            self.durum.setText("Önce örnek adresi yapıştırın.")
            return
        kalip = ham
        bulunan = []

        imdb = self.imdb.text().strip()
        if not imdb:
            m = re.search(r"tt\d{7,9}", ham)
            imdb = m.group(0) if m else ""
        if imdb and imdb in kalip:
            kalip = kalip.replace(imdb, "{IMDB}")
            bulunan.append("{IMDB}")

        tmdb = self.tmdb.text().strip()
        if tmdb and tmdb in kalip:
            kalip = re.sub(r"(?<!\d)" + re.escape(tmdb) + r"(?!\d)", "{TMDB}", kalip)
            bulunan.append("{TMDB}")

        s = self.sezon.text().strip()
        e = self.bolum.text().strip()
        if s:
            for ara, yer in ((f"S{int(s):02d}", "S{S2}"), (f"season/{s}", "season/{S}")):
                if ara in kalip:
                    kalip = kalip.replace(ara, yer.replace("S{S2}", "S{S2}"))
                    bulunan.append("{S}")
                    break
            else:
                kalip = re.sub(r"(?<![\d])" + re.escape(s) + r"(?![\d])", "{S}", kalip, count=1)
                if "{S}" in kalip:
                    bulunan.append("{S}")
        if e:
            for ara, yer in ((f"E{int(e):02d}", "E{E2}"), (f"episode/{e}", "episode/{E}")):
                if ara in kalip:
                    kalip = kalip.replace(ara, yer)
                    bulunan.append("{E}")
                    break
            else:
                kalip = re.sub(r"(?<![\d])" + re.escape(e) + r"(?![\d])", "{E}", kalip, count=1)
                if "{E}" in kalip:
                    bulunan.append("{E}")

        self.kalip.setText(kalip)
        if not self.ad.text().strip():
            self.ad.setText(_ad_uret(ham))
        if "{S}" in kalip or "{E}" in kalip or "{S2}" in kalip or "{E2}" in kalip:
            self.tip.setCurrentIndex(1)
        if bulunan:
            self.durum.setText("Bulunan yer tutucular: " + ", ".join(dict.fromkeys(bulunan)))
        else:
            self.durum.setText(
                "Yer tutucu bulunamadı. IMDb/TMDB kimliğini yukarıya yazıp tekrar "
                "deneyin ya da kalıbı elle düzenleyin.")
        self._onizle()

    def _ornek_degerler(self) -> dict:
        dizi = self.tip.currentIndex() == 1
        return {
            "IMDB": "tt0903747" if dizi else "tt1375666",
            "TMDB": "1396" if dizi else "27205",
            "TITLE": "Breaking Bad" if dizi else "Inception",
            "YEAR": "2008" if dizi else "2010",
            "S": "1", "E": "1", "S2": "01", "E2": "01",
            "IA_ID": "ornek_oge", "IA_DOSYA": "video.mp4",
        }

    def _onizle(self):
        self.deneme.setText(sablon_doldur(self.kalip.text().strip(), self._ornek_degerler()))

    def _test(self):
        u = self.deneme.text().strip()
        if not u:
            return
        if "{" in u:
            QMessageBox.warning(self, "Eksik", f"Doldurulamayan alan var:\n{u}")
            return
        self.durum.setText("Test ediliyor…")
        QApplication.processEvents()
        try:
            import requests
            r = requests.get(u, timeout=12, stream=True, allow_redirects=True,
                             headers={"User-Agent": "MediaBox/1.0",
                                      "Range": "bytes=0-2047"})
            tur = r.headers.get("Content-Type", "?")
            r.close()
            if r.status_code in (200, 206):
                self.durum.setText(f"✓ Yanıt alındı — HTTP {r.status_code} · {tur}")
            else:
                self.durum.setText(f"⚠ HTTP {r.status_code} · {tur}")
        except Exception as ex:
            self.durum.setText(f"✕ Bağlanılamadı: {type(ex).__name__}")

    def _kaydet(self):
        tpl = self.kalip.text().strip()
        ad = self.ad.text().strip() or _ad_uret(tpl)
        if not tpl.lower().startswith(("http://", "https://")):
            QMessageBox.warning(self, "Geçersiz", "Adres http:// veya https:// ile başlamalı.")
            return
        self.depo.ayarlar.setdefault("saglayicilar", []).append({
            "ad": ad, "tpl": tpl,
            "tip": "tv" if self.tip.currentIndex() == 1 else "movie",
            "aktif": True,
            "tarayici": self.tarayici.isChecked()})
        self.depo.kaydet()
        self.eklenen = 1
        self.accept()


class SaglayiciYonetici(QDialog):
    def __init__(self, depo, parent=None):
        super().__init__(parent)
        self.depo = depo
        self.setWindowTitle("Sağlayıcılar — URL şablonları")
        self.resize(820, 620)
        v = QVBoxLayout(self)
        v.setSpacing(10)

        bilgi = QLabel(
            "Kendi yayın kaynağınızın adres kalıbını tanımlayın. "
            "“⚡ Otomatik URL ekle” bu kalıbı kullanarak bağlantı üretir.<br>"
            "<span style='color:#8b90a0'>Yer tutucular: "
            "{IMDB} · {TMDB} · {TITLE} · {YEAR} · {S} · {E} · {S2} · {E2}"
            " &nbsp;(S2/E2 iki haneli: 01)</span>")
        bilgi.setWordWrap(True)
        bilgi.setStyleSheet("font-size:12px;")
        v.addWidget(bilgi)

        # ── hızlı ekleme düğmeleri ──
        hizli = QHBoxLayout()
        b_hazir = QPushButton("📚  Hazır şablonlar")
        b_toplu = QPushButton("📋  Toplu ekle / yapıştır")
        b_sihir = QPushButton("🪄  Sihirbaz")
        for d in (b_hazir, b_toplu, b_sihir):
            d.setStyleSheet(f"background:{R_YUZEY2};border:1px solid {R_CIZGI};"
                            f"border-radius:8px;padding:9px 12px;font-weight:600;")
            d.setCursor(Qt.CursorShape.PointingHandCursor)
            hizli.addWidget(d)
        hizli.addStretch()
        v.addLayout(hizli)

        self.liste = QListWidget()
        v.addWidget(self.liste, 1)

        form = QHBoxLayout()
        self.ad = QLineEdit()
        self.ad.setPlaceholderText("Ad (örn. Kendi Sunucum)")
        self.ad.setFixedWidth(190)
        self.tpl = QLineEdit()
        self.tpl.setPlaceholderText("https://sunucum.example/izle/{IMDB}")
        self.tip = QComboBox()
        self.tip.addItems(["Film", "Dizi"])
        self.tip.setFixedWidth(88)
        self.tarayici = QCheckBox("Tarayıcıda")
        self.tarayici.setToolTip("Bağlantı oynatıcı yerine tarayıcıda açılsın "
                                 "(gömülü/embed sayfaları için).")
        b_ekle = QPushButton("Ekle")
        b_ekle.setFixedWidth(80)
        form.addWidget(self.ad)
        form.addWidget(self.tpl, 1)
        form.addWidget(self.tip)
        form.addWidget(self.tarayici)
        form.addWidget(b_ekle)
        v.addLayout(form)

        alt = QHBoxLayout()
        b_sil = QPushButton("Seçileni sil")
        b_ac = QPushButton("Aç / Kapat")
        b_duzen = QPushButton("Düzenle")
        b_disa = QPushButton("⭳ Dışa aktar")
        b_ice = QPushButton("⭱ İçe aktar")
        b_kapat = QPushButton("Kapat")
        for d in (b_sil, b_ac, b_duzen, b_disa, b_ice):
            alt.addWidget(d)
        alt.addStretch()
        alt.addWidget(b_kapat)
        v.addLayout(alt)

        b_ekle.clicked.connect(self._ekle)
        b_sil.clicked.connect(self._sil)
        b_ac.clicked.connect(self._degistir)
        b_duzen.clicked.connect(self._duzenle)
        b_disa.clicked.connect(self._disa)
        b_ice.clicked.connect(self._ice)
        b_kapat.clicked.connect(self.accept)
        b_hazir.clicked.connect(self._hazir)
        b_toplu.clicked.connect(self._toplu)
        b_sihir.clicked.connect(self._sihirbaz)
        self.liste.itemDoubleClicked.connect(lambda *_: self._duzenle())
        self._yenile()

    # ── liste ──
    def _yenile(self):
        self.liste.clear()
        for i, s in enumerate(self.depo.ayarlar.get("saglayicilar", [])):
            durum = "✓ açık" if s.get("aktif", True) else "○ kapalı"
            tip = "Dizi" if s.get("tip") == "tv" else "Film"
            ek = "   ↗ tarayıcı" if s.get("tarayici") else ""
            it = QListWidgetItem(
                f"{durum}   [{tip}]{ek}   {s.get('ad', '?')}\n        {s.get('tpl', '')}")
            it.setData(Qt.ItemDataRole.UserRole, i)
            self.liste.addItem(it)
        if not self.liste.count():
            it = QListWidgetItem(
                "Henüz sağlayıcı yok.\n"
                "        “📚 Hazır şablonlar”, “📋 Toplu ekle” ya da “🪄 Sihirbaz” ile ekleyin.")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.liste.addItem(it)

    def _secili(self):
        it = self.liste.currentItem()
        if not it:
            return None
        return it.data(Qt.ItemDataRole.UserRole)

    # ── temel işlemler ──
    def _ekle(self):
        ad = self.ad.text().strip()
        tpl = self.tpl.text().strip()
        if not ad or not tpl:
            QMessageBox.warning(self, "Eksik", "Ad ve adres kalıbı gerekli.")
            return
        if not tpl.lower().startswith(("http://", "https://")):
            QMessageBox.warning(self, "Geçersiz", "Adres http:// veya https:// ile başlamalı.")
            return
        self.depo.ayarlar.setdefault("saglayicilar", []).append({
            "ad": ad, "tpl": tpl,
            "tip": "tv" if self.tip.currentIndex() == 1 else "movie",
            "aktif": True,
            "tarayici": self.tarayici.isChecked()})
        self.depo.kaydet()
        self.ad.clear()
        self.tpl.clear()
        self.tarayici.setChecked(False)
        self._yenile()

    def _sil(self):
        i = self._secili()
        if i is None:
            return
        del self.depo.ayarlar["saglayicilar"][i]
        self.depo.kaydet()
        self._yenile()

    def _degistir(self):
        i = self._secili()
        if i is None:
            return
        s = self.depo.ayarlar["saglayicilar"][i]
        s["aktif"] = not s.get("aktif", True)
        self.depo.kaydet()
        self._yenile()

    def _duzenle(self):
        i = self._secili()
        if i is None:
            return
        s = self.depo.ayarlar["saglayicilar"][i]
        yeni, tamam = QInputDialog.getText(self, "Kalıbı düzenle",
                                           f"{s.get('ad', '')} adres kalıbı:",
                                           text=s.get("tpl", ""))
        if tamam and yeni.strip():
            s["tpl"] = yeni.strip()
            self.depo.kaydet()
            self._yenile()

    # ── içe / dışa aktarma ──
    def _disa(self):
        veri = json.dumps(self.depo.ayarlar.get("saglayicilar", []),
                          ensure_ascii=False, indent=2)
        yol, _ = QFileDialog.getSaveFileName(self, "Sağlayıcıları dışa aktar",
                                             "saglayicilar.json", "JSON (*.json)")
        if yol:
            try:
                Path(yol).write_text(veri, encoding="utf-8")
                QMessageBox.information(self, "Kaydedildi", f"Dosyaya yazıldı:\n{yol}")
            except Exception as e:
                QMessageBox.warning(self, "Yazılamadı", str(e))
        else:
            QApplication.clipboard().setText(veri)
            QMessageBox.information(self, "Panoya kopyalandı",
                                    "Dosya seçilmedi; liste panoya kopyalandı.")

    def _ice(self):
        yol, _ = QFileDialog.getOpenFileName(self, "Sağlayıcı dosyası seç", "",
                                             "JSON / metin (*.json *.txt);;Tümü (*)")
        if not yol:
            return
        try:
            metin = Path(yol).read_text(encoding="utf-8")
        except Exception as e:
            QMessageBox.warning(self, "Okunamadı", str(e))
            return
        kayit = sablon_ayristir(metin)
        if not kayit:
            QMessageBox.warning(self, "Boş", "Dosyada çözümlenebilir kayıt yok.")
            return
        mevcut = self.depo.ayarlar.setdefault("saglayicilar", [])
        var = {s.get("tpl") for s in mevcut}
        n = 0
        for k in kayit:
            if k["tpl"] in var:
                continue
            mevcut.append(k)
            var.add(k["tpl"])
            n += 1
        self.depo.kaydet()
        self._yenile()
        QMessageBox.information(self, "İçe aktarıldı", f"{n} sağlayıcı eklendi.")

    # ── yardımcı diyaloglar ──
    def _hazir(self):
        d = HazirSablonDialog(self.depo, self)
        if d.exec() == QDialog.DialogCode.Accepted:
            self._yenile()
            if d.eklenen:
                QMessageBox.information(self, "Eklendi", f"{d.eklenen} şablon eklendi.")

    def _toplu(self):
        d = TopluEkleDialog(self.depo, self)
        if d.exec() == QDialog.DialogCode.Accepted:
            self._yenile()
            QMessageBox.information(self, "Eklendi", f"{d.eklenen} sağlayıcı eklendi.")

    def _sihirbaz(self):
        d = SihirbazDialog(self.depo, self)
        if d.exec() == QDialog.DialogCode.Accepted:
            self._yenile()


# ══════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════
#  OTOMATİK URL ÜRETME
# ══════════════════════════════════════════════════════════════════
class _SaglayiciDeneme(QThread):
    """Aktif sağlayıcı URL'lerini sırayla dener; çalışanları bildirir."""
    ilerleme = pyqtSignal(str)          # durum metni
    bulundu = pyqtSignal(str, str, bool)  # ad, url, tarayici
    bitti = pyqtSignal(list)            # [(ad, url, tarayici), ...]

    def __init__(self, adaylar: list[tuple]):
        # adaylar: [(ad, url, tarayici), ...]
        super().__init__()
        self.adaylar = adaylar
        self._dur = False

    def durdur(self):
        self._dur = True

    def run(self):
        import ssl, urllib.request, urllib.error
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        calisan = []
        for ad, url, tar in self.adaylar:
            if self._dur:
                break
            if not url or "{" in url:
                continue
            self.ilerleme.emit(f"Deneniyor: {ad}…")
            ok = False
            try:
                bas = {"User-Agent": "Mozilla/5.0 MediaBox/1.0",
                       "Range": "bytes=0-1024"}
                for yontem in ("GET", "HEAD"):
                    try:
                        istek = urllib.request.Request(url, method=yontem, headers=bas)
                        with urllib.request.urlopen(istek, timeout=10, context=ctx) as y:
                            kod = getattr(y, "status", 200) or 200
                            if 200 <= kod < 400 or kod in (403, 405):
                                # 403/405 sık görülür ama sayfa vardır (embed)
                                ok = True
                                break
                    except urllib.error.HTTPError as e:
                        # Embed siteleri çoğu zaman 200 HTML döner; 403/405 de "var" say
                        if e.code in (200, 206, 301, 302, 403, 405):
                            ok = True
                            break
                        if yontem == "HEAD":
                            continue
                    except Exception:
                        if yontem == "HEAD":
                            continue
            except Exception:
                ok = False
            # Embed sağlayıcıları için bağlantı açılıyorsa (DNS/TCP) yeterli kabul et
            if not ok and tar:
                try:
                    istek = urllib.request.Request(
                        url, method="GET",
                        headers={"User-Agent": "Mozilla/5.0 MediaBox/1.0"})
                    with urllib.request.urlopen(istek, timeout=8, context=ctx) as y:
                        ok = True
                except urllib.error.HTTPError:
                    ok = True   # sunucu cevap verdi
                except Exception:
                    ok = False
            if ok:
                calisan.append((ad, url, tar))
                self.bulundu.emit(ad, url, tar)
                self.ilerleme.emit(f"✓ {ad}")
            else:
                self.ilerleme.emit(f"✗ {ad}")
        self.bitti.emit(calisan)


class OtoUrlDialog(QDialog):
    def __init__(self, depo, detay: dict, grup: dict, parent=None):
        super().__init__(parent)
        self.depo, self.detay, self.grup = depo, detay, grup
        self.uretilen = ""
        self.tarayicida = False
        self.saglayici_adi = ""
        self._deneme = None
        self.setWindowTitle("Otomatik URL ekle")
        self.resize(700, 480)
        v = QVBoxLayout(self)
        v.setSpacing(10)

        imdb = (detay.get("external_ids") or {}).get("imdb_id") or ""
        tmdb = detay.get("id") or ""
        baslik = detay.get("title") or detay.get("name") or ""
        yil = (detay.get("release_date") or detay.get("first_air_date") or "")[:4]
        self._degerler = saglayici_degerleri(detay)

        bilgi = QLabel(f"<b>{baslik}</b>  ({yil})<br>"
                       f"IMDb: {imdb or '—'}   ·   TMDB: {tmdb}")
        v.addWidget(bilgi)

        dizi = not grup["tekil"]
        if dizi:
            h = QHBoxLayout()
            h.addWidget(QLabel("Sezon:"))
            self.sezon = QLineEdit("1")
            self.sezon.setFixedWidth(60)
            h.addWidget(self.sezon)
            h.addWidget(QLabel("Bölüm:"))
            self.bolum = QLineEdit("1")
            self.bolum.setFixedWidth(60)
            h.addWidget(self.bolum)
            h.addStretch()
            v.addLayout(h)
            sb = grup["bolumler"][0].sezon_bolum()
            if sb:
                self.sezon.setText(str(sb[0]))
                self.bolum.setText(str(sb[1]))
        else:
            self.sezon = self.bolum = None

        v.addWidget(QLabel("Sağlayıcı seçin:"))
        self.liste = QListWidget()
        v.addWidget(self.liste, 1)

        saglayicilari_hazirla(depo)
        self._listeyi_doldur()

        self.onizleme = QLineEdit()
        self.onizleme.setReadOnly(True)
        self.onizleme.setPlaceholderText("Üretilecek adres burada görünür")
        v.addWidget(self.onizleme)

        self.durum = QLabel("")
        self.durum.setStyleSheet(f"color:{R_SOLUK};font-size:12px;")
        self.durum.setWordWrap(True)
        v.addWidget(self.durum)

        alt = QHBoxLayout()
        b_yonet = QPushButton("⚙ Sağlayıcıları yönet")
        b_iptal = QPushButton("İptal")
        self.b_uygula = QPushButton("Bağlantıyı uygula")
        self.b_uygula.setStyleSheet(
            f"background:{R_VURGU};border:0;border-radius:8px;"
            "font-weight:700;padding:8px 18px;")
        alt.addWidget(b_yonet)
        alt.addStretch()
        alt.addWidget(b_iptal)
        alt.addWidget(self.b_uygula)
        v.addLayout(alt)

        self.liste.currentItemChanged.connect(self._onizle)
        if self.sezon:
            self.sezon.textChanged.connect(self._onizle)
        if self.bolum:
            self.bolum.textChanged.connect(self._onizle)
        b_iptal.clicked.connect(self._iptal)
        self.b_uygula.clicked.connect(self._uygula)
        b_yonet.clicked.connect(self._yonet)

    def _listeyi_doldur(self):
        self.liste.clear()
        dizi = not self.grup["tekil"]
        tur = "tv" if dizi else "movie"
        self._uygun = [s for s in self.depo.ayarlar.get("saglayicilar", [])
                       if s.get("aktif", True) and s.get("tip") == tur]

        # En üstte: Hepsini dene
        if self._uygun:
            it = QListWidgetItem(
                "⚡  Hepsini dene (çalışanı bul)\n"
                "        Aktif sağlayıcıları sırayla dener; ilk çalışanı kullanır")
            it.setData(Qt.ItemDataRole.UserRole, {"_hepsini": True})
            self.liste.addItem(it)

        for s in self._uygun:
            ek = "  ↗ tarayıcı" if s.get("tarayici") else ""
            it = QListWidgetItem(f"{s.get('ad', '?')}{ek}\n        {s.get('tpl', '')}")
            it.setData(Qt.ItemDataRole.UserRole, s)
            self.liste.addItem(it)

        if self._uygun:
            self.liste.setCurrentRow(0)
        else:
            it = QListWidgetItem(
                "⚠ Aktif sağlayıcı yok.\n"
                "        ⚙ Sağlayıcıları yönet → Aç/Kapat")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.liste.addItem(it)

    def _degerler_guncel(self) -> dict:
        d = dict(self._degerler)
        if self.sezon:
            d["S"] = self.sezon.text().strip() or "1"
            d["S2"] = d["S"].zfill(2)
        if self.bolum:
            d["E"] = self.bolum.text().strip() or "1"
            d["E2"] = d["E"].zfill(2)
        return d

    def _url_icin(self, s: dict) -> str:
        return sablon_doldur(s.get("tpl", ""), self._degerler_guncel())

    def _url(self) -> str:
        it = self.liste.currentItem()
        if not it:
            return ""
        s = it.data(Qt.ItemDataRole.UserRole) or {}
        if s.get("_hepsini"):
            return ""
        return self._url_icin(s)

    def _onizle(self, *_):
        it = self.liste.currentItem()
        s = (it.data(Qt.ItemDataRole.UserRole) if it else None) or {}
        if s.get("_hepsini"):
            self.onizleme.setText(f"— {len(self._uygun)} sağlayıcı denenecek —")
            self.b_uygula.setText("Hepsini dene")
        else:
            self.onizleme.setText(self._url())
            self.b_uygula.setText("Bağlantıyı uygula")

    def _uygula(self):
        it = self.liste.currentItem()
        if not it:
            QMessageBox.warning(self, "Seçim yok", "Bir sağlayıcı seçin.")
            return
        s = it.data(Qt.ItemDataRole.UserRole) or {}
        if s.get("_hepsini"):
            self._hepsini_baslat()
            return
        u = self._url_icin(s)
        if not u:
            QMessageBox.warning(self, "Seçim yok", "Bir sağlayıcı seçin.")
            return
        if "{" in u:
            QMessageBox.warning(self, "Eksik bilgi",
                                f"Adreste doldurulamayan alan var:\n{u}")
            return
        self.uretilen = u
        self.tarayicida = bool(s.get("tarayici"))
        self.saglayici_adi = s.get("ad", "")
        self.accept()

    def _hepsini_baslat(self):
        if not self._uygun:
            QMessageBox.warning(self, "Yok", "Denenecek aktif sağlayıcı yok.")
            return
        adaylar = []
        for s in self._uygun:
            u = self._url_icin(s)
            if u and "{" not in u:
                adaylar.append((s.get("ad", "?"), u, bool(s.get("tarayici"))))
        if not adaylar:
            QMessageBox.warning(self, "Eksik",
                                "IMDb/TMDB kimliği eksik; adres üretilemedi.")
            return
        self.b_uygula.setEnabled(False)
        self.liste.setEnabled(False)
        self.durum.setText("Sağlayıcılar deneniyor…")
        self._deneme = _SaglayiciDeneme(adaylar)
        self._deneme.ilerleme.connect(self.durum.setText)
        self._deneme.bitti.connect(self._hepsini_bitti)
        isci_baslat(self._deneme)

    def _hepsini_bitti(self, calisan: list):
        self.b_uygula.setEnabled(True)
        self.liste.setEnabled(True)
        if not calisan:
            self.durum.setText("✗ Hiçbir sağlayıcı yanıt vermedi.")
            QMessageBox.warning(
                self, "Bulunamadı",
                "Aktif sağlayıcıların hiçbiri yanıt vermedi.\n"
                "Başka bir sağlayıcıyı elle seçmeyi deneyin.")
            return
        # Birden fazla ise kullanıcıya seçtir
        if len(calisan) == 1:
            ad, url, tar = calisan[0]
            self.uretilen = url
            self.tarayicida = tar
            self.saglayici_adi = ad
            self.durum.setText(f"✓ {ad}")
            self.accept()
            return
        sec, ok = QInputDialog.getItem(
            self, "Çalışan sağlayıcılar",
            f"{len(calisan)} sağlayıcı yanıt verdi. Hangisini kullanmak istersiniz?",
            [f"{a}  —  {u[:60]}" for a, u, _ in calisan], 0, False)
        if not ok:
            self.durum.setText(f"{len(calisan)} sonuç hazır — tekrar seçebilirsiniz.")
            return
        i = [f"{a}  —  {u[:60]}" for a, u, _ in calisan].index(sec)
        ad, url, tar = calisan[i]
        self.uretilen = url
        self.tarayicida = tar
        self.saglayici_adi = ad
        self.accept()

    def _iptal(self):
        if self._deneme and self._deneme.isRunning():
            self._deneme.durdur()
        self.reject()

    def _yonet(self):
        SaglayiciYonetici(self.depo, self).exec()
        self._listeyi_doldur()
        self._onizle()


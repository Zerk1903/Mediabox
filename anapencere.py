#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MediaBox Qt — Ana Pencere
==========================
Üst menü + sayfalar + diyaloglar. arayuz.py tarafından çağrılır.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QFont, QKeySequence, QShortcut, QAction
from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QPushButton, QLineEdit, QScrollArea,
                             QGridLayout, QStackedWidget, QFileDialog,
                             QMessageBox, QFrame, QMenu, QInputDialog, QDialog,
                             QListWidget, QListWidgetItem, QComboBox, QCheckBox,
                             QProgressBar, QSizePolicy, QApplication, QTextEdit)

from mediabox_qt import (Depo, Icerik, m3u_ayristir, m3u_uret, temiz_baslik, kategori_tahmin,
                         tr_sadelestir, diziye_grupla, UCRETSIZ_KAYNAKLAR, TEMALAR, tema_bul,
                         veri_klasoru, html_verisi_ice_aktar, APP_ADI, APP_SURUM)
import arayuz
from tmdb import TmdbIstemci, afis_url, AFIS_BOY
from detay import (ZenginDetay, OyuncuSayfasi, SaglayiciYonetici,
                   TmdbEsleDialog, OtoUrlDialog, TmdbKart, TmdbRaf, GorselIsci,
                   isci_baslat, saglayicilari_hazirla)
from arayuz import (Kart, Raf, Vitrin, Oynatici, R_ARKA, R_YUZEY, R_YUZEY2,
                    R_CIZGI, R_METIN, R_SOLUK, R_VURGU, KART_EN, KART_BOY,
                    sure_yaz, stil)

SEKMELER = [
    ("home",      "Ana Sayfa"),
    ("movie",     "Filmler"),
    ("series",    "Diziler"),
    ("anime",     "Animasyon"),
    ("live",      "Canlı TV"),
    ("favorites", "Favoriler"),
    ("queue",     "Sonra İzle"),
    ("recent",    "Son İzlenenler"),
]


# ══════════════════════════════════════════════════════════════════
#  ARKA PLAN İNDİRİCİ (M3U / URL)
# ══════════════════════════════════════════════════════════════════
class Indirici(QThread):
    bitti = pyqtSignal(str, str)      # (metin, hata)
    ilerleme = pyqtSignal(str)

    def __init__(self, url: str):
        super().__init__()
        self.url = url

    def run(self):
        try:
            import requests
            self.ilerleme.emit("Bağlanılıyor…")
            r = requests.get(self.url, timeout=30, stream=True,
                             headers={"User-Agent": "MediaBox/1.0"})
            r.raise_for_status()
            parcalar, boyut = [], 0
            for p in r.iter_content(65536):
                parcalar.append(p); boyut += len(p)
                if boyut % (1 << 20) < 65536:
                    self.ilerleme.emit(f"{boyut/1048576:.1f} MB indirildi…")
            self.bitti.emit(b"".join(parcalar).decode("utf-8", "ignore"), "")
        except Exception as e:
            self.bitti.emit("", f"{type(e).__name__}: {e}")


# ══════════════════════════════════════════════════════════════════
#  LİNK KONTROLÜ (CORS yok — tarayıcıda yapılamıyordu)
# ══════════════════════════════════════════════════════════════════
class LinkKontrol(QThread):
    sonuc = pyqtSignal(str, bool, str)
    bitti = pyqtSignal(int, int)

    def __init__(self, urls: list[str], is_sayisi: int = 12):
        super().__init__()
        self.urls, self.is_sayisi = urls, is_sayisi
        self._dur = False

    def durdur(self):
        self._dur = True

    def run(self):
        import ssl, socket, urllib.request, urllib.error
        from concurrent.futures import ThreadPoolExecutor, as_completed
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE   # IPTV sunucuları sıklıkla geçersiz sertifika kullanır

        def tek(u):
            if self._dur:
                return u, False, "iptal"
            if not u.lower().startswith(("http://", "https://")):
                return u, True, "yerel/akış"
            bas = {"User-Agent": "Mozilla/5.0 MediaBox/1.0", "Range": "bytes=0-1024"}
            for yontem in ("HEAD", "GET"):
                try:
                    istek = urllib.request.Request(u, method=yontem, headers=bas)
                    with urllib.request.urlopen(istek, timeout=8, context=ctx) as y:
                        return u, 200 <= y.status < 400, str(y.status)
                except urllib.error.HTTPError as e:
                    if yontem == "HEAD" and e.code in (403, 405, 501):
                        continue
                    return u, False, f"HTTP {e.code}"
                except socket.timeout:
                    return u, False, "zaman aşımı"
                except Exception as e:
                    if yontem == "GET":
                        return u, False, type(e).__name__
            return u, False, "?"

        canli = olu = 0
        with ThreadPoolExecutor(max_workers=self.is_sayisi) as ex:
            isler = {ex.submit(tek, u): u for u in self.urls}
            for f in as_completed(isler):
                if self._dur:
                    break
                u, ok, notu = f.result()
                canli += ok; olu += (not ok)
                self.sonuc.emit(u, ok, notu)
        self.bitti.emit(canli, olu)


# ══════════════════════════════════════════════════════════════════
#  DETAY SAYFASI
# ══════════════════════════════════════════════════════════════════
class DetaySayfasi(QWidget):
    def __init__(self, depo: Depo, oynat_cb, geri_cb, parent=None):
        super().__init__(parent)
        self.depo, self._oynat, self._geri = depo, oynat_cb, geri_cb
        self.grup: dict | None = None

        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        ust = QHBoxLayout(); ust.setContentsMargins(24, 14, 24, 8)
        b = QPushButton("‹  Geri"); b.setFixedWidth(90); b.clicked.connect(self._geri)
        ust.addWidget(b); ust.addStretch()
        v.addLayout(ust)

        self.kaydir = QScrollArea(); self.kaydir.setWidgetResizable(True)
        self.ic = QWidget(); self.iv = QVBoxLayout(self.ic)
        self.iv.setContentsMargins(28, 0, 28, 24); self.iv.setSpacing(14)
        self.kaydir.setWidget(self.ic)
        v.addWidget(self.kaydir, 1)

    def goster(self, grup: dict):
        self.grup = grup
        while self.iv.count():
            it = self.iv.takeAt(0)
            w = it.widget() if it else None
            if w:
                # deleteLater() TEK BAŞINA yetmez: widget silinene kadar
                # ağaçta kalır, çizilmeye devam eder ve eski içerikle
                # yenisi üst üste görünür. Önce ağaçtan koparıyoruz.
                w.hide()
                w.setParent(None)
                w.deleteLater()

        e = grup["bolumler"][0]
        ust = QHBoxLayout(); ust.setSpacing(22)
        afis = QLabel(); afis.setFixedSize(KART_EN, KART_BOY)
        afis.setStyleSheet(f"background:{R_YUZEY2};border-radius:10px;")
        afis.setScaledContents(True)
        logo = grup.get("logo") or e.logo
        if logo:
            arayuz.AFIS.iste(logo, lambda px: afis.setPixmap(px))
        ust.addWidget(afis, 0, Qt.AlignmentFlag.AlignTop)

        sag = QVBoxLayout(); sag.setSpacing(8)
        bas = QLabel(grup["baslik"]); bas.setStyleSheet("font-size:26px;font-weight:800;")
        bas.setWordWrap(True); sag.addWidget(bas)
        par = []
        if e.yil(): par.append(e.yil())
        if e.grup: par.append(e.grup)
        if e.kalite(): par.append(e.kalite())
        if e.dublaj(): par.append("TR Dublaj")
        if not grup["tekil"]: par.append(f"{len(grup['bolumler'])} bölüm")
        alt = QLabel("  ·  ".join(par)); alt.setStyleSheet(f"color:{R_SOLUK};")
        sag.addWidget(alt)
        if e.ozet:
            oz = QLabel(e.ozet); oz.setWordWrap(True)
            oz.setStyleSheet(f"color:{R_METIN};font-size:12.5px;")
            oz.setMaximumWidth(760); sag.addWidget(oz)

        dug = QHBoxLayout(); dug.setSpacing(9)
        b1 = QPushButton("▶  Oynat"); b1.setFixedSize(128, 40)
        b1.setStyleSheet(f"background:{R_VURGU};border:0;border-radius:8px;font-weight:700;")
        b1.clicked.connect(lambda: self._oynat(grup["bolumler"][0], grup["bolumler"], 0))
        fav = self.depo.favori_mi(e)
        b2 = QPushButton("♥  Favorilerde" if fav else "♡  Favorilere ekle")
        b2.setFixedSize(150, 40)
        def fav_degistir():
            yeni = self.depo.favori_degistir(e)
            b2.setText("♥  Favorilerde" if yeni else "♡  Favorilere ekle")
            self.depo.kaydet()
        b2.clicked.connect(fav_degistir)
        b3 = QPushButton("📋  Bağlantıyı kopyala"); b3.setFixedSize(170, 40)

        def _baglanti_kopyala():
            QApplication.clipboard().setText(e.url)
            b3.setText("✓  Kopyalandı"); b3.setEnabled(False)
            QTimer.singleShot(1400, lambda: (b3.setText("📋  Bağlantıyı kopyala"), b3.setEnabled(True)))

        b3.clicked.connect(_baglanti_kopyala)
        dug.addWidget(b1); dug.addWidget(b2); dug.addWidget(b3); dug.addStretch()
        sag.addLayout(dug); sag.addStretch()
        ust.addLayout(sag, 1)
        sar = QWidget(); sar.setLayout(ust)
        self.iv.addWidget(sar)

        if not grup["tekil"]:
            bl = QLabel(f"Bölümler ({len(grup['bolumler'])})")
            bl.setStyleSheet("font-size:16px;font-weight:700;margin-top:10px;")
            self.iv.addWidget(bl)
            liste = QListWidget()
            liste.setStyleSheet(f"background:{R_YUZEY};border:1px solid {R_CIZGI};border-radius:10px;")
            liste.setMinimumHeight(min(420, 44 * len(grup["bolumler"]) + 12))
            for i, b in enumerate(grup["bolumler"]):
                sb = b.sezon_bolum()
                etiket = f"S{sb[0]:02d}E{sb[1]:02d}  ·  {temiz_baslik(b.ad) or b.ad}" if sb else b.ad
                n_alt = len(b.alternatifler or [])
                if n_alt:
                    etiket += f"  ·  {n_alt + 1} kaynak"
                pr = self.depo.ilerleme.get(b.url)
                if pr and pr.get("d"):
                    etiket += f"   ({int(pr['t']/pr['d']*100)}% izlendi)"
                it = QListWidgetItem(etiket); it.setData(Qt.ItemDataRole.UserRole, i)
                liste.addItem(it)
            liste.itemDoubleClicked.connect(
                lambda it: self._oynat(grup["bolumler"][it.data(Qt.ItemDataRole.UserRole)],
                                       grup["bolumler"], it.data(Qt.ItemDataRole.UserRole)))
            self.iv.addWidget(liste)
        else:
            # Film: alternatif kaynaklar varsa listele
            alts = [e] + list(e.alternatifler or [])
            goru, alts_temiz = set(), []
            for a in alts:
                u = (a.url or "").strip()
                if u and u not in goru:
                    goru.add(u)
                    alts_temiz.append(a)
            if len(alts_temiz) > 1:
                bl = QLabel(f"Kaynaklar / alternatifler ({len(alts_temiz)})")
                bl.setStyleSheet("font-size:16px;font-weight:700;margin-top:10px;")
                self.iv.addWidget(bl)
                liste = QListWidget()
                liste.setStyleSheet(
                    f"background:{R_YUZEY};border:1px solid {R_CIZGI};border-radius:10px;")
                liste.setMinimumHeight(min(280, 44 * len(alts_temiz) + 12))
                for i, a in enumerate(alts_temiz):
                    par = []
                    if a.kalite():
                        par.append(a.kalite())
                    if a.grup:
                        par.append(a.grup[:40])
                    if a.kaynak:
                        par.append(str(a.kaynak)[:30])
                    if a.dublaj():
                        par.append("TR Dublaj")
                    if not par:
                        par.append(f"Kaynak {i + 1}")
                    it = QListWidgetItem(" · ".join(par))
                    it.setData(Qt.ItemDataRole.UserRole, i)
                    liste.addItem(it)
                def _alt_oynat(it, _alts=alts_temiz):
                    i = it.data(Qt.ItemDataRole.UserRole)
                    self._oynat(_alts[i], _alts, i)
                liste.itemDoubleClicked.connect(_alt_oynat)
                self.iv.addWidget(liste)
        self.iv.addStretch()


# ══════════════════════════════════════════════════════════════════
#  IZGARA SAYFASI
# ══════════════════════════════════════════════════════════════════
class Izgara(QWidget):
    def __init__(self, depo: Depo, tiklandi, parent=None):
        super().__init__(parent)
        self.depo, self._tiklandi = depo, tiklandi
        self.gruplar: list[dict] = []
        self.sayfa = 0
        self.SAYFA_ADET = 120

        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(6)
        ust = QHBoxLayout(); ust.setContentsMargins(28, 10, 28, 0)
        self.l_baslik = QLabel(""); self.l_baslik.setStyleSheet("font-size:20px;font-weight:800;")
        self.l_adet = QLabel(""); self.l_adet.setStyleSheet(f"color:{R_SOLUK};")
        self.c_grup = QComboBox(); self.c_grup.setFixedWidth(210)
        self.c_grup.currentIndexChanged.connect(self._filtre)
        self.c_dil = QComboBox(); self.c_dil.setFixedWidth(140)
        self.c_dil.currentIndexChanged.connect(self._filtre)
        self.c_sirala = QComboBox(); self.c_sirala.setFixedWidth(170)
        self.c_sirala.addItems([
            "Varsayılan", "En son eklenen", "A → Z", "Z → A",
            "Yıl (yeni)", "Yıl (eski)",
        ])
        self.c_sirala.currentIndexChanged.connect(self._filtre)
        ust.addWidget(self.l_baslik); ust.addWidget(self.l_adet); ust.addStretch()
        ust.addWidget(QLabel("Grup:")); ust.addWidget(self.c_grup)
        ust.addWidget(QLabel("Dil:")); ust.addWidget(self.c_dil)
        ust.addWidget(QLabel("Sırala:")); ust.addWidget(self.c_sirala)
        v.addLayout(ust)

        self.kaydir = QScrollArea(); self.kaydir.setWidgetResizable(True)
        self.ic = QWidget(); self.izgara = QGridLayout(self.ic)
        self.izgara.setContentsMargins(28, 12, 28, 20)
        self.izgara.setSpacing(16)
        self.izgara.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.kaydir.setWidget(self.ic)
        v.addWidget(self.kaydir, 1)

        self.b_daha = QPushButton("Daha fazla göster")
        self.b_daha.clicked.connect(self._daha)
        self.b_daha.setVisible(False)
        v.addWidget(self.b_daha, 0, Qt.AlignmentFlag.AlignCenter)

    def doldur(self, baslik: str, icerikler: list[Icerik], grup_secenekleri: list[str] | None = None):
        self.l_baslik.setText(baslik)
        self._ham = icerikler
        self.c_grup.blockSignals(True)
        self.c_grup.clear(); self.c_grup.addItem("Tümü", "")
        for g in (grup_secenekleri or [])[:200]:
            self.c_grup.addItem(g, g)
        self.c_grup.blockSignals(False)
        # Dil seçenekleri
        self.c_dil.blockSignals(True)
        self.c_dil.clear(); self.c_dil.addItem("Tümü", "")
        diller = []
        for e in icerikler:
            dl = e.dil()
            if dl and dl not in diller:
                diller.append(dl)
        for dl in diller:
            self.c_dil.addItem(dl, dl)
        self.c_dil.setEnabled(len(diller) > 0)
        self.c_dil.blockSignals(False)
        self.sayfa = 0
        self._filtre()
        # Yerleşim oturduktan sonra sütun sayısını tazele (ilk açılışta viewport
        # henüz nihai genişliğinde olmuyor ve ızgara dar hesaplanıyordu).
        QTimer.singleShot(0, self._sutun_tazele)

    def _sutun_tazele(self):
        yeni = self._sutun_sayisi()
        if yeni != getattr(self, "_son_sutun", None):
            self._ciz()

    def _sutun_sayisi(self) -> int:
        genislik = max(1, self.kaydir.viewport().width() - 56)
        return max(2, genislik // (KART_EN + 16))

    def _filtre(self):
        icerik = list(self._ham)
        g = self.c_grup.currentData()
        if g:
            icerik = [e for e in icerik if e.grup == g]
        dl = self.c_dil.currentData()
        if dl:
            icerik = [e for e in icerik if e.dil() == dl]
        s = self.c_sirala.currentIndex()
        if s == 2:
            icerik.sort(key=lambda e: tr_sadelestir(e.ad))
        elif s == 3:
            icerik.sort(key=lambda e: tr_sadelestir(e.ad), reverse=True)
        elif s == 4:
            icerik.sort(key=lambda e: e.yil() or "0", reverse=True)
        elif s == 5:
            icerik.sort(key=lambda e: e.yil() or "9999")
        elif s == 1:
            # En son eklenen önce (bölüm bazında; gruplamadan sonra da uygulanır)
            icerik.sort(key=lambda e: float(getattr(e, "eklenme", 0) or 0), reverse=True)
        self.gruplar = diziye_grupla(icerik)
        if s == 1:
            # Kart: gruptaki en yeni bölümün eklenme zamanına göre
            def _grup_zaman(g):
                return max(
                    (float(getattr(b, "eklenme", 0) or 0) for b in g.get("bolumler") or []),
                    default=0.0,
                )
            self.gruplar.sort(key=_grup_zaman, reverse=True)
        self.sayfa = 0
        self._ciz()

    def _daha(self):
        self.sayfa += 1
        self._ciz(ekle=True)

    def _ciz(self, ekle: bool = False):
        if not ekle:
            while self.izgara.count():
                it = self.izgara.takeAt(0)
                w = it.widget() if it else None
                if w:
                    w.hide(); w.setParent(None); w.deleteLater()
        sutun = self._sutun_sayisi()
        self._son_sutun = sutun
        bas = self.sayfa * self.SAYFA_ADET
        dilim = self.gruplar[bas: bas + self.SAYFA_ADET]
        for i, g in enumerate(dilim):
            k = bas + i
            self.izgara.addWidget(Kart(g, self.depo, self._tiklandi), k // sutun, k % sutun)
        self.l_adet.setText(f"{len(self.gruplar)} içerik")
        self.b_daha.setVisible(bas + self.SAYFA_ADET < len(self.gruplar))


# ══════════════════════════════════════════════════════════════════
#  ANA PENCERE
# ══════════════════════════════════════════════════════════════════
class AnaPencere(QMainWindow):
    def __init__(self, depo: Depo):
        super().__init__()
        self.depo = depo
        self.setWindowTitle(f"{APP_ADI} {APP_SURUM}")
        self.resize(1440, 900)
        self.setMinimumSize(1000, 640)
        self.aktif_sekme = "home"
        self._indirici = None
        self._kontrol = None
        # TMDB istemcisi (afiş, arka plan, oyuncu kadrosu, puan…)
        self.gorsel_klasor = veri_klasoru() / "gorseller"
        self.gorsel_klasor.mkdir(parents=True, exist_ok=True)
        self.tmdb = TmdbIstemci(depo.ayarlar.get("tmdb_anahtar", ""),
                                veri_klasoru() / "tmdb_onbellek")
        arayuz.TMDB = self.tmdb
        arayuz.TMDB_KLASOR = self.gorsel_klasor

        koku = QWidget(); self.setCentralWidget(koku)
        ana = QVBoxLayout(koku); ana.setContentsMargins(0, 0, 0, 0); ana.setSpacing(0)

        # ── ÜST MENÜ (istenen: menü üstte) ──
        self.ustbar = QFrame(); self.ustbar.setFixedHeight(56)
        self.ustbar.setStyleSheet(
            f"background:{R_YUZEY};border-bottom:1px solid {R_CIZGI};")
        u = QHBoxLayout(self.ustbar); u.setContentsMargins(22, 0, 18, 0); u.setSpacing(4)

        logo = QLabel("MEDIABOX")
        logo.setStyleSheet(
            f"color:{self.depo.ayarlar.get('vurgu', R_VURGU)};font-size:19px;"
            "font-weight:900;letter-spacing:1.5px;")
        u.addWidget(logo); u.addSpacing(22)

        self.sekme_dugmeleri = {}
        for anahtar, ad in SEKMELER:
            b = QPushButton(ad)
            b.setCheckable(True); b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(self._sekme_stili(False))
            b.clicked.connect(lambda _, a=anahtar: self.sekme_ac(a))
            u.addWidget(b)
            self.sekme_dugmeleri[anahtar] = b
        u.addStretch()

        self.arama = QLineEdit(); self.arama.setPlaceholderText("Ara…  (Ctrl+F)")
        self.arama.setFixedWidth(260)
        self.arama.textChanged.connect(self._arama_gecikmeli)
        u.addWidget(self.arama)

        b_ekle = QPushButton("+ Liste"); b_ekle.setFixedWidth(84)
        b_ekle.clicked.connect(self.liste_menusu)
        u.addWidget(b_ekle)
        # Yeni bölüm bildirimi zili — yalnızca bekleyen bildirim varsa görünür
        # NOT: Emoji kullanılmıyor — bazı sistemlerde emoji fontu yoktur ve
        # düğme boş kare (tofu) görünür (ölçüldü: bu ortamda 0 emoji fontu).
        # Her fontta bulunan karakterlerle yazıyoruz.
        self.b_zil = QPushButton("YENİ")
        self.b_zil.setMinimumWidth(96)      # "YENİ 12" sığmalı
        self.b_zil.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_zil.setToolTip("Yeni bölüm bildirimleri")
        self.b_zil.clicked.connect(self.yeni_bolum_penceresi)
        self.b_zil.setVisible(False)
        u.addWidget(self.b_zil)

        b_ayar = QPushButton("⚙"); b_ayar.setFixedWidth(42)
        b_ayar.clicked.connect(self.ayarlar_menusu)
        u.addWidget(b_ayar)
        ana.addWidget(self.ustbar)

        # ── SAYFALAR ──
        self.yigin = QStackedWidget()
        ana.addWidget(self.yigin, 1)

        # 0: ana sayfa (vitrin + raflar)
        #
        # DEĞİŞİKLİK: Eskiden en üstte Hero, ortada ayrıca Vitrin vardı —
        # iki büyük öneri bandı. Hero dikey afişi tam genişliğe gerdiği için
        # görüntü bozuluyordu. Hero KALDIRILDI; tek band olarak Vitrin en
        # üste alındı (kullanıcı isteği).
        self.anasayfa = QScrollArea(); self.anasayfa.setWidgetResizable(True)
        self.as_ic = QWidget()
        # Opak arka plan şart: şeffaf bırakılırsa kaydırırken eski çizim
        # silinmiyor ve raf başlıkları çift görünüyordu.
        self.as_ic.setAutoFillBackground(True)
        self.as_ic.setStyleSheet(f"background:{R_ARKA};")
        self.as_v = QVBoxLayout(self.as_ic)
        self.as_v.setContentsMargins(0, 0, 0, 20); self.as_v.setSpacing(4)
        self.vitrin = Vitrin(self.depo, self.oynat, self.detay_ac)
        self.as_v.addWidget(self.vitrin)
        self.raf_alani = QWidget()
        self.raf_alani.setAutoFillBackground(True)
        self.raf_alani.setStyleSheet(f"background:{R_ARKA};")
        self.raf_v = QVBoxLayout(self.raf_alani)
        self.raf_v.setContentsMargins(0, 8, 0, 0); self.raf_v.setSpacing(2)
        self.as_v.addWidget(self.raf_alani)
        self.as_v.addStretch()
        self.anasayfa.setWidget(self.as_ic)
        self.yigin.addWidget(self.anasayfa)

        # 1: ızgara
        self.izgara = Izgara(self.depo, self.detay_ac)
        self.yigin.addWidget(self.izgara)

        # 2: detay (TMDB destekli: arka plan, oyuncu kadrosu, benzerler)
        self.detay = ZenginDetay(self.depo, self.tmdb, self.gorsel_klasor,
                                 self.oynat, self.geri)
        self.detay.oyuncu_ac.connect(self.oyuncu_ac)
        self.detay.icerik_ac.connect(self.tmdb_icerik_ac)
        self.yigin.addWidget(self.detay)

        # 3: oynatıcı
        self.oynatici = Oynatici(self.depo)
        self.oynatici.kapandi.connect(self.oynatici_kapandi)
        # Uygulama herhangi bir yolla kapanınca depoyu diske yaz
        try:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(self._kapanista_kaydet)
        except Exception:
            pass

        self.yigin.addWidget(self.oynatici)

        # 4: oyuncu sayfası
        # Filmografi kartlarında "✓ BENDE" / "⚡ OTO" rozetleri çıksın diye
        # depo ve geri çağrımlar veriliyor.
        self.oyuncu = OyuncuSayfasi(self.tmdb, self.gorsel_klasor, self.geri,
                                    depo=self.depo,
                                    oto_url_cb=self.tmdb_oto_url,
                                    bende_mi=self._tmdb_bende_mi)
        self.oyuncu.icerik_ac.connect(self.tmdb_icerik_ac)
        self.yigin.addWidget(self.oyuncu)

        # durum çubuğu
        self.statusBar().setStyleSheet(
            f"background:{R_YUZEY};color:{R_SOLUK};border-top:1px solid {R_CIZGI};")
        self._durum_yaz()

        # kısayollar
        QShortcut(QKeySequence("Ctrl+F"), self, activated=lambda: self.arama.setFocus())
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.m3u_ac)
        QShortcut(QKeySequence("Ctrl+Q"), self, activated=self.close)
        QShortcut(QKeySequence("Escape"), self, activated=self._esc)

        self._arama_zaman = QTimer(self); self._arama_zaman.setSingleShot(True)
        self._arama_zaman.setInterval(220)
        self._arama_zaman.timeout.connect(self._arama_uygula)

        self.sekme_ac("home")
        # URL kaynaklarını arka planda periyodik yenile (varsayılan 6 saat)
        self._yenileme_zaman = QTimer(self)
        self._yenileme_zaman.setInterval(30 * 60 * 1000)      # 30 dk'da bir bak
        self._yenileme_zaman.timeout.connect(self._otomatik_yenile)
        self._yenileme_zaman.start()
        QTimer.singleShot(8000, self._otomatik_yenile)
        # Yeni bölüm taraması: açılıştan 20 sn sonra bir kez, sonra 6 saatte bir
        # (modülün kendi 12 saatlik kilidi gereksiz istekleri zaten engeller).
        self._zil_tazele()
        QTimer.singleShot(20000, self._yeni_bolum_tara)
        self._yb_zaman = QTimer(self)
        self._yb_zaman.setInterval(6 * 60 * 60 * 1000)
        self._yb_zaman.timeout.connect(self._yeni_bolum_tara)
        self._yb_zaman.start()
        if not self.depo.icerikler:
            QTimer.singleShot(400, self._ilk_kullanim)

    # ── stil yardımcıları ──
    def _sekme_stili(self, aktif: bool) -> str:
        v = self.depo.ayarlar.get("vurgu", R_VURGU)
        if aktif:
            return (f"QPushButton{{background:transparent;border:0;border-bottom:2px solid {v};"
                    f"border-radius:0;padding:16px 13px;color:{R_METIN};font-weight:700;}}")
        return (f"QPushButton{{background:transparent;border:0;border-radius:0;"
                f"padding:16px 13px;color:{R_SOLUK};font-weight:500;}}"
                f"QPushButton:hover{{color:{R_METIN};}}")

    def _durum_yaz(self):
        d = self.depo
        self.statusBar().showMessage(
            f"  {len(d.icerikler)} içerik  ·  {len(d.favoriler)} favori  ·  "
            f"{len(d.ilerleme)} izlemeye devam  ·  {len(d.kaynaklar)} kaynak")

    # ── gezinme ──
    def sekme_ac(self, anahtar: str):
        self.aktif_sekme = anahtar
        for a, b in self.sekme_dugmeleri.items():
            b.setChecked(a == anahtar)
            b.setStyleSheet(self._sekme_stili(a == anahtar))
        if anahtar == "home":
            self._anasayfa_ciz()
            self.yigin.setCurrentIndex(0)
        else:
            adlar = dict(SEKMELER)
            if anahtar == "queue":
                icerik = self.depo.kuyruk_icerikleri()
            else:
                icerik = self.depo.kategoriye_gore(anahtar)
            q = self.arama.text().strip()
            if q:
                # Sekme içinde arama yapılırken de dizi bölümleri tek karta
                # indirilmeli; eskiden bu yol `_dizileri_tamamla` çağırmıyordu.
                icerik = self._dizileri_tamamla(self.depo.ara(q, icerik))
            self.izgara.doldur(adlar.get(anahtar, anahtar), icerik,
                               self.depo.gruplar(anahtar))
            self.yigin.setCurrentIndex(1)

    def _anasayfa_ciz(self):
        while self.raf_v.count():
            it = self.raf_v.takeAt(0)
            w = it.widget() if it else None
            if w:
                # Önce ağaçtan kopar: deleteLater tek başına yetmiyor,
                # silinene kadar widget çizilmeye devam edip çift görünüyordu.
                w.hide(); w.setParent(None); w.deleteLater()

        d = self.depo
        if not d.icerikler:
            self.vitrin.ayarla([])
            bos = QLabel("Henüz içerik yok.\n\n“+ Liste” ile M3U dosyası ya da adresi ekleyin,\n"
                         "veya hazır ücretsiz kaynaklardan seçin.")
            bos.setAlignment(Qt.AlignmentFlag.AlignCenter)
            bos.setStyleSheet(f"color:{R_SOLUK};font-size:15px;padding:80px;")
            self.raf_v.addWidget(bos)
            return

        # ÜST VİTRİN — tek öneri bandı (Hero kaldırıldı).
        # Öneriler izleme geçmişindeki türlere göre seçilir; hiç geçmiş
        # yoksa öne çıkan içeriklerden bir seçki gösterilir.
        devam = d.izlemeye_devam()
        oneri = self._onerilenler()
        if not oneri:
            havuz = [e for e in (d.kategoriye_gore("movie") or d.icerikler)
                     if e.kategori != "live"]
            if havuz:
                import random
                secki = random.sample(havuz, min(8, len(havuz)))
                oneri = diziye_grupla(secki)[:8]
        self.vitrin.ayarla(oneri)

        if devam:
            # Yatay (16:9) kartlar: dikey afiş yerine geniş sinema oranı.
            # Kart yarım kalan bölümü gösterir ama TIKLANINCA dizinin
            # tamamı açılsın diye grup, tüm bölümlerle kurulur.
            # HATA DÜZELTMESİ: aynı dizinin birden çok bölümü yarım kalınca
            # her bölüm için ayrı kart üretiliyordu (ölçüm: Breaking Bad'in
            # 3 bölümü → aynı afişten 3 kart). Artık dizi başına TEK kart
            # çıkıyor; kartta en son izlenen bölüm gösteriliyor.
            devam_gruplar = []
            gorulen: set[str] = set()
            for e, _p in devam:
                anahtar = e.dizi_anahtari()
                if anahtar:
                    if anahtar in gorulen:
                        continue          # bu dizi zaten eklendi
                    gorulen.add(anahtar)
                hepsi = d.tum_bolumler(e)
                if len(hepsi) > 1:
                    devam_gruplar.append({
                        "baslik": anahtar or e.temiz_ad(),
                        "bolumler": hepsi, "logo": e.logo,
                        "kategori": e.kategori, "tekil": False,
                        "_devam": e,        # kartta gösterilecek bölüm
                    })
                else:
                    devam_gruplar.extend(diziye_grupla([e]))
            self.raf_v.addWidget(Raf("⏯  İzlemeye Devam Et", devam_gruplar,
                                     d, self.detay_ac))
        kuy = d.kuyruk_icerikleri()
        if kuy:
            self.raf_v.addWidget(Raf("🕑  Sonra İzle", diziye_grupla(kuy),
                                     d, self.detay_ac, yatay=True))
        fav = d.kategoriye_gore("favorites")
        if fav:
            self.raf_v.addWidget(Raf("♥  Favoriler", diziye_grupla(fav), d, self.detay_ac))
        son = d.kategoriye_gore("recent")
        if son:
            self.raf_v.addWidget(Raf("▶  Son İzlenenler", diziye_grupla(son[:30]),
                                     d, self.detay_ac))

        # NOT: "Sizin İçin Önerilenler" bandı buradan KALDIRILDI.
        # Artık sayfanın en üstündeki tek vitrin bu görevi görüyor
        # (yukarıda self.vitrin.ayarla(oneri) ile dolduruluyor).

        # 🌐 TMDB keşif rafları (gerçek afişlerle)
        self._raf_nesil = getattr(self, "_raf_nesil", 0) + 1
        self._tmdb_raf_yeri = QWidget()
        self._tmdb_raf_yeri.setAutoFillBackground(True)
        self._tmdb_raf_yeri.setStyleSheet(f"background:{R_ARKA};")
        self._tmdb_raf_v = QVBoxLayout(self._tmdb_raf_yeri)
        self._tmdb_raf_v.setContentsMargins(0, 0, 0, 0); self._tmdb_raf_v.setSpacing(2)
        self.raf_v.addWidget(self._tmdb_raf_yeri)
        QTimer.singleShot(150, self._tmdb_raflari_yukle)
        for anahtar, ad in (("movie", "🎬  Filmler"), ("series", "📺  Diziler"),
                            ("anime", "🎨  Animasyon"), ("live", "📡  Canlı TV")):
            ic = d.kategoriye_gore(anahtar)
            if ic:
                self.raf_v.addWidget(Raf(ad, diziye_grupla(ic[:60]), d, self.detay_ac))

    def _onerilenler(self, adet: int = 14) -> list[dict]:
        """
        İzleme geçmişindeki grup/tür dağılımına bakıp benzer içerik önerir.
        Zaten izlenenler ve favoriler listeden çıkarılır.
        """
        d = self.depo
        gecmis_url = set(d.son_izlenen) | set(d.ilerleme.keys())
        if not gecmis_url:
            return []
        harita = {e.url: e for e in d.icerikler}
        # hangi türleri izliyor?
        agirlik: dict[str, int] = {}
        for u in gecmis_url:
            e = harita.get(u)
            if e and e.grup:
                agirlik[e.grup] = agirlik.get(e.grup, 0) + 1
        if not agirlik:
            return []
        sevilen = {g for g, _ in sorted(agirlik.items(), key=lambda x: -x[1])[:4]}
        fav = set(d.favoriler)
        aday = [e for e in d.icerikler
                if e.grup in sevilen
                and e.url not in gecmis_url
                and e.url not in fav
                and e.kategori != "live"]
        if not aday:
            return []
        import random
        random.shuffle(aday)
        return diziye_grupla(aday[:adet * 2])[:adet]

    def _tmdb_raflari_yukle(self):
        """
        Ana sayfaya TMDB keşif raflarını ekler (trend, vizyon, en iyi…).
        Görseller gerçek afişlerdir. Ağ işi arka planda yapılır.
        """
        if not (self.tmdb and self.tmdb.hazir):
            if not self.depo.icerikler:
                return
            ipucu = QLabel("🔑  TMDB anahtarı girerseniz afişler, oyuncu kadrosu ve\n"
                           "      keşif rafları burada görünür.   ⚙ → TMDB API anahtarı")
            ipucu.setStyleSheet(f"color:{R_SOLUK};font-size:12px;padding:18px 28px;")
            try:
                self._tmdb_raf_v.addWidget(ipucu)
            except RuntimeError:
                pass
            return

        # “Bunları da İzle” artık EN ALTTA gösteriliyor. Diğer TMDB rafları
        # ağdan hangi sırayla dönerse dönsün (isci sonuçları asenkron gelir),
        # bu raf sabit bir yer tutucunun içine konur ki en alt garanti olsun.
        self._kacirdiklarin_yeri = QWidget()
        kl = QVBoxLayout(self._kacirdiklarin_yeri)
        kl.setContentsMargins(0, 0, 0, 0); kl.setSpacing(0)
        self._tmdb_raf_v.addWidget(self._kacirdiklarin_yeri)

        raflar = []
        if self.depo.ayarlar.get("oneri_rafi", True):
            raflar.append(
                ("kacirdiklarin",
                 "★  Bunları da İzle  ·  listenizde olmayan yüksek puanlılar"))
        raflar += [("trend", "🔥  Bu Hafta Trend"),
                   ("vizyon", "🎬  Vizyondakiler"),
                   ("turk", "🇹🇷  Türk Yapımları"),
                   ("enyi", "⭐  Tüm Zamanların En İyileri"),
                   ("dizi_populer", "📺  Popüler Diziler")]

        class _RafIsci(QThread):
            geldi = pyqtSignal(int, str, str, list)
            def __init__(self, istemci, tip, baslik, nesil):
                super().__init__()
                self.istemci, self.tip, self.baslik = istemci, tip, baslik
                self.nesil = nesil
            def run(self):
                try:
                    if self.tip == "kacirdiklarin":
                        # Kullanıcı isteği: bu raf yalnızca FİLM önersin,
                        # dizi karışmasın (önceden film+dizi birleştiriliyordu).
                        veri = self.istemci.yuksek_puanli(4, dizi=False)
                    else:
                        veri = self.istemci.raf(self.tip) or []
                except Exception:
                    veri = []
                self.geldi.emit(self.nesil, self.tip, self.baslik, veri)

        nesil = getattr(self, "_raf_nesil", 0)
        for tip, baslik in raflar:
            i = _RafIsci(self.tmdb, tip, baslik, nesil)
            i.geldi.connect(self._tmdb_raf_geldi)
            isci_baslat(i)          # kütükte tutulur; çalışırken yok edilmez

    # ── “Bunları da İzle” süzgeci ─────────────────────────────────────
    def _izlenmemis_yuksek_puanli(self, ogeler: list, adet: int = 30) -> list:
        """
        TMDB listesinden KÜTÜPHANEDE OLMAYANLARI seçer.

        Elenenler:
          • listenizde zaten olan yapımlar (_tmdb_bende_mi)
          • afişi olmayanlar (kart boş görünüyordu)
          • puanı 7,0'ın altındakiler (güvenlik ağı)
          • “gizlenenler” — kullanıcı kartı gizlediyse bir daha çıkmaz

        Sonuç puana göre sıralanır; en yüksek puanlı önce gelir.
        """
        try:
            gizli = set(self.depo.ayarlar.get("gizli_oneriler") or [])
        except Exception:
            gizli = set()
        secilen, gorulen = [], set()
        for o in (ogeler or []):
            try:
                kimlik = o.get("id")
                if not kimlik or kimlik in gorulen or str(kimlik) in gizli:
                    continue
                if not o.get("poster_path"):
                    continue
                if float(o.get("vote_average") or 0) < 7.0:
                    continue
                if self._tmdb_bende_mi(o):
                    continue          # zaten kütüphanede
                gorulen.add(kimlik)
                secilen.append(o)
            except Exception:
                continue
        secilen.sort(key=lambda x: float(x.get("vote_average") or 0), reverse=True)
        return secilen[:adet]

    def oneriyi_gizle(self, oge: dict):
        """“Bunları da İzle” kartını kalıcı olarak gizler (× rozeti)."""
        try:
            kimlik = str(oge.get("id") or "")
            if not kimlik:
                return
            gizli = list(self.depo.ayarlar.get("gizli_oneriler") or [])
            if kimlik not in gizli:
                gizli.append(kimlik)
                del gizli[500:]           # sınırsız büyümesin
                self.depo.ayarlar["gizli_oneriler"] = gizli
                self.depo.kaydet()
            ad = oge.get("title") or oge.get("name") or "Öneri"
            self.statusBar().showMessage(
                f"“{ad}” önerilerden gizlendi  ·  ⚙ → Gizlenen önerileri sıfırla", 6000)
            self._anasayfa_ciz()
        except Exception as e:
            print(f"[MediaBox] öneri gizlenemedi: {type(e).__name__}: {e}")

    def _tmdb_raf_geldi(self, nesil: int, tip: str, baslik: str, ogeler: list):
        # Bayat sonuç: ana sayfa yeniden çizilmiş, bu işçi eski nesle ait.
        if nesil != getattr(self, "_raf_nesil", 0):
            return
        if not ogeler:
            return
        gizle_cb = None
        if tip == "kacirdiklarin":
            # Kütüphanede olanları ayıkla. Hepsi elenirse raf HİÇ eklenmez
            # (boş başlık göstermek kötü görünüyordu).
            ogeler = self._izlenmemis_yuksek_puanli(ogeler)
            if not ogeler:
                return
            gizle_cb = self.oneriyi_gizle
        try:
            # Kullanıcı isteği: kaydırmalı raf yok. TmdbRaf sarmalanan ızgara
            # kullanır (yan yana en çok 10, kalanı alt satırda).
            kap = TmdbRaf(baslik, ogeler, self.gorsel_klasor,
                          self.tmdb_icerik_ac, depo=self.depo,
                          oto_url_cb=self.tmdb_oto_url,
                          bende_mi=self._tmdb_bende_mi,
                          puan_goster=(tip == "kacirdiklarin"),
                          gizle_cb=gizle_cb)
            # “Bunları da İzle” → sabit alt yer tutucunun içine konur,
            # böylece diğer raflar ne zaman gelirse gelsin bu raf hep en
            # altta kalır. Diğer tüm raflar normal şekilde üstüne eklenir.
            yer = getattr(self, "_kacirdiklarin_yeri", None)
            if tip == "kacirdiklarin":
                if yer is not None:
                    yer.layout().addWidget(kap)
                else:
                    self._tmdb_raf_v.addWidget(kap)
            else:
                # Diğer raflar yer tutucunun HEP ÖNÜNE eklenir; böylece
                # hangi sırayla gelirlerse gelsinler, "Bunları da İzle"
                # yer tutucusu (ve içeriği) her zaman en altta kalır.
                if yer is not None:
                    idx = self._tmdb_raf_v.indexOf(yer)
                    self._tmdb_raf_v.insertWidget(idx if idx >= 0 else -1, kap)
                else:
                    self._tmdb_raf_v.addWidget(kap)
        except RuntimeError:
            pass      # sayfa değişmişse sessizce geç

    def detay_ac(self, grup: dict):
        self.detay.goster(grup)
        self.yigin.setCurrentIndex(2)

    def geri(self):
        self.sekme_ac(self.aktif_sekme)

    def oyuncu_ac(self, kisi: dict):
        """Oyuncu kadrosundan bir isme tıklanınca."""
        self.oyuncu.goster(kisi)
        self.yigin.setCurrentIndex(4)

    def tmdb_icerik_ac(self, oge: dict):
        """
        TMDB kartına (benzer içerik / filmografi) tıklanınca.
        Kullanıcının listesinde varsa detayını açar; yoksa bilgi verir.
        """
        baslik = oge.get("title") or oge.get("name") or ""
        yil = (oge.get("release_date") or oge.get("first_air_date") or "")[:4]
        bulunan = self._listede_bul(baslik, yil)
        if bulunan:
            self.detay_ac(diziye_grupla(bulunan)[0])
            return
        c = QMessageBox(self)
        c.setWindowTitle("Listenizde yok")
        c.setText(f"<b>{baslik}</b>{f' ({yil})' if yil else ''}")
        c.setInformativeText(
            "Bu yapım içerik listenizde bulunamadı.\n\n"
            "Fragmanını hemen izleyebilir, ya da bir M3U listesi ekleyip\n"
            "“⚡ Otomatik URL ekle” ile bağlantı üretebilirsiniz.")
        b_frag = c.addButton("▶  Fragmanı izle", QMessageBox.ButtonRole.AcceptRole)
        c.addButton("Tamam", QMessageBox.ButtonRole.RejectRole)
        c.exec()
        if c.clickedButton() is b_frag:
            self.tmdb_fragman_oynat(oge)

    def tmdb_fragman_oynat(self, oge: dict):
        """
        TMDB kartından fragman izleme (yapım listenizde olmasa da).
        Fragman listesi arka planda çekilir, ilki oynatıcıda açılır.
        """
        if not (self.tmdb and self.tmdb.hazir):
            return
        tid = oge.get("id") or 0
        if not tid:
            return
        dizi = bool(oge.get("name")) and not oge.get("title")
        baslik = oge.get("title") or oge.get("name") or "Fragman"
        self.statusBar().showMessage(f"🔎 {baslik} fragmanı aranıyor…", 8000)

        class _Isci(QThread):
            hazir = pyqtSignal(object)

            def __init__(self, ist, tid, dz):
                super().__init__()
                self.ist, self.tid, self.dz = ist, tid, dz

            def run(self):
                try:
                    from fragman import fragmanlari_getir
                    self.hazir.emit(fragmanlari_getir(self.ist, self.tid, self.dz))
                except Exception:
                    self.hazir.emit([])

        def geldi(liste):
            self._durum_yaz()
            if not liste:
                QMessageBox.information(self, "Fragman yok",
                                        f"{baslik} için TMDB'de fragman bulunamadı.")
                return
            from mediabox_qt import Icerik
            from mpv_islem import ytdl_var_mi
            f = liste[0]
            if not ytdl_var_mi():
                import webbrowser
                webbrowser.open(f["url"])
                self.statusBar().showMessage("Fragman tarayıcıda açıldı", 6000)
                return
            oge2 = Icerik(ad=f"{baslik} — {f['ad']}"[:90], url=f["url"],
                          kategori="movie", grup="Fragman")
            self.oynat(oge2, [oge2], 0)

        i = _Isci(self.tmdb, tid, dizi)
        i.hazir.connect(geldi)
        isci_baslat(i)

    # ── HIZLI ARAMA İNDEKSİ ────────────────────────────────────────
    # SORUN: Her TMDB kartı "bu bende var mı?" diye TÜM listeyi tarıyordu.
    # Ölçüm (7.000 içerik): tek arama 178 ms → 100 kart = 18 saniye DONMA.
    # ÇÖZÜM: bir kez sözlük indeksi kur, sorgular O(1) olsun.
    def _indeks_kur(self):
        ix = {}
        for e in self.depo.icerikler:
            for ad in (e.temiz_ad(), e.dizi_anahtari()):
                if ad:
                    ix.setdefault(tr_sadelestir(ad), []).append(e)
        self._arama_ix = ix
        self._ix_boyut = len(self.depo.icerikler)

    def _indeks(self):
        # İçerik sayısı değiştiyse indeksi tazele
        if (not hasattr(self, "_arama_ix")
                or self._ix_boyut != len(self.depo.icerikler)):
            self._indeks_kur()
        return self._arama_ix

    def _tmdb_bende_mi(self, oge: dict) -> bool:
        """TMDB içeriği kullanıcının listesinde var mı? (indeksten, O(1))"""
        baslik = oge.get("title") or oge.get("name") or ""
        hedef = tr_sadelestir(temiz_baslik(baslik))
        return bool(hedef) and hedef in self._indeks()

    def tmdb_oto_url(self, oge: dict):
        """
        Kart üzerindeki '⚡ OTO' rozetinden otomatik URL ekleme.
        TMDB detayını (IMDb kimliği için) çekip sağlayıcı penceresini açar.
        """
        if not (self.tmdb and self.tmdb.hazir):
            QMessageBox.information(self, "TMDB gerekli",
                                    "Önce TMDB anahtarı girin:\n⚙ → TMDB API anahtarı")
            return
        # Varsayılan sağlayıcıları (yoksa) yükle
        saglayicilari_hazirla(self.depo)

        baslik = oge.get("title") or oge.get("name") or "?"
        dizi = bool(oge.get("name")) and not oge.get("title")
        self.statusBar().showMessage(f"{baslik} için TMDB bilgisi alınıyor…")

        class _Isci(QThread):
            hazir = pyqtSignal(object)
            def __init__(self, ist, tid, dz):
                super().__init__(); self.ist, self.tid, self.dz = ist, tid, dz
            def run(self):
                try:
                    self.hazir.emit(self.ist.detay(self.tid, self.dz))
                except Exception:
                    self.hazir.emit(None)

        def geldi(detay):
            self._durum_yaz()
            if not detay:
                QMessageBox.warning(self, "Alınamadı", "TMDB detayı alınamadı.")
                return
            sahte = {"baslik": baslik, "bolumler": [Icerik(ad=baslik)], "tekil": not dizi}
            d = OtoUrlDialog(self.depo, detay, sahte, self)
            if d.exec() == QDialog.DialogCode.Accepted and d.uretilen:
                yeni = Icerik(
                    ad=f"{baslik} ({(detay.get('release_date') or detay.get('first_air_date') or '')[:4]})".strip(),
                    url=d.uretilen,
                    grup="TMDB ile eklenen",
                    kategori="series" if dizi else "movie",
                    kaynak="Otomatik URL",
                    tmdb_id=detay.get("id", 0) or 0,
                    tarayici=getattr(d, "tarayicida", False),
                    logo=afis_url(detay.get("poster_path", "")) if detay.get("poster_path") else "",
                    eklenme=time.time())
                self.depo.icerikler.append(yeni)
                self.depo.kaydet()
                self.sekme_ac(self.aktif_sekme)
                self._durum_yaz()
                QMessageBox.information(self, "Eklendi",
                                        f"{yeni.ad}\n\n{d.uretilen}")

        _i = _Isci(self.tmdb, oge.get("id"), dizi)
        _i.hazir.connect(geldi)
        isci_baslat(_i)

    def _listede_bul(self, baslik: str, yil: str = ""):
        """Kullanıcının listesinde bu yapım var mı?"""
        hedef = tr_sadelestir(temiz_baslik(baslik))
        if not hedef:
            return []
        tam = self._indeks().get(hedef)
        if tam:
            return list(tam)
        # Tam eşleşme yoksa kısmi ara (yalnızca burada tarama yapılır)
        return [e for e in self.depo.icerikler
                if hedef in tr_sadelestir(e.temiz_ad())][:40]

    def oynat(self, icerik: Icerik, liste=None, sira=0):
        """
        İçeriği oynatır.

        SONRAKİ BÖLÜM DÜZELTMESİ
            Liste verilmediğinde (ya da tek öğelikse) dizi bölümlerinde
            "sıradaki bölüm" oluşmuyor ve otomatik geçiş çalışmıyordu.
            Bu yüzden bölüm oynatılırken liste, dizinin TÜM bölümleriyle
            tamamlanır ve sıra numarası ona göre bulunur.
        """
        # "Tarayıcıda aç" işaretli sağlayıcılardan gelen bağlantılar mpv ile
        # oynatılamaz (sayfa içine gömülü oynatıcılardır). Bunları sistem
        # tarayıcısına gönderiyoruz.
        if getattr(icerik, "tarayici", False):
            import webbrowser
            webbrowser.open(icerik.url)
            self.statusBar().showMessage(f"Tarayıcıda açıldı:  {icerik.ad}", 6000)
            return

        if (not liste or len(liste) <= 1) and (icerik.dizi_kok_anahtari() or icerik.dizi_anahtari()):
            hepsi = self.depo.tum_bolumler(icerik)
            if len(hepsi) > 1:
                liste = hepsi
                try:
                    sira = next(i for i, b in enumerate(hepsi) if b.url == icerik.url)
                except StopIteration:
                    sira = 0
        self.ustbar.setVisible(False)
        self.yigin.setCurrentIndex(3)
        self.oynatici.oynat(icerik, liste, sira)

    def oynatici_kapandi(self):
        """
        Oynatıcı kapandı → önceki sayfaya dön.

        DAYANIKLILIK: Sayfa değişimi EN ÖNCE ve kendi try'ı içinde yapılır.
        Eskiden _durum_yaz() ya da _anasayfa_ciz() bir hata atarsa (bozuk
        afiş, eksik TMDB verisi) bu slot yarıda kalıyordu; Qt slot içindeki
        istisnayı yutuyor ve kullanıcı "geri tuşu çalışmadı" diyordu.
        Artık her adım ayrı korunuyor: en kötü ihtimalle raflar tazelenmez
        ama SAYFA MUTLAKA DEĞİŞİR.
        """
        try:
            self.ustbar.setVisible(True)
        except Exception:
            pass
        try:
            # Tam ekranda izlerken Geri'ye basınca ana sayfa tam ekran
            # kalıyordu (başlık çubuğu yok, kullanıcı sıkışıyordu).
            if self.isFullScreen():
                self.showNormal()
        except Exception:
            pass
        try:
            hedef = 2 if getattr(self.detay, "grup", None) else 0
            self.yigin.setCurrentIndex(hedef)
        except Exception as e:
            print(f"[MediaBox] sayfa değiştirilemedi: {type(e).__name__}: {e}")
            try:
                self.yigin.setCurrentIndex(0)
            except Exception:
                pass
        try:
            self._durum_yaz()
        except Exception:
            pass
        # İlerleme değişmiş olabilir; ana sayfa raflarını tazele
        try:
            if self.yigin.currentIndex() == 0:
                self._anasayfa_ciz()
        except Exception as e:
            print(f"[MediaBox] ana sayfa tazelenemedi: {type(e).__name__}: {e}")

    def _esc(self):
        """
        ESC — tek ESC sahibi burasıdır.

        Önceden hem AnaPencere hem Oynatici aynı pencerede "Escape" için
        QShortcut kuruyordu. Qt bunu "ambiguous shortcut overload" sayar ve
        HİÇBİRİNİ tetiklemez — ölçüldü: oynatıcıdayken ESC'ye basınca sayfa
        3'te kalıyordu. Artık ESC yalnızca burada bağlı; oynatıcıdaysak
        onun _esc()'sine devredilir (o da önce tam ekrandan çıkar).
        """
        try:
            i = self.yigin.currentIndex()
            if i == 3:
                self.oynatici._esc()
            elif i in (2, 4):
                if self.isFullScreen():
                    self.showNormal()
                else:
                    self.geri()
            elif self.isFullScreen():
                self.showNormal()
        except Exception as e:
            print(f"[MediaBox] ESC işlenemedi: {type(e).__name__}: {e}")

    # ── arama ──
    def _arama_gecikmeli(self):
        self._arama_zaman.start()

    def _arama_uygula(self):
        """
        Arama — HİÇBİR KOŞULDA PROGRAMI DÜŞÜRMEZ.

        Bu bir Qt slotu (zamanlayıcı/sinyal üzerinden çağrılır). Slot içinde
        yakalanmayan bir istisna PyQt6'da yorumlayıcıyı sonlandırır: kullanıcı
        için bu "arama yaparken program kapandı" demektir.

        Ölçülen gerçek çökme: `ad` alanı None olan tek bir kayıt bile
        düzenli ifadeleri patlatıyordu
        (TypeError: expected string or bytes-like object, got 'NoneType').
        Kök neden `Icerik._ad_metni()` ile giderildi; burası ise son savunma
        katmanı — beklenmedik bir hata olsa da pencere açık kalsın.
        """
        try:
            self._arama_uygula_ic()
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                self.statusBar().showMessage(
                    f"⚠ Arama sırasında hata: {type(e).__name__} "
                    f"(program çalışmaya devam ediyor)", 8000)
            except Exception:
                pass

    def _arama_uygula_ic(self):
        # BUG DÜZELTMESİ: Arama 220 ms gecikmeli çalışıyor. Kullanıcı arama
        # kutusunu temizleyip hemen bir karta tıklarsa, gecikmeli çağrı
        # detay/oynatıcı sayfasını kapatıp listeye geri atıyordu.
        # Zaten detaydaysak veya video oynuyorsa sayfayı değiştirme.
        if self.yigin.currentIndex() in (2, 3):
            return
        q = self.arama.text().strip()
        if not q:
            self.sekme_ac(self.aktif_sekme)
            return
        # ÇOK KISA SORGU KORUMASI
        #   1–2 harf neredeyse TÜM kütüphaneyle eşleşir. Bu durumda arama,
        #   36.000 içeriği gruplayıp binlerce kart hazırlıyor ama ekranda
        #   yalnızca 120'si görünüyordu — boşa yapılan iş yüzünden her tuş
        #   vuruşu saniyeler sürüyor, 220 ms'lik gecikmeli çağrılar birikip
        #   program yanıt veremez hâle geliyordu ("arama yaparken kapanıyor").
        #   Kullanıcı 3. harfi yazana kadar bekliyoruz.
        if len(q) < 3:
            self.statusBar().showMessage(
                "Aramak için en az 3 harf yazın…", 2500)
            return

        sonuc = self.depo.ara(q)
        # ARAMA DÜZELTMESİ: Dizi aratınca eşleşen her bölüm ayrı kart olarak
        # çıkıyordu. Eşleşen bölümlerin ait olduğu dizilerin TÜM bölümlerini
        # toplayıp tek karta indiriyoruz (kart "12 bölüm" olarak görünür).
        sonuc = self._dizileri_tamamla(sonuc)
        self.izgara.doldur(f"“{q}” için sonuçlar", sonuc, [])
        self.yigin.setCurrentIndex(1)

    def _dizi_indeksi(self) -> dict:
        """
        {dizi_anahtarı: [bölümler]} indeksi — bir kez kurulur, saklanır.

        NEDEN: `_dizileri_tamamla` her tuş vuruşunda TÜM kütüphaneyi tarayıp
        her içerik için `dizi_anahtari()` hesaplıyordu. Ölçüm (36.000 içerik,
        aynı dizi 3 kaynakta):

            tek arama            → 4,2–4,5 sn
            arama 220 ms gecikmeli → istekler birikiyor, program yanıt vermiyor

        Kullanıcının "arama yaparken program kapanıyor" dediği durum buydu.
        İndeksle aynı iş 0,00 sn'ye iniyor.
        """
        # İNDEKS TAZELEME — yalnızca uzunluğa bakmak YETMEZ.
        #   Kaynak yenilendiğinde liste tamamen değişip aynı uzunlukta
        #   kalabiliyor. O durumda bayat indeks kullanılıyordu: yeni içerik
        #   aramada HİÇ çıkmıyordu (ölçüldü: 10 kayıt yenilendi → arama 0
        #   sonuç verdi). Listenin kimliğini de karşılaştırıyoruz.
        liste = self.depo.icerikler
        imza = (len(liste), id(liste),
                id(liste[0]) if liste else 0,
                id(liste[-1]) if liste else 0)
        if getattr(self, "_dx_imza", None) == imza and getattr(self, "_dx", None):
            return self._dx
        dx: dict[str, list] = {}
        for e in liste:
            try:
                # Kök ad (grup/kalite bağımsız) — diziye_grupla ile aynı mantık
                a = (e.dizi_kok_anahtari() or e.dizi_anahtari() or "").strip()
                if a:
                    from mediabox_qt import tr_sadelestir
                    a = tr_sadelestir(a)
            except Exception:
                continue          # bozuk kayıt indeksi düşürmesin
            if a:
                dx.setdefault(a, []).append(e)
        self._dx = dx
        self._dx_imza = imza
        self._dx_boyut = len(liste)
        return dx

    def _dizileri_tamamla(self, icerikler: list) -> list:
        """
        Sonuçtaki dizi bölümlerini, o dizinin tüm bölümleriyle tamamlar.

        Böylece `diziye_grupla` tek bir dizi kartı üretir; kullanıcı arama
        sonucunda bölüm bölüm liste yerine dizi afişini görür.
        """
        if not icerikler:
            return icerikler
        anahtarlar = set()
        tekiller = []
        for e in icerikler:
            a = (e.dizi_kok_anahtari() or e.dizi_anahtari() or "").strip()
            if a:
                from mediabox_qt import tr_sadelestir
                anahtarlar.add(tr_sadelestir(a))
            else:
                tekiller.append(e)
        if not anahtarlar:
            return icerikler

        dx = self._dizi_indeksi()
        ekli = {id(x) for x in tekiller}
        cikti = list(tekiller)
        for a in anahtarlar:
            for e in dx.get(a, ()):
                if id(e) not in ekli:
                    cikti.append(e)
                    ekli.add(id(e))
        return cikti

    # ── liste ekleme ──
    def liste_menusu(self):
        m = QMenu(self)
        m.addAction("📂  M3U dosyası aç…", self.m3u_ac)
        m.addAction("🌐  Adresten yükle…", self.url_yukle)
        m.addAction("📡  Xtream Codes…", self.xtream_ekle)
        m.addAction("🔗  Tek video/URL ekle…", self.tekli_video_ekle)
        m.addAction("📁  Klasör tara…", self.klasor_tara)
        m.addAction("☁  Dropbox'tan içe aktar…", self.dropbox_ac)
        m.addSeparator()
        m.addAction("🎁  Ücretsiz kaynaklar…", self.ucretsiz_kaynaklar)
        m.addAction("🗂  Kaynak yöneticisi…", self.kaynak_yonetici)
        m.addSeparator()
        m.addAction("💾  Listeyi dışa aktar…", self.disa_aktar)
        m.addAction("📥  HTML sürümünden içe aktar…", self.html_aktar)
        m.addSeparator()
        m.addAction("🔍  Ölü linkleri kontrol et…", self.link_kontrol)
        m.addAction("🗑  Tüm içeriği temizle", self.temizle)
        m.exec(self.sender().mapToGlobal(self.sender().rect().bottomLeft()))

    def m3u_ac(self):
        yol, _ = QFileDialog.getOpenFileName(
            self, "M3U dosyası seç", "", "Çalma listesi (*.m3u *.m3u8 *.txt);;Tüm dosyalar (*)")
        if yol:
            self.m3u_dosya_yukle(yol)

    def m3u_dosya_yukle(self, yol: str):
        try:
            metin = Path(yol).read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            QMessageBox.critical(self, "Hata", f"Dosya okunamadı:\n{e}")
            return
        self._icerik_ekle(metin, Path(yol).name)

    def url_yukle(self):
        url, ok = QInputDialog.getText(self, "Adresten yükle", "M3U adresi:")
        if not (ok and url.strip()):
            return
        url = url.strip()
        self.statusBar().showMessage("İndiriliyor…")
        self._indirici = Indirici(url)
        self._indirici.ilerleme.connect(lambda s: self.statusBar().showMessage(s))
        def bitti(metin, hata):
            if hata:
                QMessageBox.critical(self, "İndirilemedi", hata)
                self._durum_yaz(); return
            self._icerik_ekle(metin, url, url_kaynak=url)
        self._indirici.bitti.connect(bitti)
        self._indirici.start()

    def xtream_ekle(self):
        """
        Xtream Codes panel bilgisiyle liste indirir.

        Sunucu + kullanıcı + şifre → get.php M3U (m3u_plus) üretilir ve
        mevcut liste yükleme yoluna verilir. Bilgiler ayarlara kaydedilir
        (sonraki açılışta form dolu gelir).
        """
        kayit = self.depo.ayarlar.get("xtream") or {}
        d = QDialog(self)
        d.setWindowTitle("Xtream Codes")
        d.setMinimumWidth(480)
        v = QVBoxLayout(d)
        v.addWidget(QLabel(
            "Panel bilgilerinizi girin. Liste <b>get.php</b> üzerinden M3U olarak alınır."
        ))

        form = QVBoxLayout()
        form.setSpacing(8)

        def satir(etiket, widget):
            h = QHBoxLayout()
            lb = QLabel(etiket)
            lb.setFixedWidth(100)
            h.addWidget(lb)
            h.addWidget(widget, 1)
            form.addLayout(h)

        host_k = QLineEdit(kayit.get("host", ""))
        host_k.setPlaceholderText("örn. http://ornek.com:8080  veya  ornek.com:8080")
        user_k = QLineEdit(kayit.get("user", ""))
        pass_k = QLineEdit(kayit.get("password", ""))
        pass_k.setEchoMode(QLineEdit.EchoMode.Password)
        goster = QCheckBox("Şifreyi göster")
        goster.toggled.connect(
            lambda k: pass_k.setEchoMode(
                QLineEdit.EchoMode.Normal if k else QLineEdit.EchoMode.Password))

        tur_k = QComboBox()
        tur_k.addItem("Tümü (canlı + film + dizi)", "m3u_plus")
        tur_k.addItem("Yalnızca canlı TV", "live")
        tur_k.addItem("Yalnızca film (VOD)", "movie")
        tur_k.addItem("Yalnızca dizi", "series")
        onceki_tur = kayit.get("type", "m3u_plus")
        for i in range(tur_k.count()):
            if tur_k.itemData(i) == onceki_tur:
                tur_k.setCurrentIndex(i)
                break

        cikti_k = QComboBox()
        cikti_k.addItem("TS / MPEGTS (önerilen)", "ts")
        cikti_k.addItem("HLS (m3u8)", "hls")
        onceki_out = kayit.get("output", "ts")
        for i in range(cikti_k.count()):
            if cikti_k.itemData(i) == onceki_out:
                cikti_k.setCurrentIndex(i)
                break

        satir("Sunucu", host_k)
        satir("Kullanıcı", user_k)
        satir("Şifre", pass_k)
        form.addWidget(goster)
        satir("İçerik", tur_k)
        satir("Çıktı", cikti_k)
        v.addLayout(form)

        bilgi = QLabel(
            "Not: Bazı paneller yalnızca “Tümü” tipini destekler. "
            "Bağlantı başarısız olursa çıktıyı HLS deneyin."
        )
        bilgi.setWordWrap(True)
        bilgi.setStyleSheet(f"color:{R_SOLUK};font-size:11px;")
        v.addWidget(bilgi)

        alt = QHBoxLayout()
        b_iptal = QPushButton("İptal")
        b_yukle = QPushButton("Listeyi yükle")
        b_yukle.setStyleSheet(
            f"background:{self.depo.ayarlar.get('vurgu', R_VURGU)};"
            "border:0;border-radius:8px;font-weight:700;padding:8px 16px;")
        alt.addStretch()
        alt.addWidget(b_iptal)
        alt.addWidget(b_yukle)
        v.addLayout(alt)
        b_iptal.clicked.connect(d.reject)

        def normalize_host(h: str) -> str:
            h = (h or "").strip().rstrip("/")
            if not h:
                return ""
            if "://" not in h:
                h = "http://" + h
            # Yanlışlıkla get.php / player_api yapıştırılmışsa kök al
            for kes in ("/get.php", "/player_api.php", "/panel_api.php",
                        "/live/", "/movie/", "/series/"):
                if kes in h.lower():
                    h = h[:h.lower().index(kes)]
                    break
            return h.rstrip("/")

        def yukle():
            host = normalize_host(host_k.text())
            user = user_k.text().strip()
            parola = pass_k.text()
            tip = tur_k.currentData()
            output = cikti_k.currentData()
            if not host or not user or not parola:
                QMessageBox.warning(d, "Eksik bilgi",
                                    "Sunucu, kullanıcı ve şifre gerekli.")
                return
            from urllib.parse import quote
            # Xtream get.php — type: m3u_plus | m3u  (+ bazı panellerde filtre yok)
            # Canlı/film/dizi ayrımı panel tarafında her zaman desteklenmez;
            # m3u_plus tam listeyi verir. Özel tip seçildiyse yine m3u_plus
            # çekilir; kategori filtreleme kullanıcıya bırakılır.
            api_type = "m3u_plus"
            m3u_url = (
                f"{host}/get.php?username={quote(user)}"
                f"&password={quote(parola)}"
                f"&type={api_type}&output={output}"
            )
            kaynak_adi = f"Xtream: {user}@{host.split('://', 1)[-1]}"

            # Kaydet (sonraki açılış)
            self.depo.ayarlar["xtream"] = {
                "host": host, "user": user, "password": parola,
                "type": tip, "output": output,
            }
            self.depo.kaydet()
            d.accept()

            self.statusBar().showMessage("Xtream listesi indiriliyor…")
            self._indirici = Indirici(m3u_url)

            def ilerleme(s, _tip=tip):
                self.statusBar().showMessage(f"Xtream: {s}")

            def bitti(metin, hata, _tip=tip, _adi=kaynak_adi, _url=m3u_url):
                if hata:
                    QMessageBox.critical(
                        self, "Xtream indirilemedi",
                        f"{hata}\n\nSunucu adresini, portu ve bilgileri kontrol edin.")
                    self._durum_yaz()
                    return
                if not metin or "#EXTM3U" not in metin[:500] and "#EXTINF" not in metin:
                    # Bazı paneller hata JSON/HTML döner
                    oniz = (metin or "")[:300].replace("\n", " ")
                    QMessageBox.warning(
                        self, "Geçersiz yanıt",
                        "Sunucu M3U listesi döndürmedi.\n\n"
                        f"Önizleme:\n{oniz}")
                    self._durum_yaz()
                    return
                # İsteğe bağlı kaba filtre (canlı / film / dizi)
                if _tip in ("live", "movie", "series"):
                    metin = self._xtream_m3u_filtrele(metin, _tip)
                self._icerik_ekle(metin, _adi, url_kaynak=_url)

            self._indirici.ilerleme.connect(ilerleme)
            self._indirici.bitti.connect(bitti)
            self._indirici.start()

        b_yukle.clicked.connect(yukle)
        d.exec()

    def _xtream_m3u_filtrele(self, metin: str, tip: str) -> str:
        """
        Xtream m3u_plus listesinde kaba canlı/film/dizi süzmesi.

        Panel group-title ve URL yollarına bakar; emin olunamayan satırlar
        listede bırakılır (kaçırmamak için).
        """
        satirlar = metin.splitlines()
        sonuc = ["#EXTM3U"]
        i = 0
        while i < len(satirlar):
            sat = satirlar[i]
            if sat.startswith("#EXTINF"):
                ad = sat
                url = satirlar[i + 1] if i + 1 < len(satirlar) else ""
                i += 2
                low = (ad + " " + url).lower()
                grup = ""
                if "group-title=" in low:
                    try:
                        # orijinal satırdan group-title
                        import re as _re
                        m = _re.search(r'group-title="([^"]*)"', ad, _re.I)
                        if m:
                            grup = m.group(1).lower()
                    except Exception:
                        pass
                tut = True
                if tip == "live":
                    # film/dizi ipuçları varsa ele
                    if any(x in low for x in ("/movie/", "/series/", "vod", "film", "dizi", "serie")):
                        if "/live/" not in low and "tv" not in grup:
                            tut = False
                elif tip == "movie":
                    if any(x in low for x in ("/live/", "/series/")) and "/movie/" not in low:
                        tut = False
                    if any(x in grup for x in ("dizi", "serie", "show", "tv |", "| tv")):
                        if "film" not in grup and "movie" not in grup and "vod" not in grup:
                            tut = False
                elif tip == "series":
                    if any(x in low for x in ("/live/", "/movie/")) and "/series/" not in low:
                        tut = False
                if tut and url and not url.startswith("#"):
                    sonuc.append(ad)
                    sonuc.append(url)
            else:
                i += 1
        return "\n".join(sonuc) + "\n"

    def tekli_video_ekle(self):
        """
        Tek bir video/HLS URL'sini doğrudan içeriğe ekler — M3U listesi
        olarak AYRIŞTIRMADAN. .txt/.m3u8/uzantısız gibi VLC'de çalışan ama
        program içinde bir "kanal listesi" (#EXTINF) formatında OLMAYAN
        tekil bağlantılar için (ör. bir HLS master playlist adresi).
        """
        d = QDialog(self)
        d.setWindowTitle("Tek video/URL ekle")
        d.setMinimumWidth(460)
        v = QVBoxLayout(d); v.setSpacing(10)

        v.addWidget(QLabel("Video adı:"))
        ad_kutu = QLineEdit()
        v.addWidget(ad_kutu)

        v.addWidget(QLabel("Video URL'si (mp4 / m3u8 / .txt uzantılı HLS vb.):"))
        url_kutu = QLineEdit()
        url_kutu.setPlaceholderText("https://…")
        v.addWidget(url_kutu)

        v.addWidget(QLabel("Hangi bölüme eklensin?"))
        kutu = QComboBox()
        kutu.addItem("Filmler", "movie")
        kutu.addItem("Diziler", "series")
        kutu.addItem("Animasyon", "anime")
        kutu.addItem("Canlı TV", "live")
        v.addWidget(kutu)

        h = QHBoxLayout()
        iptal = QPushButton("İptal"); tamam = QPushButton("Ekle")
        tamam.setStyleSheet(f"background:{self.depo.ayarlar.get('vurgu', R_VURGU)};"
                            "border:0;border-radius:8px;font-weight:700;padding:9px 22px;")
        tamam.setDefault(True)
        h.addStretch(); h.addWidget(iptal); h.addWidget(tamam)
        v.addLayout(h)
        iptal.clicked.connect(d.reject); tamam.clicked.connect(d.accept)

        if d.exec() != QDialog.DialogCode.Accepted:
            return
        url = url_kutu.text().strip()
        if not url:
            return
        ad = ad_kutu.text().strip() or url.rsplit("/", 1)[-1].split("?")[0] or url

        icerik = Icerik(ad=ad, url=url, kategori=kutu.currentData(), kaynak="Tekli ekleme",
                        eklenme=time.time())
        self._listeye_ekle([icerik], "Tekli ekleme", url_kaynak=url)

    def dropbox_ac(self):
        """
        Dropbox hesabına bağlanma / bağlıysa içe aktarma penceresi.

        Akış:
          1) Bağlı değilse: App key girilir, tarayıcıda onay sayfası
             açılır, Dropbox'ın verdiği kod yapıştırılır → refresh_token
             kalıcı olarak kaydedilir (yalnızca refresh_token diske yazılır,
             access_token her seferinde tazelenir).
          2) Bağlıysa: "Şimdi içe aktar" hesaptaki TÜM .m3u/.m3u8
             dosyalarını bulup indirir ve kütüphaneye ekler.
        """
        d = QDialog(self); d.setWindowTitle("Dropbox"); d.setMinimumWidth(460)
        v = QVBoxLayout(d); v.setSpacing(10)

        app_key = self.depo.ayarlar.get("dropbox_app_key", "")
        refresh_token = self.depo.ayarlar.get("dropbox_refresh_token", "")
        bagli = bool(app_key and refresh_token)

        durum_etiket = QLabel()
        v.addWidget(durum_etiket)

        # ── Bağlı değilse: kurulum alanı ──
        anahtar_kutu = QLineEdit(app_key)
        anahtar_kutu.setPlaceholderText("Dropbox App key (dropbox.com/developers/apps)")
        kod_kutu = QLineEdit()
        kod_kutu.setPlaceholderText("Tarayıcıda onayladıktan sonra çıkan kodu buraya yapıştırın")
        b_ac = QPushButton("1) Tarayıcıda onay sayfasını aç")
        b_baglan = QPushButton("2) Kodu doğrula ve bağlan")
        kod_kutu.setVisible(False); b_baglan.setVisible(False)

        yardim = QLabel(
            '<a href="https://www.dropbox.com/developers/apps">App key almak için '
            'buraya tıklayın</a> — "Scoped access" + "Full Dropbox" seçin, '
            '"files.metadata.read" ve "files.content.read" izinlerini işaretleyip '
            'kaydedin. Oluşan "App key" değerini yukarı yapıştırın.')
        yardim.setOpenExternalLinks(True); yardim.setWordWrap(True)
        yardim.setStyleSheet(f"color:{R_SOLUK};font-size:11px;")

        kurulum = QWidget(); kv = QVBoxLayout(kurulum); kv.setContentsMargins(0, 0, 0, 0)
        kv.addWidget(yardim)
        kv.addWidget(anahtar_kutu)
        kv.addWidget(b_ac)
        kv.addWidget(kod_kutu)
        kv.addWidget(b_baglan)
        v.addWidget(kurulum)

        # ── Bağlıysa: içe aktarma alanı ──
        bagli_alan = QWidget(); bv = QVBoxLayout(bagli_alan); bv.setContentsMargins(0, 0, 0, 0)
        b_iceri_aktar = QPushButton("🔄  Şimdi Dropbox'tan içe aktar")
        b_baglanti_kes = QPushButton("Bağlantıyı kaldır")
        bv.addWidget(b_iceri_aktar); bv.addWidget(b_baglanti_kes)
        v.addWidget(bagli_alan)

        ilerleme = QLabel(""); ilerleme.setStyleSheet(f"color:{R_SOLUK};font-size:11px;")
        v.addWidget(ilerleme)

        kapat = QPushButton("Kapat"); v.addWidget(kapat)
        kapat.clicked.connect(d.accept)

        def goruntu_guncelle():
            nonlocal bagli
            bagli = bool(self.depo.ayarlar.get("dropbox_app_key") and
                        self.depo.ayarlar.get("dropbox_refresh_token"))
            kurulum.setVisible(not bagli)
            bagli_alan.setVisible(bagli)
            if bagli:
                ad = self.depo.ayarlar.get("dropbox_hesap_adi", "")
                durum_etiket.setText(f"✅  Dropbox bağlı" + (f"  ·  {ad}" if ad else ""))
            else:
                durum_etiket.setText("Dropbox hesabınız henüz bağlı değil.")
        goruntu_guncelle()

        self._db_verifier = ""

        def adim1_ac():
            anahtar = anahtar_kutu.text().strip()
            if not anahtar:
                QMessageBox.warning(d, "Eksik", "Önce App key girin.")
                return
            try:
                from dropbox_kaynak import yetkilendirme_baslat
                verifier, _ = yetkilendirme_baslat(anahtar)
            except Exception as e:
                QMessageBox.critical(d, "Hata", str(e)); return
            self._db_verifier = verifier
            self.depo.ayarlar["dropbox_app_key"] = anahtar
            kod_kutu.setVisible(True); b_baglan.setVisible(True)
            ilerleme.setText("Tarayıcıda onayladıktan sonra çıkan kodu yapıştırıp "
                            "'Kodu doğrula ve bağlan'a basın.")
        b_ac.clicked.connect(adim1_ac)

        def adim2_dogrula():
            kod = kod_kutu.text().strip()
            anahtar = anahtar_kutu.text().strip()
            if not kod:
                return
            ilerleme.setText("Doğrulanıyor…")
            b_baglan.setEnabled(False)

            class _Isci(QThread):
                bitti = pyqtSignal(dict, str)
                def run(self):
                    try:
                        from dropbox_kaynak import kod_ile_token_al
                        veri = kod_ile_token_al(anahtar, self._verifier, kod)
                        self.bitti.emit(veri, "")
                    except Exception as e:
                        self.bitti.emit({}, str(e))
            isci = _Isci(); isci._verifier = self._db_verifier

            def tamam(veri, hata):
                b_baglan.setEnabled(True)
                if hata or not veri.get("refresh_token"):
                    ilerleme.setText("")
                    QMessageBox.critical(d, "Bağlanamadı", hata or "Kod geçersiz.")
                    return
                self.depo.ayarlar["dropbox_app_key"] = anahtar
                self.depo.ayarlar["dropbox_refresh_token"] = veri["refresh_token"]
                self.depo.kaydet()
                ilerleme.setText("✅ Bağlandı.")
                goruntu_guncelle()
            isci.bitti.connect(tamam)
            self._db_isci = isci
            isci.start()
        b_baglan.clicked.connect(adim2_dogrula)

        def baglanti_kes():
            if QMessageBox.question(d, "Emin misiniz?",
                    "Dropbox bağlantısı kaldırılacak. Daha önce içe aktarılmış "
                    "içerikler kütüphanede kalır."
                ) != QMessageBox.StandardButton.Yes:
                return
            for k in ("dropbox_app_key", "dropbox_refresh_token", "dropbox_hesap_adi"):
                self.depo.ayarlar.pop(k, None)
            self.depo.kaydet()
            goruntu_guncelle()
        b_baglanti_kes.clicked.connect(baglanti_kes)

        def iceri_aktar():
            anahtar = self.depo.ayarlar.get("dropbox_app_key", "")
            rt = self.depo.ayarlar.get("dropbox_refresh_token", "")
            if not (anahtar and rt):
                return
            b_iceri_aktar.setEnabled(False)
            ilerleme.setText("Dropbox'a bağlanılıyor…")

            class _Isci(QThread):
                bitti = pyqtSignal(list, str, str)   # (icerikler, hesap_adi, hata)
                ilerle = pyqtSignal(str)
                def run(self):
                    try:
                        from dropbox_kaynak import (erisim_tokeni_yenile,
                                                    m3u_dosyalarini_bul, dosya_metni_al,
                                                    hesap_adi_al, kategori_tahmin_dosya_adindan)
                        at = erisim_tokeni_yenile(anahtar, rt)
                        hesap = hesap_adi_al(at)
                        self.ilerle.emit("Dosyalar taranıyor…")
                        dosyalar = m3u_dosyalarini_bul(at)
                        if not dosyalar:
                            self.bitti.emit([], hesap, "")
                            return
                        tumu = []
                        for i, f in enumerate(dosyalar, 1):
                            self.ilerle.emit(f"{i}/{len(dosyalar)}  {f['ad']} indiriliyor…")
                            try:
                                metin = dosya_metni_al(at, f["yol"])
                            except Exception:
                                continue
                            kat = kategori_tahmin_dosya_adindan(f["ad"])
                            yeni = m3u_ayristir(metin, kaynak=f"Dropbox: {f['ad']}", kategori=kat)
                            tumu.extend(yeni)
                        self.bitti.emit(tumu, hesap, "")
                    except Exception as e:
                        self.bitti.emit([], "", str(e))
            isci = _Isci()
            isci.ilerle.connect(lambda m: ilerleme.setText(m))

            def tamam(icerikler, hesap, hata):
                b_iceri_aktar.setEnabled(True)
                if hata:
                    ilerleme.setText("")
                    QMessageBox.critical(d, "İçe aktarılamadı", hata)
                    return
                if hesap:
                    self.depo.ayarlar["dropbox_hesap_adi"] = hesap
                if not icerikler:
                    ilerleme.setText("")
                    QMessageBox.information(d, "Bulunamadı",
                                            "Hesapta .m3u/.m3u8 dosyası bulunamadı.")
                    return
                # Önceki Dropbox içeriğinin yerini alır (yinelenmesin diye)
                self.depo.icerikler = [e for e in self.depo.icerikler
                                       if not e.kaynak.startswith("Dropbox: ")]
                self.depo.icerikler.extend(icerikler)
                zaten = {k.get("ad") for k in self.depo.kaynaklar}
                if "Dropbox (otomatik)" not in zaten:
                    self.depo.kaynaklar.append({
                        "ad": "Dropbox (otomatik)", "tur": "dropbox",
                        "url": "", "adet": len(icerikler), "zaman": time.time()})
                else:
                    for k in self.depo.kaynaklar:
                        if k.get("ad") == "Dropbox (otomatik)":
                            k["adet"] = len(icerikler); k["zaman"] = time.time()
                self.depo.kaydet()
                ilerleme.setText(f"✅ {len(icerikler)} içerik eklendi.")
                self._durum_yaz()
                if self.aktif_sekme == "home":
                    self._anasayfa_ciz()
            isci.bitti.connect(tamam)
            self._db_isci2 = isci
            isci.start()
        b_iceri_aktar.clicked.connect(iceri_aktar)

        d.exec()

    def klasor_tara(self):
        klasor = QFileDialog.getExistingDirectory(self, "Video klasörü seç")
        if not klasor:
            return
        UZANTI = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".ts", ".mpg", ".mpeg", ".flv", ".wmv"}
        kok = Path(klasor)
        yeni = []
        for p in sorted(kok.rglob("*")):
            if p.is_file() and p.suffix.lower() in UZANTI:
                yeni.append(Icerik(ad=p.stem, url=str(p), eklenme=time.time(), grup=(p.parent.name if p.parent != kok else kok.name),
                                   kategori="movie", kaynak=f"Yerel: {kok.name}"))
        if not yeni:
            QMessageBox.information(self, "Bilgi", "Klasörde video bulunamadı.")
            return
        secim = self._kategori_sor(f"Yerel: {kok.name}", yeni)
        if secim is None:
            return
        if secim != "auto":
            for e in yeni:
                e.kategori = secim
        self._listeye_ekle(yeni, f"Yerel: {kok.name}")

    def _kategori_sor(self, kaynak_adi: str, yeni: list) -> str | None:
        """
        M3U eklenirken içeriğin hangi bölüme gideceğini sorar.
        (HTML sürümündeki davranışın aynısı — eksikti, eklendi.)
        Dönen: 'auto' | 'movie' | 'series' | 'anime' | 'live'  ya da None (iptal)
        """
        # Otomatik tahmin dağılımını göster: kullanıcı bilinçli seçsin
        sayim = {}
        for e in yeni:
            k = kategori_tahmin(e.grup, e.ad)
            sayim[k] = sayim.get(k, 0) + 1
        adlar = {"movie": "Film", "series": "Dizi", "anime": "Animasyon", "live": "Canlı TV"}
        dagilim = "   ".join(f"{adlar.get(k, k)}: {v}" for k, v in
                             sorted(sayim.items(), key=lambda x: -x[1]))

        d = QDialog(self)
        d.setWindowTitle("Liste nereye eklensin?")
        d.setMinimumWidth(460)
        v = QVBoxLayout(d); v.setSpacing(12)

        bas = QLabel(f"<b>{kaynak_adi[:70]}</b>")
        bas.setWordWrap(True); v.addWidget(bas)
        alt = QLabel(f"{len(yeni)} içerik bulundu.")
        alt.setStyleSheet(f"color:{R_SOLUK};"); v.addWidget(alt)

        v.addWidget(QLabel("Bu içerikler hangi bölümde görünsün?"))
        kutu = QComboBox()
        kutu.addItem(f"Otomatik ayır  ({dagilim})", "auto")
        kutu.addItem("Hepsi → Filmler", "movie")
        kutu.addItem("Hepsi → Diziler", "series")
        kutu.addItem("Hepsi → Animasyon", "anime")
        kutu.addItem("Hepsi → Canlı TV", "live")
        v.addWidget(kutu)

        ipucu = QLabel("“Otomatik ayır”, grup adına ve S01E01 gibi ifadelere bakarak\n"
                       "her içeriği kendi bölümüne yerleştirir.")
        ipucu.setStyleSheet(f"color:{R_SOLUK};font-size:11px;")
        ipucu.setWordWrap(True); v.addWidget(ipucu)

        h = QHBoxLayout()
        iptal = QPushButton("İptal"); tamam = QPushButton("Ekle")
        tamam.setStyleSheet(f"background:{self.depo.ayarlar.get('vurgu', R_VURGU)};"
                            "border:0;border-radius:8px;font-weight:700;padding:9px 22px;")
        tamam.setDefault(True)
        h.addStretch(); h.addWidget(iptal); h.addWidget(tamam)
        v.addLayout(h)
        iptal.clicked.connect(d.reject); tamam.clicked.connect(d.accept)

        if d.exec() != QDialog.DialogCode.Accepted:
            return None
        return kutu.currentData()

    def _icerik_ekle(self, metin: str, kaynak_adi: str, url_kaynak: str = "",
                     kategori: str | None = None):
        yeni = m3u_ayristir(metin, kaynak=kaynak_adi, kategori=kategori)
        if not yeni:
            # Kanal listesi (#EXTINF) formatında değil — ama elimizde bir
            # URL varsa (Adresten yükle ile geldiyse) bu URL'nin kendisi
            # muhtemelen doğrudan oynatılabilir tek bir video/HLS akışıdır
            # (ör. .txt uzantılı bir HLS master playlist adresi). Kullanıcıya
            # bunu TEK VİDEO olarak eklemeyi öner, sessizce vazgeçme.
            if url_kaynak:
                cvp = QMessageBox.question(
                    self, "Kanal listesi bulunamadı",
                    "Bu adres bir kanal listesi (M3U) formatında değil.\n\n"
                    "Bunun yerine bu adresi TEK BİR VİDEO olarak eklemek "
                    "ister misiniz? (VLC gibi oynatıcılarda doğrudan "
                    "çalışan .txt/.m3u8 video bağlantıları için uygundur.)",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if cvp == QMessageBox.StandardButton.Yes:
                    ad = url_kaynak.rsplit("/", 1)[-1].split("?")[0] or url_kaynak
                    icerik = Icerik(ad=ad, url=url_kaynak, kategori="movie",
                                    kaynak=kaynak_adi)
                    self._listeye_ekle([icerik], kaynak_adi, url_kaynak)
                    return
            QMessageBox.warning(self, "Boş liste", "Bu listede geçerli içerik bulunamadı.")
            self._durum_yaz(); return
        secim = self._kategori_sor(kaynak_adi, yeni)
        if secim is None:
            self._durum_yaz(); return          # kullanıcı vazgeçti
        if secim != "auto":
            for e in yeni:
                e.kategori = secim
        self._listeye_ekle(yeni, kaynak_adi, url_kaynak)

    def _listeye_ekle(self, yeni: list[Icerik], kaynak_adi: str, url_kaynak: str = "",
                     eskiyi_sil: bool = False):
        """
        Listeyi depoya ekler.

        KORUMA (varsayılan): Mevcut içerik ASLA silinmez. Yalnızca yeni URL'ler
        eklenir; zaten var olanlar atlanır. Kullanıcı açıkça onaylamadıkça
        (`eskiyi_sil=True` — kaynak yöneticisi "değiştir") hiçbir kayıt
        kaldırılmaz. Otomatik yenileme de bu yolu kullanır.
        """
        if not yeni:
            QMessageBox.warning(self, "Boş liste", "Eklenecek geçerli içerik yok.")
            return

        kaynak_adi = (kaynak_adi or "").strip() or "Bilinmeyen"

        silinen = 0
        if eskiyi_sil and kaynak_adi:
            once = len(self.depo.icerikler)
            self.depo.icerikler = [
                e for e in self.depo.icerikler if e.kaynak != kaynak_adi]
            silinen = once - len(self.depo.icerikler)

        mevcut_url = {e.url for e in self.depo.icerikler}
        # Aynı dizi bölümünün URL'sini yerinde güncelle (silmeden)
        guncellenen = 0
        from mediabox_qt import tr_sadelestir

        def kimlik_dizi(e):
            try:
                sb = e.sezon_bolum()
            except Exception:
                return None
            if not sb:
                return None
            try:
                kok = tr_sadelestir(
                    (e.dizi_kok_anahtari() or e.temiz_ad() or e.ad or "").strip())
            except Exception:
                return None
            if not kok:
                return None
            return (kok, int(sb[0]), int(sb[1]))

        # Mevcut dizi bölüm indexi (ilk eşleşme)
        dizi_idx = {}
        for e in self.depo.icerikler:
            k = kimlik_dizi(e)
            if k and k not in dizi_idx:
                dizi_idx[k] = e

        eklendi = 0
        atlanan = 0
        for e in yeni:
            if not e.url:
                continue
            if e.url in mevcut_url:
                atlanan += 1
                continue
            # Aynı bölüm farklı URL → mevcut kaydın URL'sini güncelle (silme yok)
            k = kimlik_dizi(e)
            if k and k in dizi_idx and not eskiyi_sil:
                eski = dizi_idx[k]
                if eski.url != e.url:
                    eski_url = eski.url
                    eski.url = e.url
                    if e.ad:
                        eski.ad = e.ad
                    if e.logo and not eski.logo:
                        eski.logo = e.logo
                    # İlerleme/favori anahtarını yeni URL'ye taşı
                    try:
                        self.depo.url_tasi(eski_url, e.url)
                    except Exception:
                        pass
                    mevcut_url.discard(eski_url)
                    mevcut_url.add(e.url)
                    guncellenen += 1
                    continue
            e.kaynak = kaynak_adi
            if not getattr(e, "eklenme", 0):
                e.eklenme = time.time()
            self.depo.icerikler.append(e)
            mevcut_url.add(e.url)
            eklendi += 1
            if k and k not in dizi_idx:
                dizi_idx[k] = e

        # kaynaklar kaydı
        tur = "url" if url_kaynak else "dosya"
        kayit = None
        for k in self.depo.kaynaklar:
            if k.get("ad") == kaynak_adi:
                kayit = k
                break
        adet_kaynak = sum(1 for e in self.depo.icerikler if e.kaynak == kaynak_adi)
        if kayit is not None:
            kayit["adet"] = adet_kaynak
            kayit["zaman"] = time.time()
            if url_kaynak:
                kayit["url"] = url_kaynak
                kayit["tur"] = "url"
            else:
                kayit["tur"] = kayit.get("tur") or "dosya"
        else:
            self.depo.kaynaklar.append({
                "ad": kaynak_adi, "tur": tur,
                "url": url_kaynak, "adet": adet_kaynak, "zaman": time.time()})

        self.depo.kaydet()
        self._dx = None
        self._dx_imza = None
        self.sekme_ac(self.aktif_sekme)
        self._durum_yaz()

        parcalar = [f"{eklendi} yeni eklendi"]
        if guncellenen:
            parcalar.append(f"{guncellenen} URL güncellendi")
        if atlanan:
            parcalar.append(f"{atlanan} zaten vardı")
        if silinen:
            parcalar.append(f"{silinen} eski kaldırıldı")
        QMessageBox.information(
            self, "Liste" + (" güncellendi" if (guncellenen or silinen) else " eklendi"),
            " · ".join(parcalar))


    def ucretsiz_kaynaklar(self):
        d = QDialog(self); d.setWindowTitle("Ücretsiz & Yasal Kaynaklar"); d.resize(680, 480)
        v = QVBoxLayout(d)
        bilgi = QLabel("Bu kaynaklar tamamen yasaldır ve ücretsizdir.")
        bilgi.setStyleSheet(f"color:{R_SOLUK};padding-bottom:6px;")
        v.addWidget(bilgi)
        liste = QListWidget()
        for k in UCRETSIZ_KAYNAKLAR:
            it = QListWidgetItem(f"{k['ad']}   —   {k['adet']}\n{k['aciklama']}")
            it.setData(Qt.ItemDataRole.UserRole, k)
            liste.addItem(it)
        v.addWidget(liste, 1)
        h = QHBoxLayout()
        b1 = QPushButton("Seçileni ekle"); b2 = QPushButton("Kapat")
        h.addStretch(); h.addWidget(b1); h.addWidget(b2); v.addLayout(h)
        b2.clicked.connect(d.reject)
        def ekle():
            it = liste.currentItem()
            if not it:
                return
            k = it.data(Qt.ItemDataRole.UserRole)
            d.accept()
            if k["url"].startswith("ia://"):
                QMessageBox.information(
                    self, "Internet Archive",
                    "Internet Archive arşivi bir sonraki sürümde doğrudan taranacak.\n"
                    "Şimdilik diğer kaynakları kullanabilirsiniz.")
                return
            self.statusBar().showMessage(f"{k['ad']} indiriliyor…")
            self._indirici = Indirici(k["url"])
            self._indirici.ilerleme.connect(lambda s: self.statusBar().showMessage(s))
            def bitti(metin, hata):
                if hata:
                    QMessageBox.critical(self, "İndirilemedi", hata); self._durum_yaz(); return
                self._icerik_ekle(metin, k["ad"], url_kaynak=k["url"],
                                  kategori=k.get("kategori"))
            self._indirici.bitti.connect(bitti)
            self._indirici.start()
        b1.clicked.connect(ekle)
        liste.itemDoubleClicked.connect(lambda _: ekle())
        d.exec()

    def disa_aktar(self):
        if not self.depo.icerikler:
            QMessageBox.information(self, "Bilgi", "Dışa aktarılacak içerik yok.")
            return
        yol, _ = QFileDialog.getSaveFileName(self, "M3U kaydet", "mediabox.m3u", "M3U (*.m3u)")
        if not yol:
            return
        try:
            Path(yol).write_text(m3u_uret(self.depo.icerikler), encoding="utf-8")
            QMessageBox.information(self, "Kaydedildi", f"{len(self.depo.icerikler)} içerik kaydedildi.")
        except Exception as e:
            QMessageBox.critical(self, "Hata", str(e))

    def html_aktar(self):
        QMessageBox.information(
            self, "HTML sürümünden aktarım",
            "Tarayıcıda MediaBox'ı açın, F12 → Konsol'a şunu yapıştırıp Enter'a basın:\n\n"
            "copy(JSON.stringify(Object.fromEntries(Object.keys(localStorage)\n"
            "  .filter(k=>k.startsWith('mb_')).map(k=>[k,localStorage[k]]))))\n\n"
            "Ardından bir metin dosyasına yapıştırıp .json olarak kaydedin ve\n"
            "bu pencerede o dosyayı seçin.")
        yol, _ = QFileDialog.getOpenFileName(self, "Yedek dosyası seç", "", "JSON (*.json *.txt)")
        if not yol:
            return
        r = html_verisi_ice_aktar(self.depo, yol)
        if not r.get("ok"):
            QMessageBox.critical(self, "Hata", r.get("hata", "?"))
            return
        self.sekme_ac("home")
        # URL kaynaklarını arka planda periyodik yenile (varsayılan 6 saat)
        self._yenileme_zaman = QTimer(self)
        self._yenileme_zaman.setInterval(30 * 60 * 1000)      # 30 dk'da bir bak
        self._yenileme_zaman.timeout.connect(self._otomatik_yenile)
        self._yenileme_zaman.start()
        QTimer.singleShot(8000, self._otomatik_yenile); self._durum_yaz()
        QMessageBox.information(
            self, "Aktarıldı",
            f"{r['icerik']} içerik\n{r['favori']} favori\n{r['ilerleme']} izleme kaydı\n"
            f"{r['kaynak']} kaynak\n{r['etiket']} etiket")

    def link_kontrol(self):
        if not self.depo.icerikler:
            return
        urls = [e.url for e in self.depo.icerikler][:2000]
        d = QDialog(self); d.setWindowTitle("Link kontrolü"); d.resize(460, 190)
        v = QVBoxLayout(d)
        bilgi = QLabel(f"{len(urls)} bağlantı kontrol ediliyor…\n"
                       "(Tarayıcıda CORS yüzünden yapılamıyordu — burada gerçek istek atılıyor.)")
        bilgi.setWordWrap(True); v.addWidget(bilgi)
        cubuk = QProgressBar(); cubuk.setRange(0, len(urls)); v.addWidget(cubuk)
        ozet = QLabel("Canlı: 0    Ölü: 0"); v.addWidget(ozet)
        h = QHBoxLayout(); b_dur = QPushButton("Durdur"); h.addStretch(); h.addWidget(b_dur)
        v.addLayout(h)

        olu_liste, sayac = [], {"n": 0, "canli": 0, "olu": 0}
        self._kontrol = LinkKontrol(urls)
        def geldi(u, ok, notu):
            sayac["n"] += 1
            sayac["canli" if ok else "olu"] += 1
            if not ok: olu_liste.append(u)
            cubuk.setValue(sayac["n"])
            ozet.setText(f"Canlı: {sayac['canli']}    Ölü: {sayac['olu']}")
        def bitti(c, o):
            d.accept()
            if not olu_liste:
                QMessageBox.information(self, "Sonuç", f"Tüm bağlantılar çalışıyor ({c}).")
                return
            if QMessageBox.question(
                    self, "Ölü linkler",
                    f"{len(olu_liste)} bağlantı yanıt vermedi.\nListeden silinsin mi?"
                ) == QMessageBox.StandardButton.Yes:
                olu = set(olu_liste)
                self.depo.icerikler = [e for e in self.depo.icerikler if e.url not in olu]
                self.depo.kaydet(); self.sekme_ac(self.aktif_sekme); self._durum_yaz()
        self._kontrol.sonuc.connect(geldi)
        self._kontrol.bitti.connect(bitti)
        b_dur.clicked.connect(lambda: (self._kontrol.durdur(), d.reject()))
        self._kontrol.start()
        d.exec()

    def temizle(self):
        if QMessageBox.question(self, "Emin misiniz?",
                                "Tüm içerik, favori ve izleme geçmişi silinecek.") \
                != QMessageBox.StandardButton.Yes:
            return
        self.depo.icerikler.clear(); self.depo.kaynaklar.clear()
        self.depo.favoriler.clear(); self.depo.son_izlenen.clear()
        self.depo.ilerleme.clear()
        self.depo.kaydet(); self.sekme_ac("home"); self._durum_yaz()

    # ── ayarlar ──
    def ayarlar_menusu(self):
        m = QMenu(self)
        m.addAction("🔑  TMDB API anahtarı…", self.tmdb_ayar)
        m.addAction("🔗  Sağlayıcılar (URL şablonları)…", self.saglayici_ac)
        m.addAction("🖼  TMDB afişlerini tazele", self.tmdb_onbellek_temizle)
        m.addAction("🎨  Tema / arayüz…", self.tema_sec)

        # “Bunları da İzle” rafı
        oner = m.addMenu("★  Bunları da İzle")
        a_oner = oner.addAction("Ana sayfada göster")
        a_oner.setCheckable(True)
        a_oner.setChecked(bool(self.depo.ayarlar.get("oneri_rafi", True)))
        a_oner.triggered.connect(self._oneri_rafi_ayar)
        gizli_sayi = len(self.depo.ayarlar.get("gizli_oneriler") or [])
        a_sifirla = oner.addAction(f"Gizlenen önerileri sıfırla  ({gizli_sayi})")
        a_sifirla.setEnabled(gizli_sayi > 0)
        a_sifirla.triggered.connect(self._gizli_onerileri_sifirla)
        m.addSeparator()
        kum = getattr(self, "_kumanda", None)
        acik = bool(kum and kum.calisiyor())
        m.addAction("📱  Telefon kumandası" + ("  (açık)" if acik else "…"),
                    self.kumanda_ac)

        # İzleme deneyimi
        iz = m.addMenu("🎬  İzleme")
        a_jen = iz.addAction("⏩  Jenerik atlama düğmesi")
        a_jen.setCheckable(True)
        a_jen.setChecked(bool(self.depo.ayarlar.get("jenerik_atla", True)))
        a_jen.triggered.connect(self._jenerik_ayar)
        a_oniz = iz.addAction("🖼  Zaman çubuğu önizlemesi")
        a_oniz.setCheckable(True)
        a_oniz.setChecked(bool(self.depo.ayarlar.get("onizleme", True)))
        a_oniz.triggered.connect(self._onizleme_ayar)
        a_yb = iz.addAction("🔔  Yeni bölüm bildirimi")
        a_yb.setCheckable(True)
        a_yb.setChecked(bool(self.depo.ayarlar.get("yeni_bolum_bildirim", True)))
        a_yb.triggered.connect(self._yeni_bolum_ayar)
        iz.addAction("🔄  Yeni bölümleri şimdi kontrol et",
                     lambda: self._yeni_bolum_tara(zorla=True))
        m.addAction("✨  Görüntü iyileştirme (shader)…", self.shader_penceresi)
        sonra = iz.addMenu("⏭  Sonraki bölüme geçiş")
        gecerli = int(self.depo.ayarlar.get("sonraki_bolum_sn", 8))
        for sn, et in ((0, "Anında geç"), (5, "5 saniye bekle"),
                       (8, "8 saniye bekle"), (15, "15 saniye bekle"),
                       (-1, "Otomatik geçme")):
            a = sonra.addAction(et)
            a.setCheckable(True)
            a.setChecked(gecerli == sn)
            a.triggered.connect(lambda _, v=sn: self._sonraki_ayar(v))
        m.addSeparator()

        # Video modu — Wayland'de gömme çökmeye yol açabildiği için seçilebilir
        vm = m.addMenu("🖥  Video modu")
        aktif = self.depo.ayarlar.get("video_modu", "oto")
        for anahtar, etiket in (("oto", "Otomatik (önerilen)"),
                                ("gomulu", "Gömülü — pencere içinde (X11)"),
                                ("ayri", "Ayrı pencere — her yerde çalışır")):
            a = vm.addAction(etiket)
            a.setCheckable(True); a.setChecked(aktif == anahtar)
            a.triggered.connect(lambda _, k=anahtar: self._video_modu(k))

        # Siyah ekran sorunu için video çıkışı seçimi
        vc = m.addMenu("🎞  Video çıkışı (siyah ekran için)")
        acikvc = self.depo.ayarlar.get("video_cikis", "oto")
        for anahtar, etiket in (
                ("oto",      "Otomatik (önerilen)"),
                ("x11egl",   "gpu + x11egl  — Wayland'de siyah ekran çözümü"),
                ("gpu",      "gpu  — standart"),
                ("gpu-next", "gpu-next  — yeni motor"),
                ("x11",      "x11  — yazılım, her yerde çizer (yavaş)"),
                ("xv",       "xv  — eski donanım katmanı")):
            a = vc.addAction(etiket)
            a.setCheckable(True); a.setChecked(acikvc == anahtar)
            a.triggered.connect(lambda _, k=anahtar: self._video_cikis(k))

        hw = m.addAction("⚡  Donanım hızlandırma")
        hw.setCheckable(True)
        hw.setChecked(self.depo.ayarlar.get("donanim_hizlandirma", True))
        hw.toggled.connect(self._hw_degistir)
        m.addSeparator()
        yn = m.addMenu("🔄  Otomatik yenileme")
        akt = self.depo.ayarlar.get("yenileme_saat", 6)
        for saat, et in ((0, "Kapalı"), (3, "3 saatte bir"),
                         (6, "6 saatte bir"), (12, "12 saatte bir"), (24, "Günde bir")):
            a = yn.addAction(et); a.setCheckable(True); a.setChecked(akt == saat)
            a.triggered.connect(lambda _, sa=saat: self._yenileme_ayarla(sa))
        m.addAction("📊  İzleme istatistikleri…", self.istatistikler)
        m.addAction("🩺  Oynatıcı tanısı…", self.tani_goster)
        m.addAction("📁  Veri klasörünü göster", lambda: QMessageBox.information(
            self, "Veri klasörü", str(veri_klasoru())))
        m.addAction("ℹ  Hakkında", self.hakkinda)
        m.exec(self.sender().mapToGlobal(self.sender().rect().bottomLeft()))

    # ── TMDB ──────────────────────────────────────────────────────
    def tmdb_ayar(self):
        """TMDB API anahtarı girme/doğrulama penceresi."""
        d = QDialog(self); d.setWindowTitle("TMDB API Anahtarı"); d.setMinimumWidth(520)
        v = QVBoxLayout(d); v.setSpacing(11)

        bas = QLabel("<b>TMDB API Anahtarı</b>")
        v.addWidget(bas)
        aciklama = QLabel(
            "Afiş, özet ve puan bilgilerini çekmek için kullanılır.\n"
            "Ücretsiz almak için: themoviedb.org → Ayarlar → API → API Key (v3 auth)")
        aciklama.setWordWrap(True)
        aciklama.setStyleSheet(f"color:{R_SOLUK};font-size:12px;")
        v.addWidget(aciklama)

        kutu = QLineEdit(self.depo.ayarlar.get("tmdb_anahtar", ""))
        kutu.setPlaceholderText("32 karakterlik anahtarı buraya yapıştırın")
        v.addWidget(kutu)

        goster = QCheckBox("Anahtarı gizle")
        goster.setChecked(True)
        kutu.setEchoMode(QLineEdit.EchoMode.Password)
        goster.toggled.connect(lambda k: kutu.setEchoMode(
            QLineEdit.EchoMode.Password if k else QLineEdit.EchoMode.Normal))
        v.addWidget(goster)

        durum = QLabel(""); durum.setWordWrap(True)
        durum.setStyleSheet("font-size:12px;")
        v.addWidget(durum)

        h = QHBoxLayout()
        b_test = QPushButton("Bağlantıyı sına")
        b_iptal = QPushButton("İptal")
        b_kaydet = QPushButton("Kaydet")
        b_kaydet.setStyleSheet(
            f"background:{self.depo.ayarlar.get('vurgu', R_VURGU)};border:0;"
            "border-radius:8px;font-weight:700;padding:9px 22px;")
        h.addWidget(b_test); h.addStretch(); h.addWidget(b_iptal); h.addWidget(b_kaydet)
        v.addLayout(h)

        def sina():
            k = kutu.text().strip()
            if not k:
                durum.setText("<span style='color:#ff8f8f'>Anahtar boş.</span>"); return
            durum.setText("Sınanıyor…")
            QApplication.processEvents()
            try:
                import requests
                r = requests.get("https://api.themoviedb.org/3/configuration",
                                 params={"api_key": k}, timeout=12)
                if r.status_code == 200:
                    durum.setText("<span style='color:#6ddc94'>✓ Anahtar geçerli.</span>")
                elif r.status_code == 401:
                    durum.setText("<span style='color:#ff8f8f'>✗ Anahtar geçersiz (401).</span>")
                else:
                    durum.setText(f"<span style='color:#f0c060'>? Beklenmedik yanıt: "
                                  f"HTTP {r.status_code}</span>")
            except Exception as e:
                durum.setText(f"<span style='color:#ff8f8f'>Bağlanılamadı: "
                              f"{type(e).__name__}</span>")

        b_test.clicked.connect(sina)
        b_iptal.clicked.connect(d.reject)
        b_kaydet.clicked.connect(d.accept)
        if d.exec() == QDialog.DialogCode.Accepted:
            self.depo.ayarlar["tmdb_anahtar"] = kutu.text().strip()
            self.depo.kaydet()
            self.tmdb.anahtar = kutu.text().strip()
            arayuz.TMDB_ESLESME.clear()
            self.statusBar().showMessage(
                "TMDB anahtarı kaydedildi — afişler yükleniyor…", 5000)
            self.sekme_ac(self.aktif_sekme)

    def icerik_duzenle(self, grup: dict):
        """Bir içeriğin adını, grubunu, kategorisini ve bağlantısını düzenler."""
        e = grup["bolumler"][0]
        d = QDialog(self); d.setWindowTitle("İçeriği düzenle"); d.setMinimumWidth(560)
        v = QVBoxLayout(d); v.setSpacing(10)

        def satir(etiket, deger):
            h = QHBoxLayout()
            l = QLabel(etiket); l.setFixedWidth(96)
            g = QLineEdit(deger)
            h.addWidget(l); h.addWidget(g, 1)
            v.addLayout(h)
            return g

        g_ad = satir("Ad:", e.ad)
        g_grup = satir("Grup:", e.grup)
        g_logo = satir("Afiş URL:", e.logo)
        g_url = satir("Bağlantı:", e.url)

        h = QHBoxLayout()
        l = QLabel("Kategori:"); l.setFixedWidth(96)
        kut = QComboBox()
        kodlar = ["movie", "series", "anime", "live"]
        kut.addItems(["Film", "Dizi", "Animasyon", "Canlı TV"])
        kut.setCurrentIndex(kodlar.index(e.kategori) if e.kategori in kodlar else 0)
        h.addWidget(l); h.addWidget(kut, 1)
        v.addLayout(h)

        if len(grup["bolumler"]) > 1:
            n = QLabel(f"ℹ Grup adı/kategori değişikliği {len(grup['bolumler'])} bölümün "
                       "tamamına uygulanır.")
            n.setStyleSheet(f"color:{R_SOLUK};font-size:11.5px;"); n.setWordWrap(True)
            v.addWidget(n)

        alt = QHBoxLayout()
        b_sil = QPushButton("🗑  Listeden sil")
        b_ip = QPushButton("İptal"); b_kay = QPushButton("Kaydet")
        b_kay.setStyleSheet(f"background:{self.depo.ayarlar.get('vurgu', R_VURGU)};"
                            "border:0;border-radius:8px;font-weight:700;padding:8px 20px;")
        alt.addWidget(b_sil); alt.addStretch(); alt.addWidget(b_ip); alt.addWidget(b_kay)
        v.addLayout(alt)

        sonuc = {"islem": None}
        b_ip.clicked.connect(d.reject)
        b_kay.clicked.connect(lambda: (sonuc.update(islem="kaydet"), d.accept()))
        def sil():
            if QMessageBox.question(
                    d, "Emin misiniz?",
                    f"{len(grup['bolumler'])} içerik listeden silinecek."
                ) == QMessageBox.StandardButton.Yes:
                sonuc["islem"] = "sil"; d.accept()
        b_sil.clicked.connect(sil)

        if d.exec() != QDialog.DialogCode.Accepted:
            return
        if sonuc["islem"] == "sil":
            silinecek = {b.url for b in grup["bolumler"]}
            self.depo.icerikler = [x for x in self.depo.icerikler if x.url not in silinecek]
            self.depo.kaydet(); self.sekme_ac(self.aktif_sekme); self._durum_yaz()
            return
        yeni_kat = kodlar[kut.currentIndex()]
        e.ad = g_ad.text().strip() or e.ad
        e.logo = g_logo.text().strip()
        e.url = g_url.text().strip() or e.url
        for b in grup["bolumler"]:
            b.grup = g_grup.text().strip()
            b.kategori = yeni_kat
        self.depo.kaydet()
        arayuz.TMDB_ESLESME.clear()
        self.sekme_ac(self.aktif_sekme)

    def _otomatik_yenile(self):
        """
        URL kaynaklarını süresi geldiyse sessizce yeniler.
        Aralık: ayarlar["yenileme_saat"] (0 = kapalı, varsayılan 6 saat).
        """
        saat = self.depo.ayarlar.get("yenileme_saat", 6)
        if not saat:
            return
        simdi = time.time()

        # ── Dropbox: bağlıysa süresi geldiğinde sessizce yeniden tara ──
        anahtar = self.depo.ayarlar.get("dropbox_app_key", "")
        rt = self.depo.ayarlar.get("dropbox_refresh_token", "")
        for k in self.depo.kaynaklar:
            if k.get("tur") == "dropbox" and anahtar and rt:
                if simdi - k.get("zaman", 0) >= saat * 3600:
                    k["zaman"] = simdi
                    self._dropbox_arka_planda_yenile(anahtar, rt, k)
                break

        for k in self.depo.kaynaklar:
            if k.get("tur") != "url" or not k.get("url"):
                continue
            if simdi - k.get("zaman", 0) < saat * 3600:
                continue
            k["zaman"] = simdi            # tekrar tekrar denemesin
            ad = k.get("ad", "")
            ind = Indirici(k["url"])
            def bitti(metin, hata, _ad=ad, _k=k):
                if hata or not metin:
                    return
                yeni = m3u_ayristir(metin, kaynak=_ad)
                if not yeni:
                    return
                # KORUMA: otomatik yenilemede hiçbir şey silinmez; yalnız yeni URL eklenir
                mevcut = {e.url for e in self.depo.icerikler}
                eklendi = 0
                for e in yeni:
                    if e.url and e.url not in mevcut:
                        e.kaynak = _ad
                        self.depo.icerikler.append(e)
                        mevcut.add(e.url)
                        eklendi += 1
                _k["adet"] = sum(1 for e in self.depo.icerikler if e.kaynak == _ad)
                _k["zaman"] = time.time()
                self.depo.kaydet()
                self.statusBar().showMessage(
                    f"🔄 {_ad} otomatik yenilendi (+{eklendi} yeni, mevcut korundu)", 6000)
                if self.aktif_sekme == "home":
                    self._anasayfa_ciz()
            ind.bitti.connect(bitti)
            isci_baslat(ind)
            break        # her turda yalnızca bir kaynak (ağı yormamak için)

    def _dropbox_arka_planda_yenile(self, app_key: str, refresh_token: str, kaynak_kaydi: dict):
        """Dropbox içeriğini kullanıcıya sormadan, arka planda yeniden tarar."""
        class _Isci(QThread):
            bitti = pyqtSignal(list, str)
            def run(self):
                try:
                    from dropbox_kaynak import (erisim_tokeni_yenile,
                                                m3u_dosyalarini_bul, dosya_metni_al,
                                                kategori_tahmin_dosya_adindan)
                    at = erisim_tokeni_yenile(app_key, refresh_token)
                    tumu = []
                    for f in m3u_dosyalarini_bul(at):
                        try:
                            metin = dosya_metni_al(at, f["yol"])
                        except Exception:
                            continue
                        kat = kategori_tahmin_dosya_adindan(f["ad"])
                        tumu.extend(m3u_ayristir(metin, kaynak=f"Dropbox: {f['ad']}", kategori=kat))
                    self.bitti.emit(tumu, "")
                except Exception as e:
                    self.bitti.emit([], str(e))

        def tamam(icerikler, hata):
            if hata or not icerikler:
                return
            # KORUMA: Dropbox otomatik taramada mevcut içerik silinmez
            mevcut = {e.url for e in self.depo.icerikler}
            eklendi = 0
            for e in icerikler:
                if e.url and e.url not in mevcut:
                    self.depo.icerikler.append(e)
                    mevcut.add(e.url)
                    eklendi += 1
            kaynak_kaydi["adet"] = sum(
                1 for e in self.depo.icerikler if (e.kaynak or "").startswith("Dropbox: "))
            self.depo.kaydet()
            self.statusBar().showMessage(
                f"🔄 Dropbox otomatik yenilendi (+{eklendi} yeni, mevcut korundu)", 6000)
            if self.aktif_sekme == "home":
                self._anasayfa_ciz()

        isci = _Isci()
        isci.bitti.connect(tamam)
        self._db_arkaplan_isci = isci
        isci.start()

    def kaynak_yonetici(self):
        """Eklenen listeleri gösterir; yenileme ve silme yapar."""
        d = QDialog(self); d.setWindowTitle("Kaynak yöneticisi"); d.resize(680, 440)
        v = QVBoxLayout(d)
        liste = QListWidget(); v.addWidget(liste, 1)

        def yenile():
            liste.clear()
            sayim = {}
            for e in self.depo.icerikler:
                sayim[e.kaynak] = sayim.get(e.kaynak, 0) + 1
            for i, k in enumerate(self.depo.kaynaklar):
                ad = k.get("ad", "?")
                n = sayim.get(ad, 0)
                tur = ("☁ Dropbox" if k.get("tur") == "dropbox"
                      else "🌐 URL" if k.get("tur") == "url" else "📂 Dosya")
                it = QListWidgetItem(f"{tur}   {ad}\n        {n} içerik")
                it.setData(Qt.ItemDataRole.UserRole, i)
                liste.addItem(it)
            if not self.depo.kaynaklar:
                it = QListWidgetItem("Henüz kaynak eklenmedi.")
                it.setFlags(Qt.ItemFlag.NoItemFlags)
                liste.addItem(it)
        yenile()

        alt = QHBoxLayout()
        b_yen = QPushButton("🔄 Yenile (URL)")
        b_sil = QPushButton("🗑 Kaynağı ve içeriğini sil")
        b_kap = QPushButton("Kapat")
        alt.addWidget(b_yen); alt.addWidget(b_sil); alt.addStretch(); alt.addWidget(b_kap)
        v.addLayout(alt)
        b_kap.clicked.connect(d.accept)

        def secili():
            it = liste.currentItem()
            return it.data(Qt.ItemDataRole.UserRole) if it else None

        def sil():
            i = secili()
            if i is None: return
            k = self.depo.kaynaklar[i]
            ad = k.get("ad", "")
            if QMessageBox.question(d, "Emin misiniz?",
                    f"“{ad}” kaynağı ve ona ait tüm içerikler silinecek."
                ) != QMessageBox.StandardButton.Yes:
                return
            self.depo.icerikler = [e for e in self.depo.icerikler if e.kaynak != ad]
            del self.depo.kaynaklar[i]
            self.depo.kaydet(); yenile(); self.sekme_ac(self.aktif_sekme); self._durum_yaz()
        b_sil.clicked.connect(sil)

        def yenile_url():
            i = secili()
            if i is None: return
            k = self.depo.kaynaklar[i]
            if k.get("tur") != "url" or not k.get("url"):
                QMessageBox.information(d, "Bilgi", "Yalnızca URL kaynakları yenilenebilir.")
                return
            ad = k.get("ad", "")
            self.statusBar().showMessage(f"{ad} yenileniyor…")
            self._indirici = Indirici(k["url"])
            def bitti(metin, hata):
                if hata:
                    QMessageBox.critical(d, "Yenilenemedi", hata); return
                yeni = m3u_ayristir(metin, kaynak=ad)
                if not yeni:
                    QMessageBox.warning(d, "Boş", "Liste boş döndü — mevcut içerik korundu.")
                    return
                cevap = QMessageBox.question(
                    d, "Kaynak yenile",
                    f"“{ad}” için {len(yeni)} içerik indirildi.\n\n"
                    "Evet = yalnızca yeni URL’leri ekle (mevcut kalsın)\n"
                    "Hayır = bu kaynağın eskilerini silip yenisiyle değiştir",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No |
                    QMessageBox.StandardButton.Cancel)
                if cevap == QMessageBox.StandardButton.Cancel:
                    return
                self._listeye_ekle(
                    yeni, ad, url_kaynak=k.get("url") or "",
                    eskiyi_sil=(cevap == QMessageBox.StandardButton.No))
                yenile()
            self._indirici.bitti.connect(bitti)
            self._indirici.start()
        b_yen.clicked.connect(yenile_url)
        d.exec()

    def istatistikler(self):
        """İzleme istatistikleri."""
        dp = self.depo
        top = len(dp.icerikler)
        kat = {}
        for e in dp.icerikler:
            kat[e.kategori] = kat.get(e.kategori, 0) + 1
        adlar = {"movie": "Film", "series": "Dizi", "anime": "Animasyon", "live": "Canlı TV"}
        toplam_sn = sum(p.get("t", 0) for p in dp.ilerleme.values())
        gruplar = {}
        for e in dp.icerikler:
            if e.grup:
                gruplar[e.grup] = gruplar.get(e.grup, 0) + 1
        enler = sorted(gruplar.items(), key=lambda x: -x[1])[:8]

        sat = [f"Toplam içerik           : {top}"]
        for k, n in sorted(kat.items(), key=lambda x: -x[1]):
            sat.append(f"   {adlar.get(k, k):<18}: {n}")
        sat.append("")
        sat.append(f"Favoriler               : {len(dp.favoriler)}")
        sat.append(f"Son izlenenler          : {len(dp.son_izlenen)}")
        sat.append(f"Yarım kalan             : {len(dp.ilerleme)}")
        sat.append(f"Toplam izleme           : {sure_yaz(toplam_sn)}")
        sat.append(f"Kaynak sayısı           : {len(dp.kaynaklar)}")
        sat.append("")
        sat.append("En çok içerik olan gruplar:")
        for g, n in enler:
            sat.append(f"   {g[:34]:<34} {n}")
        eksik_logo = sum(1 for e in dp.icerikler if not e.logo)
        eksik_yil = sum(1 for e in dp.icerikler if not e.yil())
        sat.append("")
        sat.append("Eksik bilgi:")
        sat.append(f"   afişi olmayan       : {eksik_logo}")
        sat.append(f"   yılı bilinmeyen     : {eksik_yil}")

        d = QDialog(self); d.setWindowTitle("İzleme istatistikleri"); d.resize(520, 520)
        v = QVBoxLayout(d)
        alan = QTextEdit(); alan.setReadOnly(True)
        alan.setStyleSheet(f"background:{R_YUZEY2};border:1px solid {R_CIZGI};"
                           "font-family:monospace;font-size:12.5px;")
        alan.setPlainText("\n".join(sat))
        v.addWidget(alan)
        b = QPushButton("Kapat"); b.clicked.connect(d.accept)
        v.addWidget(b, 0, Qt.AlignmentFlag.AlignRight)
        d.exec()

    # ── yeni bölüm bildirimi ───────────────────────────────────────
    def _zil_tazele(self):
        """Bekleyen bildirim varsa zili göster."""
        try:
            from yeni_bolum import bekleyen_sonuc
            n = len(bekleyen_sonuc(self.depo))
            self.b_zil.setVisible(n > 0)
            if n:
                self.b_zil.setText(f"YENİ  {n}")
                v = self.depo.ayarlar.get("vurgu", R_VURGU)
                self.b_zil.setStyleSheet(
                    f"QPushButton{{background:{v};border:0;border-radius:8px;"
                    f"color:#fff;font-weight:700;padding:0 12px;}}")
        except Exception:
            pass

    def _yeni_bolum_tara(self, zorla: bool = False):
        """TMDB'den yeni bölüm kontrolü (arka planda)."""
        from yeni_bolum import kontrol_zamani_geldi
        if not (self.tmdb and self.tmdb.hazir):
            return
        if not zorla and not kontrol_zamani_geldi(self.depo, 12):
            self._zil_tazele()
            return
        if getattr(self, "_yb_calisiyor", False):
            return
        self._yb_calisiyor = True

        class _Isci(QThread):
            hazir = pyqtSignal(object)

            def __init__(self, depo, ist):
                super().__init__()
                self.depo, self.ist = depo, ist

            def run(self):
                try:
                    from yeni_bolum import kontrol_et
                    self.hazir.emit(kontrol_et(self.depo, self.ist))
                except Exception:
                    self.hazir.emit([])

        i = _Isci(self.depo, self.tmdb)
        i.hazir.connect(self._yeni_bolum_geldi)
        isci_baslat(i)

    def _yeni_bolum_geldi(self, sonuc):
        self._yb_calisiyor = False
        self._zil_tazele()
        if sonuc:
            from yeni_bolum import ozet_metni
            self.statusBar().showMessage("🔔  " + ozet_metni(sonuc), 12000)

    def yeni_bolum_penceresi(self):
        """Bekleyen yeni bölüm bildirimlerini listeler."""
        from yeni_bolum import bekleyen_sonuc, okundu_isaretle
        sonuc = bekleyen_sonuc(self.depo)
        d = QDialog(self)
        d.setWindowTitle("Yeni bölümler")
        d.resize(620, 460)
        v = QVBoxLayout(d)
        v.setSpacing(10)

        b = QLabel("İzlediğiniz dizilerde <b>yayınlanmış ama sizde olmayan</b> bölümler:")
        b.setWordWrap(True)
        v.addWidget(b)

        liste = QListWidget()
        for x in sonuc:
            bs, be = x["bende"]
            ts, te = x["tmdb"]
            ek = ""
            if x.get("sonraki") and x.get("sonraki_tarih"):
                s2, e2 = x["sonraki"]
                ek = f"\n        ⏭ Sıradaki: S{s2:02d}E{e2:02d} — {x['sonraki_tarih']}"
            it = QListWidgetItem(
                f"{x['ad']}\n        Sizde: S{bs:02d}E{be:02d}   →   "
                f"Yayınlanan: S{ts:02d}E{te:02d}"
                f"{'  “' + x['baslik'] + '”' if x.get('baslik') else ''}"
                f"   ({x.get('tarih', '')}){ek}")
            it.setData(Qt.ItemDataRole.UserRole, x)
            liste.addItem(it)
        if not sonuc:
            it = QListWidgetItem("Bekleyen bildirim yok.\n        "
                                 "“Şimdi kontrol et” ile tarama yapabilirsiniz.")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            liste.addItem(it)
        v.addWidget(liste, 1)

        ipucu = QLabel("İpucu: yeni bölümü listenize eklemek için M3U kaynağınızı "
                       "yenileyin ya da detay sayfasından “⚡ Otomatik URL ekle” kullanın.")
        ipucu.setWordWrap(True)
        ipucu.setStyleSheet(f"color:{R_SOLUK};font-size:11px;")
        v.addWidget(ipucu)

        alt = QHBoxLayout()
        b_tara = QPushButton("🔄  Şimdi kontrol et")
        b_ac = QPushButton("Diziyi aç")
        b_oku = QPushButton("Tümünü okundu işaretle")
        b_kapat = QPushButton("Kapat")
        alt.addWidget(b_tara)
        alt.addWidget(b_ac)
        alt.addStretch()
        alt.addWidget(b_oku)
        alt.addWidget(b_kapat)
        v.addLayout(alt)

        def tara():
            self.statusBar().showMessage("🔄 Yeni bölümler taranıyor…", 8000)
            self._yeni_bolum_tara(zorla=True)
            d.accept()

        def diziyi_ac():
            it = liste.currentItem()
            if not it:
                return
            x = it.data(Qt.ItemDataRole.UserRole)
            if not x:
                return
            bulunan = [e for e in self.depo.icerikler if e.dizi_anahtari() == x["ad"]]
            if bulunan:
                d.accept()
                self.detay_ac(diziye_grupla(bulunan)[0])

        def okundu():
            okundu_isaretle(self.depo)
            self._zil_tazele()
            d.accept()

        b_tara.clicked.connect(tara)
        b_ac.clicked.connect(diziyi_ac)
        b_oku.clicked.connect(okundu)
        b_kapat.clicked.connect(d.accept)
        liste.itemDoubleClicked.connect(lambda *_: diziyi_ac())
        d.exec()

    # ── görüntü iyileştirme (shader) ───────────────────────────────
    def shader_penceresi(self):
        """
        mpv GLSL shader seçimi.

        Shader'lar GPU üzerinde çalışır; görüntüyü gerçek zamanlı işler.
        Seçim anında uygulanır (oynatma sürüyorsa yeniden başlatmaya gerek yok).
        """
        from shader import (YERLESIK, yerlesikleri_kur, kullanici_shaderlari,
                            shader_klasoru, mpv_uygula, mpv_temizle, gecerli_mi)

        yollar = yerlesikleri_kur()
        d = QDialog(self)
        d.setWindowTitle("Görüntü iyileştirme — Shader")
        d.resize(700, 560)
        v = QVBoxLayout(d)
        v.setSpacing(10)

        b = QLabel(
            "Shader'lar görüntüyü <b>gerçek zamanlı</b> işler (GPU kullanır).<br>"
            "Birden fazlasını seçebilirsiniz; sırayla uygulanırlar.")
        b.setWordWrap(True)
        v.addWidget(b)

        liste = QListWidget()
        secili = self.depo.ayarlar.get("shader_listesi") or []
        if isinstance(secili, str):
            secili = [secili] if secili else []

        for anahtar, bilgi in YERLESIK.items():
            yol = yollar.get(anahtar, "")
            it = QListWidgetItem(f"{bilgi['ad']}\n        {bilgi['aciklama']}")
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if yol in secili
                             else Qt.CheckState.Unchecked)
            it.setData(Qt.ItemDataRole.UserRole, yol)
            liste.addItem(it)

        for ad, yol in kullanici_shaderlari():
            it = QListWidgetItem(f"📄 {ad}   (kendi shader'ınız)\n        {yol}")
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if yol in secili
                             else Qt.CheckState.Unchecked)
            it.setData(Qt.ItemDataRole.UserRole, yol)
            liste.addItem(it)
        v.addWidget(liste, 1)

        durum = QLabel("")
        durum.setWordWrap(True)
        durum.setStyleSheet(f"color:{R_SOLUK};font-size:11px;")
        v.addWidget(durum)

        bilgi2 = QLabel(
            f"Kendi shader'larınızı şu klasöre koyabilirsiniz:<br>"
            f"<code>{shader_klasoru()}</code><br>"
            "Anime4K, FSR, CAS gibi popüler shader'lar bu klasöre atıldığında "
            "listede görünür. (Program telifli dosya indirmez.)")
        bilgi2.setWordWrap(True)
        bilgi2.setStyleSheet(f"color:{R_SOLUK};font-size:11px;")
        v.addWidget(bilgi2)

        alt = QHBoxLayout()
        b_klasor = QPushButton("📂  Klasörü aç")
        b_kapa = QPushButton("Tümünü kapat")
        b_iptal = QPushButton("İptal")
        b_uygula = QPushButton("Uygula")
        b_uygula.setStyleSheet(f"background:{self.depo.ayarlar.get('vurgu', R_VURGU)};"
                               "border:0;border-radius:8px;font-weight:700;padding:8px 18px;")
        alt.addWidget(b_klasor)
        alt.addWidget(b_kapa)
        alt.addStretch()
        alt.addWidget(b_iptal)
        alt.addWidget(b_uygula)
        v.addLayout(alt)

        def secilenler():
            c = []
            for i in range(liste.count()):
                it = liste.item(i)
                if it.checkState() == Qt.CheckState.Checked:
                    y = it.data(Qt.ItemDataRole.UserRole)
                    if y:
                        c.append(y)
            return c

        def uygula():
            sec = secilenler()
            for y in sec:
                ok, hata = gecerli_mi(y)
                if not ok:
                    QMessageBox.warning(d, "Geçersiz shader",
                                        f"{os.path.basename(y)}: {hata}")
                    return
            self.depo.ayarlar["shader_listesi"] = sec
            self.depo.kaydet()
            oy = self.oynatici
            if oy and getattr(oy, "mpv", None) and oy.mpv.yasiyor:
                ok, hata = (mpv_uygula(oy.mpv, sec) if sec else (True, ""))
                if not sec:
                    mpv_temizle(oy.mpv)
                if not ok:
                    QMessageBox.warning(d, "Uygulanamadı", hata)
                    return
            self.statusBar().showMessage(
                f"✨ {len(sec)} shader etkin" if sec else "✨ Shader kapatıldı", 6000)
            d.accept()

        def klasor_ac():
            import subprocess
            try:
                subprocess.Popen(["xdg-open", str(shader_klasoru())])
            except Exception:
                QApplication.clipboard().setText(str(shader_klasoru()))
                durum.setText("Klasör açılamadı; yol panoya kopyalandı.")

        def hepsini_kapat():
            for i in range(liste.count()):
                liste.item(i).setCheckState(Qt.CheckState.Unchecked)

        b_uygula.clicked.connect(uygula)
        b_iptal.clicked.connect(d.reject)
        b_klasor.clicked.connect(klasor_ac)
        b_kapa.clicked.connect(hepsini_kapat)
        d.exec()

    # ── izleme ayarları ────────────────────────────────────────────
    def _onizleme_ayar(self, acik: bool):
        self.depo.ayarlar["onizleme"] = bool(acik)
        self.depo.kaydet()
        self.statusBar().showMessage(
            "🖼 Zaman çubuğu önizlemesi " + ("açık" if acik else "kapalı"), 4000)

    def _yeni_bolum_ayar(self, acik: bool):
        self.depo.ayarlar["yeni_bolum_bildirim"] = bool(acik)
        self.depo.kaydet()
        if not acik:
            self.b_zil.setVisible(False)
        else:
            self._zil_tazele()
        self.statusBar().showMessage(
            "🔔 Yeni bölüm bildirimi " + ("açık" if acik else "kapalı"), 4000)

    def _jenerik_ayar(self, acik: bool):
        self.depo.ayarlar["jenerik_atla"] = bool(acik)
        self.depo.kaydet()
        self.statusBar().showMessage(
            "⏩ Jenerik atlama " + ("açık" if acik else "kapalı"), 4000)

    def _oneri_rafi_ayar(self, acik: bool):
        """“Bunları da İzle” rafını aç/kapat."""
        self.depo.ayarlar["oneri_rafi"] = bool(acik)
        self.depo.kaydet()
        self.statusBar().showMessage(
            "★ Bunları da İzle rafı " + ("açık" if acik else "kapalı"), 4000)
        if self.yigin.currentIndex() == 0:
            self._anasayfa_ciz()

    def _gizli_onerileri_sifirla(self):
        """× ile gizlenen önerileri geri getirir."""
        n = len(self.depo.ayarlar.get("gizli_oneriler") or [])
        self.depo.ayarlar["gizli_oneriler"] = []
        self.depo.kaydet()
        self.statusBar().showMessage(f"{n} gizlenen öneri geri getirildi", 5000)
        if self.yigin.currentIndex() == 0:
            self._anasayfa_ciz()

    def _sonraki_ayar(self, saniye: int):
        self.depo.ayarlar["sonraki_bolum_sn"] = int(saniye)
        self.depo.kaydet()
        et = {0: "anında geçiş", -1: "otomatik geçiş kapalı"}.get(
            saniye, f"{saniye} saniye bekleyecek")
        self.statusBar().showMessage(f"⏭ Sonraki bölüm: {et}", 4000)

    # ── telefon kumandası ──────────────────────────────────────────
    def kumanda_ac(self):
        """
        Telefon kumandası sunucusunu açar/kapatır ve adresi gösterir.

        TAMAMEN KORUMALI: kullanıcı bu düğmeye bastığında program hiçbir
        koşulda kapanmamalı. Modül yüklenmesi, port açma, QR üretimi ve
        pencere kurulumu ayrı ayrı korunuyor; hata olursa açıklayıcı bir
        uyarı gösterilip normal çalışmaya devam edilir.
        """
        try:
            self._kumanda_ac()
        except Exception as e:
            import traceback
            ayrinti = traceback.format_exc(limit=6)
            try:
                c = QMessageBox(self)
                c.setWindowTitle("Telefon kumandası açılamadı")
                c.setText("Kumanda başlatılırken bir sorun oluştu.\n"
                          "Program çalışmaya devam ediyor.")
                c.setInformativeText(f"{type(e).__name__}: {e}")
                c.setDetailedText(ayrinti)
                c.exec()
            except Exception:
                print("[MediaBox] Kumanda hatası:", ayrinti, file=sys.stderr)

    def _kumanda_ac(self):
        from kumanda import KumandaSunucu, qr_png, qr_var_mi

        kum = getattr(self, "_kumanda", None)
        if kum and kum.calisiyor():
            c = QMessageBox(self)
            c.setWindowTitle("Telefon kumandası")
            c.setText("Kumanda şu anda açık.")
            c.setInformativeText(kum.adres)
            b_kapat = c.addButton("Kumandayı kapat", QMessageBox.ButtonRole.DestructiveRole)
            c.addButton("Tamam", QMessageBox.ButtonRole.AcceptRole)
            c.exec()
            if c.clickedButton() is b_kapat:
                kum.durdur()
                self.statusBar().showMessage("📱 Telefon kumandası kapatıldı", 5000)
            return

        try:
            kum = KumandaSunucu(self)
            adres = kum.baslat()
            self._kumanda = kum
        except Exception as e:
            QMessageBox.warning(self, "Açılamadı", f"Kumanda sunucusu başlatılamadı:\n{e}")
            return

        d = QDialog(self)
        d.setWindowTitle("Telefon kumandası")
        d.resize(430, 520)
        v = QVBoxLayout(d)
        v.setSpacing(12)

        b = QLabel("Telefonunuz <b>aynı Wi-Fi ağında</b> olmalı.<br>"
                   "Aşağıdaki adresi tarayıcıda açın:")
        b.setWordWrap(True)
        v.addWidget(b)

        try:
            png = qr_png(adres, kutu=6)
        except Exception:
            png = b""          # QR üretilemezse adres yine gösterilir
        if png:
            from PyQt6.QtGui import QPixmap
            px = QPixmap()
            px.loadFromData(png)
            g = QLabel()
            g.setPixmap(px.scaled(260, 260, Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation))
            g.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v.addWidget(g)
        else:
            # QR üretilemedi: hangi paketin eksik olduğunu söyle.
            eksik = ("python-qrcode python-pillow" if not qr_var_mi()
                     else "python-pillow")
            ipucu = QLabel(
                "QR kodu göstermek için:<br>"
                f"<code>sudo pacman -S {eksik}</code><br>"
                "<span style='color:#8b90a0'>(Kumanda QR olmadan da çalışır — "
                "aşağıdaki adresi telefona yazın.)</span>")
            ipucu.setWordWrap(True)
            ipucu.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            ipucu.setStyleSheet(f"color:{R_SOLUK};font-size:11px;")
            v.addWidget(ipucu)

        kutu = QLineEdit(adres)
        kutu.setReadOnly(True)
        kutu.setStyleSheet("font-family:monospace;font-size:12px;padding:8px;")
        v.addWidget(kutu)

        metin = ("🔒 Sunucu yalnızca yerel ağda çalışır ve her açılışta "
                 "yeni bir erişim anahtarı üretir. Program kapanınca durur.")
        if getattr(kum, "yalnizca_yerel", False):
            metin = ("⚠ Yalnızca bu bilgisayardan erişilebiliyor (127.0.0.1). "
                     "Güvenlik duvarınız dış bağlantıları engelliyor olabilir.")
        uyari = QLabel(metin)
        uyari.setWordWrap(True)
        uyari.setStyleSheet(f"color:{R_SOLUK};font-size:11px;")
        v.addWidget(uyari)

        alt = QHBoxLayout()
        b_kop = QPushButton("📋  Adresi kopyala")
        b_kap = QPushButton("Kapat")
        b_kop.clicked.connect(lambda: QApplication.clipboard().setText(adres))
        b_kap.clicked.connect(d.accept)
        alt.addWidget(b_kop)
        alt.addStretch()
        alt.addWidget(b_kap)
        v.addLayout(alt)
        d.exec()
        self.statusBar().showMessage(f"📱 Kumanda açık:  {adres}", 12000)

    def saglayici_ac(self):
        SaglayiciYonetici(self.depo, self).exec()

    def tmdb_onbellek_temizle(self):
        n = self.tmdb.onbellek_temizle()
        for p in self.gorsel_klasor.glob("*.img"):
            try: p.unlink()
            except OSError: pass
        arayuz.TMDB_ESLESME.clear()
        self.sekme_ac(self.aktif_sekme)
        QMessageBox.information(self, "Tazelendi",
                                f"{n} TMDB kaydı ve indirilen afişler silindi.\n"
                                "Görseller yeniden indirilecek.")

    def _video_cikis(self, secim: str):
        """
        mpv video çıkışını değiştirir.
        Wayland'de pencereye gömülen mpv bazen SİYAH kare bırakır;
        'x11egl' bağlamı bunu genellikle çözer.
        """
        self.depo.ayarlar["video_cikis"] = secim
        self.depo.kaydet()
        QMessageBox.information(
            self, "Video çıkışı",
            f"Video çıkışı: {secim}\n\n"
            "Bir sonraki oynatmada geçerli olacak.\n"
            "Görüntü hâlâ siyahsa sırayla 'x11egl' → 'gpu-next' → 'x11' deneyin.")

    def _video_modu(self, mod: str):
        self.depo.ayarlar["video_modu"] = mod
        self.depo.kaydet()
        # NOT: Çalışan mpv'yi burada YOK ETMİYORUZ. Aynı süreçte mpv'yi
        # yok edip yeniden kurmak bazı sürücülerde kararsızlığa yol açıyor.
        # Ayar bir sonraki oynatmada zaten uygulanır.
        adlar = {"oto": "Otomatik", "gomulu": "Gömülü", "ayri": "Ayrı pencere"}
        QMessageBox.information(
            self, "Video modu",
            f"Video modu: {adlar.get(mod, mod)}\n\nBir sonraki oynatmada geçerli olacak.")

    def _yenileme_ayarla(self, saat: int):
        self.depo.ayarlar["yenileme_saat"] = saat
        self.depo.kaydet()
        self.statusBar().showMessage(
            "Otomatik yenileme kapatıldı." if not saat
            else f"URL kaynakları {saat} saatte bir yenilenecek.", 5000)

    def tani_goster(self):
        """Oynatıcı sorunlarını teşhis eder — çökme bildirimlerinde işe yarar."""
        from mediabox_qt import oynatici_tanisi
        rapor = oynatici_tanisi()
        d = QDialog(self); d.setWindowTitle("Oynatıcı Tanısı"); d.resize(620, 460)
        v = QVBoxLayout(d)
        alan = QTextEdit(); alan.setReadOnly(True)
        alan.setStyleSheet(f"background:{R_YUZEY2};border:1px solid {R_CIZGI};"
                           "font-family:monospace;font-size:12px;")
        alan.setPlainText(rapor)
        v.addWidget(alan)
        h = QHBoxLayout()
        b_kop = QPushButton("Panoya kopyala")
        b_kop.clicked.connect(lambda: QApplication.clipboard().setText(rapor))
        b_kap = QPushButton("Kapat"); b_kap.clicked.connect(d.accept)
        h.addWidget(b_kop); h.addStretch(); h.addWidget(b_kap)
        v.addLayout(h)
        d.exec()

    def _hw_degistir(self, v):
        self.depo.ayarlar["donanim_hizlandirma"] = v
        self.depo.kaydet()
        QMessageBox.information(self, "Bilgi",
                                "Değişiklik bir sonraki oynatmada geçerli olacak.")

    def tema_sec(self):
        """Hazır arayüz temalarından birini uygular."""
        d = QDialog(self)
        d.setWindowTitle("Tema / Arayüz")
        d.resize(480, 420)
        v = QVBoxLayout(d)
        v.addWidget(QLabel("Hazır tema paketleri — arka plan, yüzey ve vurgu rengi birlikte değişir."))
        liste = QListWidget()
        secili = self.depo.ayarlar.get("tema", "netflix")
        for tm in TEMALAR:
            it = QListWidgetItem(f"{tm['ad']}\n        vurgu {tm['vurgu']}  ·  arka {tm['arka']}")
            it.setData(Qt.ItemDataRole.UserRole, tm["id"])
            liste.addItem(it)
            if tm["id"] == secili:
                liste.setCurrentItem(it)
        v.addWidget(liste, 1)
        alt = QHBoxLayout()
        b_iptal = QPushButton("İptal")
        b_uygula = QPushButton("Uygula")
        b_uygula.setStyleSheet(
            f"background:{self.depo.ayarlar.get('vurgu', R_VURGU)};"
            "border:0;border-radius:8px;font-weight:700;padding:8px 18px;")
        alt.addStretch(); alt.addWidget(b_iptal); alt.addWidget(b_uygula)
        v.addLayout(alt)

        def uygula():
            it = liste.currentItem()
            if not it:
                return
            tid = it.data(Qt.ItemDataRole.UserRole)
            tm = tema_bul(tid)
            self.depo.ayarlar["tema"] = tid
            self.depo.ayarlar["vurgu"] = tm["vurgu"]
            self.depo.kaydet()
            QApplication.instance().setStyleSheet(stil(tm["vurgu"], tm))
            # Üst logo rengi
            try:
                self.ustbar.setStyleSheet(
                    f"background:{tm.get('yuzey', R_YUZEY)};border-bottom:1px solid {tm.get('cizgi', R_CIZGI)};")
            except Exception:
                pass
            self.sekme_ac(self.aktif_sekme)
            self.statusBar().showMessage(f"🎨 Tema: {tm['ad']}", 4000)
            d.accept()

        b_uygula.clicked.connect(uygula)
        b_iptal.clicked.connect(d.reject)
        liste.itemDoubleClicked.connect(lambda *_: uygula())
        d.exec()

    def renk_sec(self):
        """Eski menü kısayolu — tema seçicine yönlendirir."""
        self.tema_sec()

    def hakkinda(self):
        d = self.depo
        try:
            import mpv
            t = mpv.MPV(vo="null", ao="null"); mv = t.mpv_version; t.terminate()
        except Exception:
            mv = "?"
        QMessageBox.about(
            self, f"{APP_ADI} {APP_SURUM}",
            f"<b>{APP_ADI} Qt {APP_SURUM}</b><br><br>"
            f"Saf Python masaüstü medya merkezi.<br>"
            f"Oynatıcı: gömülü {mv}<br><br>"
            f"İçerik: {len(d.icerikler)}<br>"
            f"Veri: {veri_klasoru()}<br><br>"
            f"<i>HLS, MPEG-TS, RTMP, RTSP, SRT, UDP ve yerel dosyalar desteklenir.</i>")

    def _ilk_kullanim(self):
        c = QMessageBox(self)
        c.setWindowTitle("Hoş geldiniz")
        c.setText(f"<b>{APP_ADI}</b>'a hoş geldiniz.")
        c.setInformativeText(
            "Başlamak için bir içerik listesi ekleyin:\n\n"
            "•  M3U dosyanız varsa açın\n"
            "•  Bir M3U adresi girin\n"
            "•  Hazır ücretsiz & yasal kaynaklardan seçin\n"
            "•  Tarayıcıdaki MediaBox verilerinizi aktarın")
        b1 = c.addButton("Ücretsiz kaynaklar", QMessageBox.ButtonRole.AcceptRole)
        b2 = c.addButton("M3U aç", QMessageBox.ButtonRole.AcceptRole)
        b3 = c.addButton("HTML'den aktar", QMessageBox.ButtonRole.AcceptRole)
        c.addButton("Sonra", QMessageBox.ButtonRole.RejectRole)
        c.exec()
        if c.clickedButton() == b1: self.ucretsiz_kaynaklar()
        elif c.clickedButton() == b2: self.m3u_ac()
        elif c.clickedButton() == b3: self.html_aktar()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.yigin.currentIndex() == 1:
            QTimer.singleShot(60, self.izgara._sutun_tazele)

    def _kapanista_kaydet(self):
        """aboutToQuit / closeEvent — favori ve ilerlemeyi diske kilitle."""
        try:
            oy = getattr(self, "oynatici", None)
            if oy is not None and getattr(oy, "icerik", None) is not None:
                sur = float(getattr(oy, "_sur", 0) or 0)
                kon = float(getattr(oy, "_kon", 0) or 0)
                if sur > 0 and kon >= 15:
                    self.depo.izleme_kaydet(oy.icerik, kon, sur)
        except Exception as ex:
            print(f"[MediaBox] kapanışta izleme: {type(ex).__name__}: {ex}")
        try:
            self.depo.kaydet()
        except Exception as ex:
            print(f"[MediaBox] kapanışta depo: {type(ex).__name__}: {ex}")

    def closeEvent(self, e):
        # Her adım AYRI try/except: biri patlasa bile depo.kaydet çalışsın.
        self._kapanista_kaydet()
        try:
            self.oynatici.yok_et()
        except Exception as ex:
            print(f"[MediaBox] kapanışta oynatıcı: {type(ex).__name__}: {ex}")
        try:
            kum = getattr(self, "_kumanda", None)
            if kum:
                kum.durdur()
        except Exception:
            pass
        super().closeEvent(e)

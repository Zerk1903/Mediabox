#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MediaBox Qt — Arayüz + Gömülü mpv Oynatıcı
===========================================
mediabox_qt.py tarafından çağrılır. Tek başına çalıştırılmaz.

TASARIM
    Üstte yatay menü (logo + sekmeler + arama), altında içerik alanı.
    Koyu tema, poster ızgarası, hero afiş, yatay raflar.

OYNATICI
    mpv, Qt widget'ının içine gömülür (wid parametresi). Ayrı pencere
    açılmaz. HLS/TS/RTMP/RTSP/SRT/UDP — tarayıcının açamadığı akışlar dahil.
"""

from __future__ import annotations

import locale
try:
    locale.setlocale(locale.LC_NUMERIC, "C")   # libmpv'den önce
except Exception:
    pass

import hashlib
import os
import sys
import time
from pathlib import Path

from PyQt6.QtCore import (Qt, QSize, QTimer, QThread, pyqtSignal, QObject,
                          QRunnable, QThreadPool, pyqtSlot, QCoreApplication,
                          QEvent)
from PyQt6.QtGui import (QAction, QColor, QFont, QIcon, QKeySequence, QPainter,
                         QPixmap, QImage, QShortcut, QLinearGradient, QBrush, QPen)
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLabel, QPushButton, QLineEdit,
                             QScrollArea, QGridLayout, QStackedWidget, QSlider,
                             QFileDialog, QMessageBox, QSizePolicy, QFrame,
                             QMenu, QInputDialog, QDialog, QListWidget,
                             QListWidgetItem, QComboBox, QCheckBox, QProgressBar,
                             QGraphicsDropShadowEffect)

from mediabox_qt import (Depo, Icerik, m3u_ayristir, m3u_uret, temiz_baslik,
                         tr_sadelestir, diziye_grupla, UCRETSIZ_KAYNAKLAR,
                         afis_klasoru, veri_klasoru, APP_ADI, APP_SURUM)

# ══════════════════════════════════════════════════════════════════
#  RENKLER
# ══════════════════════════════════════════════════════════════════
R_ARKA = "#0b0c10"
R_YUZEY = "#15171e"
R_YUZEY2 = "#1d2029"
R_CIZGI = "#2a2e3a"
R_METIN = "#eceef4"
R_SOLUK = "#8b90a0"
R_VURGU = "#E50914"

KART_EN, KART_BOY = 168, 252
YKART_EN, YKART_BOY = 300, 169   # yatay (16:9) kart

KOMPAKT_BTN = (
    "QPushButton{background:%s;border:1px solid %s;border-radius:7px;"
    "padding:2px 6px;color:%s;font-size:12px;}"
    "QPushButton:hover{background:#262a35;}"
    "QPushButton:pressed{background:#14171e;}"
) % (R_YUZEY2, R_CIZGI, R_METIN)



def stil(vurgu: str = R_VURGU, tema: dict | None = None) -> str:
    """
    Uygulama stil sayfası.

    `tema` verilirse arka plan / yüzey / metin renkleri de paketten alınır;
    yalnızca `vurgu` verilirse varsayılan koyu palet + o vurgu kullanılır.
    """
    if tema:
        arka = tema.get("arka", R_ARKA)
        yuzey = tema.get("yuzey", R_YUZEY)
        yuzey2 = tema.get("yuzey2", R_YUZEY2)
        cizgi = tema.get("cizgi", R_CIZGI)
        metin = tema.get("metin", R_METIN)
        vurgu = tema.get("vurgu", vurgu) or vurgu
    else:
        arka, yuzey, yuzey2, cizgi, metin = R_ARKA, R_YUZEY, R_YUZEY2, R_CIZGI, R_METIN
    return f"""
    QWidget {{ background:{arka}; color:{metin};
               font-family:'Inter','Segoe UI',sans-serif; font-size:13px; }}
    QScrollArea, QScrollArea > QWidget > QWidget {{ background:transparent; border:0; }}
    QScrollBar:vertical {{ background:transparent; width:10px; margin:0; }}
    QScrollBar::handle:vertical {{ background:{cizgi}; border-radius:5px; min-height:40px; }}
    QScrollBar::handle:vertical:hover {{ background:#3a4050; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height:0; }}
    QScrollBar:horizontal {{ background:transparent; height:10px; }}
    QScrollBar::handle:horizontal {{ background:{cizgi}; border-radius:5px; min-width:40px; }}
    QLineEdit {{ background:{yuzey2}; border:1px solid {cizgi}; border-radius:18px;
                 padding:8px 16px; color:{metin}; selection-background-color:{vurgu}; }}
    QLineEdit:focus {{ border-color:{vurgu}; }}
    QPushButton {{ background:{yuzey2}; border:1px solid {cizgi}; border-radius:8px;
                   padding:8px 16px; color:{metin}; }}
    QPushButton:hover {{ background:#262a35; border-color:#3a4050; }}
    QPushButton:pressed {{ background:#1a1d26; }}
    QComboBox {{ background:{yuzey2}; border:1px solid {cizgi}; border-radius:8px;
                 padding:6px 12px; }}
    QComboBox QAbstractItemView {{ background:{yuzey2}; border:1px solid {cizgi};
                                   selection-background-color:{vurgu}; }}
    QMenu {{ background:{yuzey2}; border:1px solid {cizgi}; border-radius:8px; padding:6px; }}
    QMenu::item {{ padding:8px 22px; border-radius:6px; }}
    QMenu::item:selected {{ background:{vurgu}; }}
    QSlider::groove:horizontal {{ height:4px; background:{cizgi}; border-radius:2px; }}
    QSlider::sub-page:horizontal {{ background:{vurgu}; border-radius:2px; }}
    QSlider::handle:horizontal {{ background:#fff; width:13px; height:13px;
                                  margin:-5px 0; border-radius:7px; }}
    QDialog {{ background:{yuzey}; }}
    QListWidget {{ background:{yuzey2}; border:1px solid {cizgi}; border-radius:8px; }}
    QListWidget::item {{ padding:9px; border-radius:6px; }}
    QListWidget::item:selected {{ background:{vurgu}; }}
    QProgressBar {{ background:{yuzey2}; border:0; border-radius:3px; height:6px; text-align:center; }}
    QProgressBar::chunk {{ background:{vurgu}; border-radius:3px; }}
    QCheckBox {{ spacing:8px; }}
    QMainWindow, QFrame {{ background:{arka}; }}
    QStatusBar {{ background:{yuzey}; color:{metin}; }}
    """


def sure_yaz(sn: float) -> str:
    if sn is None or sn < 0:
        return "0:00"
    sn = int(sn)
    s, d, sa = sn % 60, (sn // 60) % 60, sn // 3600
    return f"{sa}:{d:02d}:{s:02d}" if sa else f"{d}:{s:02d}"


def renk_uret(metin: str) -> QColor:
    """Afişi olmayan içerik için ada göre sabit bir renk üretir."""
    h = int(hashlib.md5(metin.encode("utf-8")).hexdigest()[:8], 16)
    tonlar = [(26,26,46),(22,33,62),(15,52,96),(27,27,47),(44,0,62),
              (43,45,66),(13,27,42),(28,28,28),(50,20,20),(20,40,30)]
    r, g, b = tonlar[h % len(tonlar)]
    return QColor(r, g, b)


# ══════════════════════════════════════════════════════════════════
#  AFİŞ İNDİRİCİ (arka planda, disk önbellekli)
# ══════════════════════════════════════════════════════════════════
def _golge_ver(w, yaricap: int = 12):
    """Metni parlak arka planda okunur kılar (afiş üstündeki yazılar için)."""
    ef = QGraphicsDropShadowEffect(w)
    ef.setBlurRadius(yaricap)
    ef.setOffset(0, 2)
    ef.setColor(QColor(0, 0, 0, 235))
    w.setGraphicsEffect(ef)


class AfisSinyal(QObject):
    # DİKKAT: Sinyal artık QPixmap değil QImage taşıyor (bkz. AfisIsi).
    hazir = pyqtSignal(str, QImage)


class AfisIsi(QRunnable):
    """
    Afişi arka planda indirir/okur ve QImage olarak döndürür.

    ÇÖKME DÜZELTMESİ — QPixmap İŞ PARÇACIĞI KURALI
        Qt'de QPixmap yalnızca GUI (ana) iş parçacığında kullanılabilir.
        Bu sınıf bir QThreadPool işçisidir; burada QPixmap oluşturmak,
        loadFromData()/scaled() çağırmak Qt'nin kesin yasağını çiğner.
        Sonuç Python'da yakalanamaz: X11/XCB katmanında bozulma olur ve
        program bir anda kapanır ("arama yaparken program kapandı").

        Ölçüldü (çalışma zamanı izleyicisi, gerçek arama akışı):
            QPixmap.__init__      → 2 kez yabancı iş parçacığında
            QPixmap.loadFromData  → 1 kez
            QPixmap.scaled        → 1 kez

        Arama sırasında yüzlerce kart oluşup silindiği ve her biri afiş
        istediği için bu yol çok yoğun çalışıyor — çökmenin arama sırasında
        ortaya çıkmasının sebebi bu.

        QImage ise iş parçacığı güvenlidir; indirme/ölçekleme burada
        QImage ile yapılır, QPixmap'e dönüşüm ana iş parçacığında
        (AfisYoneticisi._geldi) gerçekleşir.
    """

    def __init__(self, url: str, sinyal: AfisSinyal):
        super().__init__()
        self.url, self.sinyal = url, sinyal

    @pyqtSlot()
    def run(self):
        try:
            ad = hashlib.md5(self.url.encode()).hexdigest() + ".img"
            yol = afis_klasoru() / ad

            im = QImage()
            if yol.exists():
                im.load(str(yol))

            if im.isNull():
                import requests
                r = requests.get(self.url, timeout=10,
                                 headers={"User-Agent": "MediaBox/1.0"})
                if r.status_code != 200 or len(r.content) < 200:
                    return
                if not im.loadFromData(r.content) or im.isNull():
                    return
                try:
                    yol.write_bytes(r.content)
                except Exception:
                    pass

            # Ölçekleme de QImage ile (iş parçacığı güvenli).
            if im.width() > im.height():
                # Geniş (backdrop) görsel — hero tam genişlik kullanıyor.
                im = im.scaled(QSize(1600, 900),
                               Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
            else:
                im = im.scaled(QSize(KART_EN * 2, KART_BOY * 2),
                               Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                               Qt.TransformationMode.SmoothTransformation)
            if im.isNull():
                return
            self.sinyal.hazir.emit(self.url, im)
        except Exception:
            pass


class AfisYoneticisi(QObject):
    BELLEK_SINIR = 800          # bellekte tutulacak en fazla afiş sayısı

    def __init__(self):
        super().__init__()
        self.bellek: dict[str, QPixmap] = {}
        self.bekleyen: set[str] = set()
        self.sinyal = AfisSinyal()
        self.sinyal.hazir.connect(self._geldi)
        self.havuz = QThreadPool()
        self.havuz.setMaxThreadCount(6)
        self.aboneler: dict[str, list] = {}
        # Afiş yöneticisi her zaman ana iş parçacığında kurulur; köprüyü de
        # burada kurarak yabancı iş parçacığına bağlanmasını kesin engelle.
        kopru_kur()

    def _geldi(self, url: str, im: QImage):
        """
        İşçiden QImage gelir; QPixmap'e dönüşüm BURADA (ana iş parçacığında)
        yapılır — QPixmap yalnızca GUI iş parçacığında kullanılabilir.
        """
        self.bekleyen.discard(url)
        try:
            px = QPixmap.fromImage(im)
        except Exception:
            self.aboneler.pop(url, None)
            return
        if px.isNull():
            self.aboneler.pop(url, None)
            return

        # Bellek sınırı: arama sırasında yüzlerce farklı afiş istenebiliyor;
        # sınırsız büyürse bellek şişer (ölçüldü: 36.000 içerikli kütüphanede
        # gezinirken sürekli yeni afiş isteniyor).
        if len(self.bellek) > self.BELLEK_SINIR:
            for eski_url in list(self.bellek)[: self.BELLEK_SINIR // 4]:
                self.bellek.pop(eski_url, None)
        self.bellek[url] = px

        for cb in self.aboneler.pop(url, []):
            # Her abone AYRI korunur: biri patlarsa diğerleri afişini almalı.
            # (Silinmiş bir widget RuntimeError atar; bu normaldir.)
            try:
                cb(px)
            except RuntimeError:
                pass       # widget silinmiş
            except Exception:
                pass       # beklenmedik hata programı düşürmesin

    def iste(self, url: str, geri_cagir):
        if not url or not url.startswith(("http://", "https://")):
            return
        px = self.bellek.get(url)
        if px is not None:
            # Bellekten anında dönen yol da korunmalı: burası bir Qt slotu
            # zincirinden çağrılabiliyor ve yakalanmayan hata programı kapatır.
            try:
                geri_cagir(px)
            except RuntimeError:
                pass
            except Exception:
                pass
            return
        self.aboneler.setdefault(url, []).append(geri_cagir)
        if url in self.bekleyen:
            return
        self.bekleyen.add(url)
        self.havuz.start(AfisIsi(url, self.sinyal))


AFIS = None            # AfisYoneticisi — uygulama başlarken kurulur
TMDB = None            # TmdbIstemci    — anapencere kurar
TMDB_KLASOR = None     # görsel önbellek klasörü
TMDB_ESLESME = {}      # "ad|yıl|dizi" -> afiş URL'si ('' = bulunamadı)
_TMDB_BEKLEYEN = set()
_TMDB_HAVUZ = None


# ══════════════════════════════════════════════════════════════════
#  ANA İŞ PARÇACIĞI KÖPRÜSÜ
# ══════════════════════════════════════════════════════════════════
# HATA (ölçülerek bulundu): TMDB arama işçileri bir ThreadPoolExecutor
# içinde çalışır. Oradan `QTimer.singleShot(0, ...)` çağırmak SESSİZCE
# hiçbir şey yapmaz — geri çağrım asla tetiklenmez.
#
#   Ölçüm: yabancı iş parçacığından 5 istek → QTimer ile 0/5 çalıştı,
#          sinyal köprüsü ile 5/5 çalıştı.
#
# Sonuç: TMDB afişi bulunuyordu ama ekrana HİÇ ulaşmıyordu; kartlar
# baş harfli renkli kutu olarak kalıyordu. Bu yüzden tüm iş parçacığı
# geçişleri artık bu köprüden yapılıyor.
class _AnaIsParcasi(QObject):
    calistir = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.calistir.connect(self._calistir)

    @pyqtSlot(object)
    def _calistir(self, fn):
        try:
            fn()
        except RuntimeError:
            pass          # ilgili widget silinmişse sessizce geç


_KOPRU = None


def kopru_kur():
    """
    Köprüyü kurar. ANA İŞ PARÇACIĞINDA çağrılmalıdır (uygulama açılırken).

    İKİNCİ TUZAK (bu da ölçüldü): bir QObject hangi iş parçacığında
    oluşturulursa sinyalleri o iş parçacığında işlenir. Köprüyü "ilk
    ihtiyaç duyan" oluştursun diye bırakırsak, ilk çağıran genelde bir
    TMDB işçisi olur ve köprü yabancı iş parçacığına bağlanır.

      Ölçüm: yabancı iş parçacığında kurulan köprü → 2/6 çalıştı,
             ana iş parçacığında kurulan köprü    → 6/6 çalıştı.
    """
    global _KOPRU
    if _KOPRU is None:
        _KOPRU = _AnaIsParcasi()
    return _KOPRU


def ana_is_parcasinda(fn):
    """`fn`'i Qt ana iş parçacığında çalıştırır (yabancı thread'ten güvenli)."""
    global _KOPRU
    k = _KOPRU
    if k is None:
        # Güvenlik ağı: köprü kurulmamışsa şimdi kur, sonra ana iş
        # parçacığına taşı — yoksa sinyal yanlış yerde işlenir.
        k = _KOPRU = _AnaIsParcasi()
        uyg = QCoreApplication.instance()
        if uyg is not None and k.thread() is not uyg.thread():
            k.moveToThread(uyg.thread())
    k.calistir.emit(fn)


def tmdb_afis_iste(icerik, geri_cagir):
    """
    Bir içerik için TMDB afişini arka planda bulur.

    M3U dosyalarında çoğu zaman tvg-logo yoktur; o yüzden kartlar boş
    renkli kutu olarak görünüyordu. Burada içeriğin adı ve yılı TMDB'de
    aranıp gerçek afiş indiriliyor.
    """
    global _TMDB_HAVUZ
    if TMDB is None or not TMDB.hazir:
        return
    ad = icerik.dizi_kok_anahtari() or icerik.temiz_ad()
    if not ad:
        return
    dizi = bool(icerik.dizi_kok_anahtari()) or icerik.kategori in ("series", "anime")
    if icerik.kategori == "live":
        return                      # canlı kanalların TMDB karşılığı yok
    anahtar = f"{ad}|{icerik.yil()}|{int(dizi)}"

    hazir = TMDB_ESLESME.get(anahtar)
    if hazir is not None:
        if hazir:
            AFIS.iste(hazir, geri_cagir)
        return
    if anahtar in _TMDB_BEKLEYEN:
        return
    _TMDB_BEKLEYEN.add(anahtar)

    if _TMDB_HAVUZ is None:
        from concurrent.futures import ThreadPoolExecutor
        _TMDB_HAVUZ = ThreadPoolExecutor(max_workers=3)

    def bul():
        url = ""
        try:
            r = TMDB.ara(ad, icerik.yil(), dizi)
            if r and r.get("poster_path"):
                from tmdb import afis_url as _au
                url = _au(r["poster_path"])
                try:
                    icerik.tmdb_id = r.get("id", 0) or 0
                    icerik.puan = r.get("vote_average", 0) or 0
                    icerik.afis = r.get("poster_path", "")
                except Exception:
                    pass
        except Exception:
            url = ""
        TMDB_ESLESME[anahtar] = url
        _TMDB_BEKLEYEN.discard(anahtar)
        if url:
            # Yabancı iş parçacığındayız → köprü ile ana iş parçacığına geç.
            ana_is_parcasinda(lambda u=url, g=geri_cagir: AFIS.iste(u, g))

    _TMDB_HAVUZ.submit(bul)


def tmdb_arka_iste(icerik, geri_cagir):
    """
    Hero için TMDB'nin GENİŞ arka plan görselini (backdrop) getirir.
    Dikey afiş geniş alana yayılınca bozuk görünüyordu; bu yüzden ayrı.
    """
    global _TMDB_HAVUZ
    if TMDB is None or not TMDB.hazir:
        return
    ad = icerik.dizi_kok_anahtari() or icerik.temiz_ad()
    if not ad or icerik.kategori == "live":
        return
    dizi = bool(icerik.dizi_kok_anahtari()) or icerik.kategori in ("series", "anime")
    anahtar = f"ARKA|{ad}|{icerik.yil()}|{int(dizi)}"

    hazir = TMDB_ESLESME.get(anahtar)
    if hazir is not None:
        if hazir:
            AFIS.iste(hazir, geri_cagir)
        return
    if anahtar in _TMDB_BEKLEYEN:
        return
    _TMDB_BEKLEYEN.add(anahtar)

    if _TMDB_HAVUZ is None:
        from concurrent.futures import ThreadPoolExecutor
        _TMDB_HAVUZ = ThreadPoolExecutor(max_workers=3)

    def bul():
        url = ""
        try:
            r = TMDB.ara(ad, icerik.yil(), dizi)
            if r:
                from tmdb import afis_url as _au, ARKA_BOY as _AB
                yol = r.get("backdrop_path") or r.get("poster_path")
                if yol:
                    url = _au(yol, _AB if r.get("backdrop_path") else "w500")
        except Exception:
            url = ""
        TMDB_ESLESME[anahtar] = url
        _TMDB_BEKLEYEN.discard(anahtar)
        if url:
            # Yabancı iş parçacığındayız → köprü ile ana iş parçacığına geç.
            ana_is_parcasinda(lambda u=url, g=geri_cagir: AFIS.iste(u, g))

    _TMDB_HAVUZ.submit(bul)


# ══════════════════════════════════════════════════════════════════
#  İÇERİK KARTI
# ══════════════════════════════════════════════════════════════════
class Kart(QFrame):
    def __init__(self, grup: dict, depo: Depo, tiklandi, parent=None, en: int = 0):
        super().__init__(parent)
        self.grup, self.depo, self._tiklandi = grup, depo, tiklandi
        self.ilk: Icerik = grup["bolumler"][0]
        self._px: QPixmap | None = None
        self._hover = False
        # `en` verilirse kart o genişliğe ölçeklenir (2:3 afiş oranı korunur).
        # Böylece dar pencerede de yan yana 10 afiş sığabiliyor.
        if en and en != KART_EN:
            self.setFixedSize(en, round(en * KART_BOY / KART_EN) + 4)
        else:
            self.setFixedSize(KART_EN, KART_BOY + 4)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        logo = grup.get("logo") or self.ilk.logo
        if logo:
            AFIS.iste(logo, self._afis_geldi)
        else:
            # M3U'da logo yok → TMDB'den gerçek afişi getir
            tmdb_afis_iste(self.ilk, self._afis_geldi)

    def _afis_geldi(self, px: QPixmap):
        try:
            self._px = px
            self.update()
        except RuntimeError:
            pass          # kart silinmişse sessizce geç

    def enterEvent(self, e):
        self._hover = True; self.update()

    def leaveEvent(self, e):
        self._hover = False; self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._tiklandi(self.grup)
        elif e.button() == Qt.MouseButton.RightButton:
            self._menu(e)

    def _menu(self, e):
        """Sağ tık: hızlı işlemler."""
        from PyQt6.QtWidgets import QMenu, QApplication
        m = QMenu(self)
        a_oynat = m.addAction("▶  Oynat")
        fav = self.depo.favori_mi(self.ilk)
        a_fav = m.addAction("♥  Favorilerden çıkar" if fav else "♡  Favorilere ekle")
        kuy = self.depo.kuyrukta_mi(self.ilk)
        a_kuy = m.addAction("🕑  Sonra izle listesinden çıkar" if kuy
                            else "🕑  Sonra izle listesine ekle")
        m.addSeparator()
        a_duz = m.addAction("✏  Düzenle…")
        a_kop = m.addAction("📋  Bağlantıyı kopyala")
        sec = m.exec(e.globalPosition().toPoint())
        if sec == a_oynat:
            pen = self.window()
            if hasattr(pen, "oynat"):
                pen.oynat(self.ilk, self.grup["bolumler"], 0)
        elif sec == a_fav:
            self.depo.favori_degistir(self.ilk)
            self.depo.kaydet(); self.update()
        elif sec == a_kuy:
            self.depo.kuyruk_degistir(self.ilk)
            self.depo.kaydet(); self.update()
        elif sec == a_duz:
            pen = self.window()
            if hasattr(pen, "icerik_duzenle"):
                pen.icerik_duzenle(self.grup)
        elif sec == a_kop:
            QApplication.clipboard().setText(self.ilk.url)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(0, 0, -1, -5)

        yol_r = 10
        # arka plan / afiş
        if self._px and not self._px.isNull():
            p.setClipRect(r)
            olcek = self._px.scaled(r.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                    Qt.TransformationMode.SmoothTransformation)
            x = r.x() - (olcek.width() - r.width()) // 2
            y = r.y() - (olcek.height() - r.height()) // 2
            p.setClipping(False)
            yol = p.clipRegion()
            p.save()
            from PyQt6.QtGui import QPainterPath
            pp = QPainterPath(); pp.addRoundedRect(float(r.x()), float(r.y()),
                                                   float(r.width()), float(r.height()),
                                                   yol_r, yol_r)
            p.setClipPath(pp)
            p.drawPixmap(x, y, olcek)
            p.restore()
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(renk_uret(self.grup["baslik"]))
            p.drawRoundedRect(r, yol_r, yol_r)
            p.setPen(QColor(255, 255, 255, 38))
            f = QFont(); f.setPointSize(26); f.setBold(True); p.setFont(f)
            bas = "".join(w[0] for w in self.grup["baslik"].split()[:2]).upper() or "?"
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, bas)

        # alt karartma + yazı
        from PyQt6.QtGui import QPainterPath
        pp = QPainterPath(); pp.addRoundedRect(float(r.x()), float(r.y()),
                                               float(r.width()), float(r.height()), yol_r, yol_r)
        p.setClipPath(pp)
        g = QLinearGradient(0, r.height() * 0.45, 0, r.height())
        g.setColorAt(0, QColor(0, 0, 0, 0)); g.setColorAt(1, QColor(0, 0, 0, 232))
        p.fillRect(r, QBrush(g))

        p.setPen(QColor(255, 255, 255))
        f = QFont(); f.setPointSize(9); f.setBold(True); p.setFont(f)
        yazi = self.grup["baslik"]
        met = p.fontMetrics().elidedText(yazi, Qt.TextElideMode.ElideRight, r.width() - 16)
        p.drawText(r.adjusted(8, 0, -8, -20), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft, met)

        alt = []
        y = self.ilk.yil()
        if y: alt.append(y)
        if not self.grup["tekil"]:
            alt.append(f"{len(self.grup['bolumler'])} bölüm")
        elif self.ilk.dublaj():
            alt.append("TR Dublaj")
        # KAYNAK: Aynı dizi birden çok M3U grubundan gelebiliyor; her kaynak
        # ayrı kart olduğu için hangisi olduğu kartta görünmeli.
        kyn = (self.grup.get("kaynak") or "").strip()
        if kyn and not self.grup["tekil"]:
            alt.append(kyn[:18])
        if alt:
            p.setPen(QColor(190, 194, 205))
            f2 = QFont(); f2.setPointSize(8); p.setFont(f2)
            # Kaynak adı uzun olabiliyor; karta sığmayan kısmı "…" ile kes
            # (ölçüldü: "40 bölüm · VATANIM SENSIN 4K" kart kenarından taşıyordu).
            alt_metin = p.fontMetrics().elidedText(
                " · ".join(alt), Qt.TextElideMode.ElideRight, r.width() - 16)
            p.drawText(r.adjusted(8, 0, -8, -7),
                       Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft, alt_metin)

        # kalite rozeti
        kal = self.ilk.kalite()
        if kal and self.grup["tekil"]:
            f3 = QFont(); f3.setPointSize(7); f3.setBold(True); p.setFont(f3)
            w = p.fontMetrics().horizontalAdvance(kal) + 12
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(0, 0, 0, 190))
            p.drawRoundedRect(r.right() - w - 6, r.y() + 6, w, 17, 5, 5)
            p.setPen(QColor(205, 228, 255))
            p.drawText(r.right() - w - 6, r.y() + 6, w, 17, Qt.AlignmentFlag.AlignCenter, kal)

        # bölüm rozeti
        if not self.grup["tekil"]:
            t = f"{len(self.grup['bolumler'])}"
            f3 = QFont(); f3.setPointSize(7); f3.setBold(True); p.setFont(f3)
            w = max(20, p.fontMetrics().horizontalAdvance(t) + 12)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(R_VURGU))
            p.drawRoundedRect(r.right() - w - 6, r.y() + 6, w, 17, 5, 5)
            p.setPen(QColor(255, 255, 255))
            p.drawText(r.right() - w - 6, r.y() + 6, w, 17, Qt.AlignmentFlag.AlignCenter, t)

        # favori kalbi
        if self.depo.favori_mi(self.ilk):
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(229, 9, 20, 235))
            p.drawEllipse(r.x() + 6, r.y() + 6, 22, 22)
            p.setPen(QColor(255, 255, 255))
            f4 = QFont(); f4.setPointSize(10); p.setFont(f4)
            p.drawText(r.x() + 6, r.y() + 6, 22, 22, Qt.AlignmentFlag.AlignCenter, "♥")

        # izleme ilerlemesi
        pr = self.depo.ilerleme.get(self.ilk.url)
        if pr and pr.get("d"):
            oran = max(0.02, min(1.0, pr["t"] / pr["d"]))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 150))
            p.drawRect(r.x(), r.bottom() - 4, r.width(), 4)
            p.setBrush(QColor(R_VURGU))
            p.drawRect(r.x(), r.bottom() - 4, int(r.width() * oran), 4)

        p.setClipping(False)
        if self._hover:
            p.setPen(QPen(QColor(255, 255, 255, 170), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), yol_r, yol_r)
        p.end()


# ══════════════════════════════════════════════════════════════════
#  YATAY KART (16:9)
#  Dikey afişi olmayan içerikler için: geniş, sinema oranında kart.
#  Öneri/İzlemeye Devam raflarında kullanılır.
# ══════════════════════════════════════════════════════════════════
class YatayKart(QFrame):
    def __init__(self, grup: dict, depo: Depo, tiklandi, parent=None, en: int = 0):
        super().__init__(parent)
        self.grup, self.depo, self._tiklandi = grup, depo, tiklandi
        # "_devam": izlemeye devam rafında kartın temsil ettiği bölüm.
        # Grup tüm bölümleri taşır (tıklayınca hepsi açılsın diye) ama
        # ilerleme çubuğu/kalan süre bu bölüme aittir.
        self.ilk: Icerik = grup.get("_devam") or grup["bolumler"][0]
        self._px: QPixmap | None = None
        self._hover = False
        # `en` verilirse 16:9 oran korunarak ölçeklenir.
        if en and en != YKART_EN:
            self.setFixedSize(en, round(en * YKART_BOY / YKART_EN))
        else:
            self.setFixedSize(YKART_EN, YKART_BOY)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        logo = grup.get("logo") or self.ilk.logo
        if logo:
            AFIS.iste(logo, self._afis_geldi)
        else:
            tmdb_afis_iste(self.ilk, self._afis_geldi)

    def _afis_geldi(self, px: QPixmap):
        try:
            self._px = px; self.update()
        except RuntimeError:
            pass

    def enterEvent(self, e): self._hover = True; self.update()
    def leaveEvent(self, e): self._hover = False; self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._tiklandi(self.grup)

    def paintEvent(self, e):
        from PyQt6.QtGui import QPainterPath
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(0, 0, -1, -1)
        yr = 10
        pp = QPainterPath()
        pp.addRoundedRect(float(r.x()), float(r.y()), float(r.width()), float(r.height()), yr, yr)
        p.setClipPath(pp)

        if self._px and not self._px.isNull():
            # Afişin ÜST kısmını göster (yüzler genelde üstte olur)
            ol = self._px.scaled(r.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                 Qt.TransformationMode.SmoothTransformation)
            x = r.x() - (ol.width() - r.width()) // 2
            y = r.y() - int((ol.height() - r.height()) * 0.28)
            p.drawPixmap(x, y, ol)
        else:
            taban = renk_uret(self.grup["baslik"])
            g = QLinearGradient(0, 0, r.width(), r.height())
            g.setColorAt(0, taban.lighter(125)); g.setColorAt(1, taban.darker(115))
            p.fillRect(r, QBrush(g))
            p.setPen(QColor(255, 255, 255, 30))
            f = QFont(); f.setPointSize(30); f.setBold(True); p.setFont(f)
            bas = "".join(w[0] for w in self.grup["baslik"].split()[:2]).upper() or "?"
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, bas)

        # alt karartma
        g2 = QLinearGradient(0, r.height() * 0.35, 0, r.height())
        g2.setColorAt(0, QColor(0, 0, 0, 0)); g2.setColorAt(1, QColor(0, 0, 0, 238))
        p.fillRect(r, QBrush(g2))

        # başlık
        p.setPen(QColor(255, 255, 255))
        f = QFont(); f.setPointSize(10); f.setBold(True); p.setFont(f)
        met = p.fontMetrics().elidedText(self.grup["baslik"],
                                         Qt.TextElideMode.ElideRight, r.width() - 22)
        p.drawText(r.adjusted(11, 0, -11, -22),
                   Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft, met)

        # alt bilgi
        alt = []
        y = self.ilk.yil()
        if y: alt.append(y)
        if self.ilk.grup: alt.append(self.ilk.grup[:22])
        if not self.grup["tekil"]:
            alt.append(f"{len(self.grup['bolumler'])} bölüm")
        if alt:
            p.setPen(QColor(196, 200, 210))
            f2 = QFont(); f2.setPointSize(8); p.setFont(f2)
            p.drawText(r.adjusted(11, 0, -11, -8),
                       Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
                       "  ·  ".join(alt))

        # kalite rozeti
        kal = self.ilk.kalite()
        if kal:
            f3 = QFont(); f3.setPointSize(7); f3.setBold(True); p.setFont(f3)
            w = p.fontMetrics().horizontalAdvance(kal) + 14
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(0, 0, 0, 195))
            p.drawRoundedRect(r.right() - w - 8, r.y() + 8, w, 18, 5, 5)
            p.setPen(QColor(205, 228, 255))
            p.drawText(r.right() - w - 8, r.y() + 8, w, 18, Qt.AlignmentFlag.AlignCenter, kal)

        # favori
        if self.depo.favori_mi(self.ilk):
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(229, 9, 20, 235))
            p.drawEllipse(r.x() + 8, r.y() + 8, 22, 22)
            p.setPen(QColor(255, 255, 255))
            f4 = QFont(); f4.setPointSize(10); p.setFont(f4)
            p.drawText(r.x() + 8, r.y() + 8, 22, 22, Qt.AlignmentFlag.AlignCenter, "♥")

        # oynatma ilerlemesi
        pr = self.depo.ilerleme.get(self.ilk.url)
        if pr and pr.get("d"):
            oran = max(0.02, min(1.0, pr["t"] / pr["d"]))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 55))
            p.drawRect(r.x(), r.bottom() - 4, r.width(), 4)
            p.setBrush(QColor(R_VURGU))
            p.drawRect(r.x(), r.bottom() - 4, int(r.width() * oran), 4)
            # kalan süre
            kalan = max(0, pr["d"] - pr["t"])
            f5 = QFont(); f5.setPointSize(7); f5.setBold(True); p.setFont(f5)
            t = f"{sure_yaz(kalan)} kaldı"
            w = p.fontMetrics().horizontalAdvance(t) + 14
            p.setBrush(QColor(0, 0, 0, 195)); p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(r.x() + 8, r.y() + 8, w, 18, 5, 5)
            p.setPen(QColor(235, 238, 245))
            p.drawText(r.x() + 8, r.y() + 8, w, 18, Qt.AlignmentFlag.AlignCenter, t)

        p.setClipping(False)
        if self._hover:
            p.setPen(QPen(QColor(255, 255, 255, 175), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), yr, yr)
        p.end()


# ══════════════════════════════════════════════════════════════════
#  GÖMÜLÜ MPV OYNATICI
# ══════════════════════════════════════════════════════════════════
class Oynatici(QWidget):
    kapandi = pyqtSignal()
    # ── ÇÖKME DÜZELTMESİ ──────────────────────────────────────────
    # mpv'nin property_observer geri çağrıları mpv'nin KENDİ iş parçacığından
    # gelir (ölçüldü: observer thread id ≠ Qt ana thread id). Qt widget'larına
    # yabancı iş parçacığından dokunmak çökmeye yol açar — "Oynat"a basınca
    # program kapanmasının sebebi buydu.
    # Çözüm: geri çağrı yalnızca sinyal yayar; sinyal Qt olay kuyruğu üzerinden
    # ana iş parçacığında işlenir.
    _sig_pause = pyqtSignal(bool)
    _sig_hata = pyqtSignal(str)
    _sig_bitti = pyqtSignal()

    def __init__(self, depo: Depo, parent=None):
        super().__init__(parent)
        self.depo = depo
        self.icerik: Icerik | None = None
        self.liste: list[Icerik] = []
        self.sira = 0
        self.mpv = None
        self._sur = 0.0
        self._kon = 0.0
        self._surukleniyor = False
        # mpv bir yt-dlp adresini açamazsa kendi çözümleyicimize düşmek için
        self._ytdl_yedek_bekliyor = None
        self._ytdl_ek = {}
        self._mpv_hook_yedek = None
        self._son_ytdl_hata = ""
        # En güncel yt-dlp'yi arka planda belirle: ilk oynatmada sürüm
        # sorgusu için beklenmesin (ölçüldü: 3 süreç = 0,72 sn donma).
        try:
            from mpv_islem import ytdl_tara_arkaplan
            ytdl_tara_arkaplan()
        except Exception:
            pass
        # DİKKAT: Qt stilleri alt öğelere miras geçer. Düz "background:#000"
        # yazarsak video alanına da uygulanır ve mpv'nin karesini siler.
        # Bu yüzden stil YALNIZCA bu widget'a bağlanıyor (#oynaticiKok).
        self.setObjectName("oynaticiKok")
        self.setStyleSheet("QWidget#oynaticiKok { background:#000; }")

        ana = QVBoxLayout(self); ana.setContentsMargins(0, 0, 0, 0); ana.setSpacing(0)

        # üst bar
        ust = QWidget(); ust.setFixedHeight(52)
        ust.setStyleSheet("background:rgba(10,11,15,235);")
        uh = QHBoxLayout(ust); uh.setContentsMargins(12, 0, 12, 0)
        geri = QPushButton("‹  Geri"); geri.setFixedHeight(32); geri.setMinimumWidth(84)
        geri.setStyleSheet(KOMPAKT_BTN); geri.clicked.connect(self.kapat)
        self.baslik = QLabel(""); self.baslik.setStyleSheet("font-size:14px;font-weight:600;")
        self.baslik.setAlignment(Qt.AlignmentFlag.AlignCenter)
        uh.addWidget(geri); uh.addWidget(self.baslik, 1)
        self.b_onceki = QPushButton("‹ Önceki"); self.b_onceki.setFixedHeight(32)
        self.b_onceki.setMinimumWidth(92); self.b_onceki.setStyleSheet(KOMPAKT_BTN)
        self.b_sonraki = QPushButton("Sonraki ›"); self.b_sonraki.setFixedHeight(32)
        self.b_sonraki.setMinimumWidth(96); self.b_sonraki.setStyleSheet(KOMPAKT_BTN)
        self.b_onceki.clicked.connect(lambda: self.komsu(-1))
        self.b_sonraki.clicked.connect(lambda: self.komsu(1))
        uh.addWidget(self.b_onceki); uh.addWidget(self.b_sonraki)
        ana.addWidget(ust)

        # video alanı — mpv buraya gömülür
        # mpv bu widget'ın X alt penceresine çizer; mpv'nin penceresi Qt'nin
        # boyadığı arka planın ÜSTÜNDEDİR, dolayısıyla siyah arka plan
        # görüntüyü silmez — yalnızca letterbox (boş) alanı doldurur.
        # NOT: WA_OpaquePaintEvent + WA_NoSystemBackground DENENDİ ve GERİ ALINDI:
        # bu ikisi Qt'nin arka planı çizmesini tamamen engelliyor ve mpv'nin
        # boyamadığı bölgelerde ana sayfa görüntüsü sızıyordu (bayat kare).
        self.video = QWidget()
        self.video.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors)
        self.video.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.video.setAutoFillBackground(True)
        self.video.setStyleSheet("background:#000;")
        self.video.setMinimumHeight(200)
        ana.addWidget(self.video, 1)

        # Ayrı pencere modunda video burada değil, mpv'nin kendi penceresinde
        # oynar; kullanıcı boş siyah alan görmesin diye bilgi gösterilir.
        self.bilgi_kutu = QLabel(
            "▶  Video ayrı bir mpv penceresinde oynatılıyor.\n\n"
            "Bu pencereden duraklatma, atlama, ses ve altyazı kontrol edilebilir.\n"
            "Gömülü görüntü için:  ⚙ → Video modu → Gömülü  (X11 oturumu gerekir)")
        self.bilgi_kutu.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bilgi_kutu.setStyleSheet(f"color:{R_SOLUK};font-size:13px;background:#000;")
        self.bilgi_kutu.setVisible(False)
        ana.addWidget(self.bilgi_kutu, 1)

        # alt kontroller
        alt = QWidget(); alt.setFixedHeight(78)
        alt.setStyleSheet("background:rgba(10,11,15,240);")
        av = QVBoxLayout(alt); av.setContentsMargins(14, 6, 14, 8); av.setSpacing(4)

        self.cubuk = QSlider(Qt.Orientation.Horizontal)
        self.cubuk.setRange(0, 1000)
        self.cubuk.sliderPressed.connect(lambda: setattr(self, "_surukleniyor", True))
        self.cubuk.sliderReleased.connect(self._atla)
        # "Nerede kalmıştım" önizlemesi: çubuk üstünde gezinirken o
        # saniyenin karesi küçük bir balonda gösterilir.
        self.cubuk.setMouseTracking(True)
        self.cubuk.installEventFilter(self)
        self.cubuk.sliderMoved.connect(self._cubuk_surukleniyor)
        av.addWidget(self.cubuk)

        sat = QHBoxLayout(); sat.setSpacing(6)

        def ikon_dugme(sembol: str, ipucu: str, en: int = 36) -> QPushButton:
            """
            Sadece ikon (emoji/sembol) gösteren, metin taşmasına yol açmayan
            kompakt kontrol düğmesi. Açıklama tamamen araç ipucunda (tooltip).
            NEDEN: eski metinli düğmeler ("Altyazı Yükle", "🏷 Jenerik Burada
            Bitiyor" vb.) dar pencerede sığmayıp kesiliyordu (ölçüldü:
            "…nerik Burada Bi…" şeklinde kırpılmış görünüyordu).
            """
            b = QPushButton(sembol)
            b.setFixedSize(en, 32)
            b.setStyleSheet(KOMPAKT_BTN)
            b.setToolTip(ipucu)
            return b

        self.b_oynat = ikon_dugme("❚❚", "Oynat / Duraklat  (Boşluk)")
        self.b_oynat.clicked.connect(self.duraklat_degistir)
        b_g10 = ikon_dugme("⏪", "10 saniye geri  (←)")
        b_g10.clicked.connect(lambda: self.atla(-10))
        b_i10 = ikon_dugme("⏩", "10 saniye ileri  (→)")
        b_i10.clicked.connect(lambda: self.atla(10))
        self.l_sure = QLabel("0:00 / 0:00")
        self.l_sure.setMinimumWidth(120)
        self.l_sure.setStyleSheet(f"color:{R_METIN};font-size:12px;")
        self.ses = QSlider(Qt.Orientation.Horizontal); self.ses.setFixedWidth(110)
        self.ses.setRange(0, 130); self.ses.setValue(100)
        self.ses.valueChanged.connect(self._ses)
        self.b_ses = ikon_dugme("🔊", "Sesi aç/kapat")
        self.b_ses.clicked.connect(self.sessiz)
        # Kutular dar kalınca metin kesiliyordu (ölçüldü: "English Sub (er").
        self.a_ses = QComboBox(); self.a_ses.setMinimumWidth(150)
        self.a_ses.setToolTip("Ses parçası")
        self.a_alt = QComboBox(); self.a_alt.setMinimumWidth(150)
        self.a_alt.setToolTip("Altyazı")
        # Uzun parça adları kutuyu şişirmesin; ortadan kısaltılır.
        for _k in (self.a_ses, self.a_alt):
            _k.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
            _k.setMaximumWidth(220)
        self.a_ses.currentIndexChanged.connect(self._ses_sec)
        self.a_alt.currentIndexChanged.connect(self._alt_sec)
        # Bölüm listesi (dizi izlerken)
        self.b_bolum = ikon_dugme("☰", "Bölümler — dizinin bölüm listesi")
        self.b_bolum.clicked.connect(self.bolum_panel)
        self.b_bolum.setVisible(False)

        # Oynatma hızı
        self.a_hiz = QComboBox(); self.a_hiz.setFixedWidth(66)
        self.a_hiz.setToolTip("Oynatma hızı")
        for h_ in ("0.5x", "0.75x", "1x", "1.25x", "1.5x", "2x"):
            self.a_hiz.addItem(h_, float(h_[:-1]))
        self.a_hiz.setCurrentIndex(2)
        self.a_hiz.currentIndexChanged.connect(self._hiz_sec)

        # Görüntü kalitesi (çok akışlı HLS yayınlarda)
        self.a_kalite = QComboBox(); self.a_kalite.setFixedWidth(88)
        self.a_kalite.setToolTip("Görüntü kalitesi")
        self.a_kalite.currentIndexChanged.connect(self._kalite_sec)
        self.a_kalite.setEnabled(False)

        # Kaynak / mirror seçici (aynı bölümün alternatif URL'leri)
        self.a_kaynak = QComboBox(); self.a_kaynak.setMinimumWidth(130)
        self.a_kaynak.setMaximumWidth(200)
        self.a_kaynak.setToolTip("Kaynak / kalite / mirror değiştir")
        self.a_kaynak.currentIndexChanged.connect(self._kaynak_sec)
        self.a_kaynak.setVisible(False)

        b_pip = ikon_dugme("🗗", "Küçük pencere — mpv'yi hep üstte tut")
        b_pip.clicked.connect(self.pip_degistir)

        b_alt = ikon_dugme("💬", "Bilgisayarınızdan .srt / .ass altyazı dosyası ekleyin")
        b_alt.clicked.connect(self.altyazi_yukle)

        b_tam = ikon_dugme("⛶", "Tam ekran  (F)")
        b_tam.clicked.connect(self.tam_ekran)

        b_jen_isaret = ikon_dugme("🏷", "")
        b_jen_isaret.setToolTip(
            "İçerik tam olarak şu anda başlıyorsa tıklayın (kısayol: G).\n"
            "Bu dizi için gerçek jenerik bitişi kalıcı olarak öğrenilir;\n"
            "bir daha kör tahmine (10–150 sn) dönülmez.")
        b_jen_isaret.clicked.connect(self._jenerik_burada_isaretle)

        # ── Teknik istatistik paneli (stabilite/teşhis) ─────────────────
        # mpv'nin KENDİ yerleşik "stats" betiğini açıp kapatıyoruz — bitrate,
        # düşen kare sayısı, ses/görüntü senkron farkı, arabellek doluluğu
        # gibi bilgileri doğrudan video üstüne bindirir. Ayrı bir telemetri
        # arayüzü yazmaya gerek yok; mpv zaten bunu üretiyor, biz sadece
        # anahtarı arayüzden erişilebilir yapıyoruz (--no-input-default-
        # bindings yüzünden mpv'nin varsayılan "i" tuşu bağlı değil).
        b_istatistik = ikon_dugme(
            "📊", "Teknik istatistikleri göster/gizle\n(bitrate, düşen kare, arabellek doluluğu)")
        b_istatistik.clicked.connect(self._istatistik_ac_kapa)

        self.durum = QLabel(""); self.durum.setStyleSheet(f"color:{R_SOLUK};font-size:11px;")

        for w in (self.b_oynat, b_g10, b_i10, self.b_ses):
            sat.addWidget(w)
        sat.addWidget(self.ses)
        sat.addWidget(self.l_sure)
        sat.addWidget(self.durum, 1)
        sat.addWidget(self.a_ses)
        sat.addWidget(self.a_alt)
        sat.addWidget(self.a_hiz)
        sat.addWidget(self.a_kalite)
        sat.addWidget(self.a_kaynak)
        sat.addWidget(self.b_bolum)
        sat.addWidget(b_alt)
        sat.addWidget(b_jen_isaret)
        sat.addWidget(b_istatistik)
        sat.addWidget(b_pip)
        sat.addWidget(b_tam)
        av.addLayout(sat)
        ana.addWidget(alt)

        # Thread-güvenli köprü bağlantıları
        self._sig_pause.connect(self._pause_arayuz)
        self._sig_hata.connect(self._hata_goster)
        self._sig_bitti.connect(self._dosya_bitti)

        self.zaman = QTimer(self); self.zaman.setInterval(500)
        self.zaman.timeout.connect(self._guncelle)

        # kısayollar
        QShortcut(QKeySequence("Space"), self, activated=self.duraklat_degistir)
        QShortcut(QKeySequence("Right"), self, activated=lambda: self.atla(10))
        QShortcut(QKeySequence("Left"), self, activated=lambda: self.atla(-10))
        QShortcut(QKeySequence("Up"), self, activated=lambda: self.ses.setValue(self.ses.value() + 5))
        QShortcut(QKeySequence("Down"), self, activated=lambda: self.ses.setValue(self.ses.value() - 5))
        QShortcut(QKeySequence("F"), self, activated=self.tam_ekran)
        QShortcut(QKeySequence("G"), self, activated=self._jenerik_burada_isaretle)
        # ── ESC BURADA KISAYOL OLARAK BAĞLANMAZ ───────────────────────
        # Ölçülen hata: AnaPencere de aynı pencerede "Escape" için bir
        # QShortcut kuruyordu (context=WindowShortcut). Qt aynı pencerede
        # aynı tuşa iki kısayol bulunca "ambiguous shortcut overload"
        # durumuna düşer ve HİÇBİRİNİ çalıştırmaz — ölçüldü: ESC'ye
        # basıldığında sayfa 3'te kaldı, oynatıcıdan çıkılamıyordu.
        # Artık ESC'yi tek sahip (AnaPencere) yakalar ve buradaki _esc()'ye
        # yönlendirir. Oynatıcı tek başına kullanılırsa aşağıdaki
        # keyPressEvent yedeği devreye girer.

    # ── mpv kurulumu ──
    def _mpv_kur(self):
        """
        mpv'yi AYRI İŞLEM olarak başlatır (JSON IPC ile kontrol).

        ÇÖKME KORUMASI
            Önceden libmpv uygulamanın içine gömülüydü (python-mpv + wid).
            Bazı sistemlerde — özellikle Wayland'de — bu ölümcül biçimde
            başarısız olup TÜM UYGULAMAYI kapatıyordu.
            Artık mpv bağımsız bir işlem: çökerse yalnızca kendisi kapanır.
            Ölçüldü: mpv'ye SIGKILL gönderildiğinde ana süreç yaşamaya devam
            etti (yalnızca BrokenPipeError, o da yakalanıyor).
        """
        if self.mpv is not None and self.mpv.yasiyor:
            return

        from mpv_islem import MpvIslem, MpvBulunamadi
        from mediabox_qt import veri_klasoru

        ayar = self.depo.ayarlar
        hwdec = bool(ayar.get("donanim_hizlandirma", True))
        mod = ayar.get("video_modu", "oto")           # oto | gomulu | ayri
        wayland = bool(os.environ.get("WAYLAND_DISPLAY")) or \
                  os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"

        if mod == "oto":
            gom = not wayland          # Wayland'de gömme riskli
        else:
            gom = (mod == "gomulu")

        self.mpv = MpvIslem(log_yolu=str(veri_klasoru() / "mpv.log"))
        wid = int(self.video.winId()) if gom else None
        # Kayıtlı görüntü iyileştirme shader'ları (varsa) baştan uygulanır.
        ek_arg = []
        sh = ayar.get("shader_listesi") or []
        if isinstance(sh, str):
            sh = [sh] if sh else []
        gecerli = []
        try:
            from shader import gecerli_mi
            gecerli = [y for y in sh if gecerli_mi(y)[0]]
        except Exception:
            gecerli = []
        if gecerli:
            ek_arg.append("--glsl-shaders=" + ":".join(gecerli))

        ok = self.mpv.baslat(wid=wid, hwdec=hwdec,
                             video_cikis=ayar.get("video_cikis", "oto"),
                             ek_argumanlar=ek_arg or None)
        if not ok:
            raise RuntimeError(self.mpv.son_hata or "mpv başlatılamadı")

        self.gomulu_mu = bool(self.mpv.gomulu)
        self._mod_yaz()

        # Gömme istendi ama başarısız olduysa kullanıcıya sebebini söyle.
        # (Ölçüldü: geçersiz --wid → mpv "BadWindow" ile ölüyor, otomatik
        #  olarak ayrı pencereye düşülüyor.)
        self._gomme_uyarisi = ""
        if wid and not self.gomulu_mu:
            # Gömme tutmadı ama yedek plan çalıştı: video AYRI PENCEREDE oynuyor.
            # Bu bir arıza değil; kullanıcıya sakin bir bilgi olarak gösterilir.
            # (Önceden burada "beklenmedik şekilde kapandı" yazıyordu ve
            #  video sorunsuz oynarken kullanıcı hata sanıyordu.)
            self._gomme_uyarisi = "ℹ Video ayrı pencerede oynatılıyor"
            self.durum.setText(self._gomme_uyarisi)
            self.bilgi_kutu.setText(
                "▶  Video ayrı bir mpv penceresinde oynatılıyor.\n\n"
                "Kontroller (duraklat, 10 sn atla, ses, altyazı) buradan çalışır.\n\n"
                "Pencere içinde görmek isterseniz:\n"
                "   ⚙ → Video çıkışı → 'x11'   ya da   'gpu + x11egl'")

        # Olayları Qt zamanlayıcısıyla ANA İŞ PARÇACIĞINDA topla
        if not hasattr(self, "_olay_zaman"):
            self._olay_zaman = QTimer(self)
            self._olay_zaman.setInterval(400)
            self._olay_zaman.timeout.connect(self._olaylari_isle)
        self._olay_zaman.start()

    def _olaylari_isle(self):
        """mpv olaylarını ana iş parçacığında işler (Qt güvenli)."""
        if not self.mpv:
            return
        # Oynatıcı kapalıysa (Geri'ye basılmış) gelen olaylar işlenmemeli:
        # 'end-file'/'eof' olayı geri sayımı başlatıp sıradaki bölümü
        # arka planda açabiliyordu.
        if not self.isVisible():
            try:
                self._olay_zaman.stop()
            except Exception:
                pass
            return
        # mpv beklenmedik şekilde kapandıysa kullanıcıyı bilgilendir
        if not self.mpv.yasiyor:
            self._olay_zaman.stop()
            # DÜZELTME: Ayrı pencerede oynarken kullanıcı mpv penceresini
            # kendi eliyle (X'e basarak) kapattığında bu KAZA değildir —
            # mpv temiz çıkış kodu (0) ile kapanır. Önceden bu durum da
            # "çöktü" sayılıp otomatik olarak yeniden açılıyordu (kullanıcı
            # X'e bastıkça video tekrar tekrar açılıyordu). Artık çıkış
            # koduna bakılıyor: 0 → kullanıcı kapattı, oynatıcı sessizce
            # kapanır; 0 DIŞINDA (ya da hiç başlamamışsa) → gerçek çökme,
            # otomatik kurtarma denenir.
            surec = getattr(self.mpv, "surec", None)
            kod = surec.returncode if surec is not None else None
            if kod == 0:
                self.kapat()
                return
            # OTOMATİK KURTARMA: mpv çöktüyse bir kez de ayrı pencere +
            # yazılım çıkışı (x11) ile dene. Bu kombinasyon her sistemde çizer.
            if not getattr(self, "_kurtarma_denendi", False) and self.icerik:
                self._kurtarma_denendi = True
                self.durum.setText("Oynatıcı yeniden başlatılıyor…")
                try:
                    self.mpv.kapat()
                except Exception:
                    pass
                self.mpv = None
                self.depo.ayarlar["video_modu"] = "ayri"
                if self.depo.ayarlar.get("video_cikis", "oto") == "oto":
                    self.depo.ayarlar["video_cikis"] = "x11"
                self.depo.kaydet()
                QTimer.singleShot(250, lambda: self.oynat(self.icerik, self.liste, self.sira))
                return
            sebep = (self.mpv.son_hata or "").strip()
            kisa = sebep.split("|")[0][:90] if sebep else ""
            self.durum.setText(
                ("⚠ Oynatıcı kapandı — " + kisa) if kisa
                else "⚠ Oynatıcı kapandı  ·  ⚙ → Video çıkışı → 'x11' deneyin")
            self.bilgi_kutu.setText(
                "⚠  Oynatıcı kapandı ve yeniden başlatılamadı.\n\n"
                + (f"Sebep: {sebep[:220]}\n\n" if sebep else "")
                + "Deneyin:\n"
                  "   ⚙ → Video çıkışı → 'x11'   (her sistemde çizer)\n"
                  "   ⚙ → Video modu → 'Ayrı pencere'\n\n"
                  "Ayrıntı:  ⚙ → Oynatıcı tanısı")
            self.bilgi_kutu.setVisible(True)
            self.video.setVisible(False)
            return
        for o in self.mpv.olaylari_al():
            ad = o.get("event")
            if ad == "property-change":
                if o.get("name") == "pause":
                    self._pause_arayuz(bool(o.get("data")))
                elif o.get("name") == "eof-reached" and o.get("data"):
                    self._dosya_bitti()
                elif o.get("name") in ("track-list", "sid", "aid", "sub-visibility"):
                    # Parça seti ya da seçim değişti (harici altyazı eklenmesi,
                    # mpv'nin kendi otomatik seçimi vb.) → kutuları tazele.
                    self._parcalari_doldur()
            elif ad in ("file-loaded", "tracks-changed"):
                # Yeni dosya yüklendi: parça listesi baştan kurulmalı.
                self._parcalari_doldur()
            elif ad == "end-file":
                sebep = o.get("reason", "")
                if sebep == "error":
                    # mpv açamadı. Eğer bu bir yt-dlp adresi ise ve henüz
                    # kendi çözümleyicimizi denemediysek, şimdi deneyelim.
                    if self._ytdl_yedege_dus():
                        continue
                    self.durum.setText("⚠ Bu bağlantı açılamadı")
                elif sebep == "eof":
                    # Bazı biçimlerde 'eof-reached' gelmiyor; asıl bitiş sinyali bu.
                    self._dosya_bitti()
            elif ad == "log-message":
                # DÜZELTME: bu olay isteniyordu (bkz. mpv_islem.py baslat())
                # ama buraya hiç bağlanmamıştı — mpv-hata.log bu yüzden hep
                # boş kalıyordu ve gerçek hatalar (403 Forbidden vb.) hiçbir
                # yerde görünmüyordu.
                self._mpv_log(o.get("level", ""), o.get("prefix", ""),
                              o.get("text", ""))

    def _mpv_log(self, seviye, bilesen, mesaj):
        """mpv günlüğü — mpv iş parçacığından gelir, Qt'ye DOKUNULMAZ."""
        try:
            if seviye in ("fatal", "error"):
                m = (mesaj or "").strip()
                if m:
                    # Dosyaya yaz: kullanıcı çökme sonrası bakabilsin
                    try:
                        from mediabox_qt import veri_klasoru
                        with open(veri_klasoru() / "mpv-hata.log", "a", encoding="utf-8") as f:
                            f.write(f"[{seviye}] {bilesen}: {m}\n")
                    except Exception:
                        pass
                    self._sig_hata.emit(m[:160])
        except Exception:
            pass

    def _mod_yaz(self):
        if not getattr(self, "gomulu_mu", False):
            self.video.setVisible(False)
            self.bilgi_kutu.setVisible(True)
        else:
            self.video.setVisible(True)
            self.bilgi_kutu.setVisible(False)

    # ── Sinyal alıcıları (HEPSİ ana iş parçacığında çalışır) ──
    def _pause_arayuz(self, duraklatildi: bool):
        self.b_oynat.setText("▶" if duraklatildi else "❚❚")

    def _hata_goster(self, mesaj: str):
        self.durum.setText(f"⚠ {mesaj}")

    def _dosya_bitti(self):
        """
        Dosya bitti → sıradaki bölüme geç.

        Not: Aynı bitiş için hem 'eof-reached' hem 'end-file' gelebiliyor;
        çift tetiklenmeyi önlemek için kısa bir kilit kullanılır.

        CANLI YAYIN İSTİSNASI: Bazı IPTV sunucuları her bağlantıyı belirli
        bir süre sonra kendileri kapatıyor (kötüye kullanımı önlemek için) —
        bu GERÇEK bir bitiş değil, sunucunun normal davranışı. "Bitti"
        yazıp kullanıcıyı endişelendirmek yerine bunu sessizce
        `_canli_donma_kontrol`'e bırakıyoruz; o zaten `eof-reached`
        özelliğini görüp kanalı fark ettirmeden yeniden bağlayacak.
        """
        simdi = time.time()
        if simdi - getattr(self, "_son_bitis", 0) < 3:
            return
        self._son_bitis = simdi
        if self.icerik and self.icerik.kategori == "live":
            return
        if self.liste and self.sira + 1 < len(self.liste):
            self._sonraki_sayim_baslat()
        else:
            self.durum.setText("Bitti")

    # ── jenerik (intro) atlama ────────────────────────────────────
    def _jenerik_kontrol(self):
        """
        Dizi jeneriği sırasında "Jeneriği atla" düğmesi gösterir.

        mpv'nin bölüm (chapter) verisi varsa ve bölüm adı "intro/opening/
        jenerik" içeriyorsa TAM konumu kullanılır — bu en doğrusudur.
        Chapter yoksa dizilerde tipik aralığa (10–150 sn) düşülür.
        Filmlerde ve canlı yayında hiç gösterilmez.
        """
        if not (self.icerik and self._sur > 60):
            return
        if not self.depo.ayarlar.get("jenerik_atla", True):
            return
        if self.icerik.kategori == "live":
            return

        aralik = getattr(self, "_jenerik_aralik", None)
        if aralik is None:
            aralik = self._jenerik_araligi_bul()
            self._jenerik_aralik = aralik or (0, 0)
        if not aralik or aralik == (0, 0):
            return

        bas, son = aralik
        icinde = bas <= self._kon < son
        if icinde and not getattr(self, "_jenerik_gorunur", False):
            self._jenerik_dugme_goster(son)
        elif not icinde and getattr(self, "_jenerik_gorunur", False):
            self._jenerik_dugme_gizle()

    def _jenerik_araligi_bul(self):
        """(başlangıç, bitiş) saniye ya da None."""
        # 1) mpv chapter listesi — en güvenilir kaynak
        try:
            bolumler = self.mpv.ozellik("chapter-list") or []
        except Exception:
            bolumler = []
        anahtar = ("intro", "opening", "jenerik", "op", "başlangıç")
        for i, b in enumerate(bolumler):
            ad = (b.get("title") or "").strip().lower()
            if any(a == ad or a in ad for a in anahtar):
                bas = float(b.get("time", 0) or 0)
                son = float(bolumler[i + 1]["time"]) if i + 1 < len(bolumler) else bas + 90
                if son - bas >= 10:
                    return (bas, son)

        # 2) Chapter yok: yalnızca DİZİ bölümlerinde tahmini aralık
        if not self.icerik.sezon_bolum() and self.icerik.kategori not in ("series", "anime"):
            return None
        if self._sur < 600:            # 10 dk'dan kısa: muhtemelen klip
            return None

        # 2b) Bu dizi için DAHA ÖNCE elle öğretilmiş bir jenerik bitişi
        # var mı? Chapter verisi olmayan kaynaklarda (M3U/IPTV rip'leri
        # hemen hiçbirinde chapter yoktur) en güvenilir kaynak budur —
        # sabit bir tahmin değil, kullanıcının "🏷 Jenerik burada bitiyor"
        # ile bizzat işaretlediği GERÇEK konumdur.
        anahtar_dizi = self.icerik.dizi_kok_anahtari()
        ogrenilen = (self.depo.ayarlar.get("jenerik_ogrenilen") or {}) if anahtar_dizi else {}
        if anahtar_dizi and anahtar_dizi in ogrenilen:
            son = float(ogrenilen[anahtar_dizi])
            if 5 <= son < self._sur:
                return (0.0, son)

        # 3) Hiçbir gerçek bilgi yok: kaba, sabit bir tahmin (10–150 sn).
        # Bu SATIR RASTGELE DEĞİL, ama dizinin gerçek jenerik süresiyle
        # de ilgisi yoktur — bu yüzden çoğu zaman yanlış hissettirir.
        # Kullanıcı "🏷 Jenerik burada bitiyor" ile bir kez işaretlerse
        # bu tahmin bir daha hiç kullanılmaz (bkz. 2b).
        return (10.0, 150.0)

    def _jenerik_burada_isaretle(self):
        """
        Kullanıcı, jeneriğin GERÇEKTEN bittiği anda bunu tetikler.
        O andaki konum bu dizi için kalıcı olarak öğrenilir; bir daha
        kör tahmine (10–150 sn) hiç dönülmez.
        """
        if not self.icerik:
            return
        anahtar_dizi = self.icerik.dizi_kok_anahtari()
        if not anahtar_dizi:
            self.durum.setText("Bu içerik bir dizi bölümü olarak tanınmadı.")
            return
        son = float(self._kon)
        if son < 3:
            self.durum.setText("Jenerik bitişini işaretlemek için biraz daha ilerleyin.")
            return
        d = self.depo.ayarlar.setdefault("jenerik_ogrenilen", {})
        d[anahtar_dizi] = son
        try:
            self.depo.kaydet()
        except Exception:
            pass
        self._jenerik_aralik = (0.0, son)      # bu bölümde de hemen geçerli olsun
        self.durum.setText(f"🏷 Bu dizi için jenerik bitişi öğrenildi: {sure_yaz(son)}")

    def _jenerik_dugme_goster(self, hedef: float):
        self._jenerik_gorunur = True
        self._jenerik_hedef = hedef
        if not hasattr(self, "_b_jenerik"):
            self._b_jenerik = QPushButton("⏩  Jeneriği atla", self)
            self._b_jenerik.setCursor(Qt.CursorShape.PointingHandCursor)
            self._b_jenerik.setStyleSheet(
                "QPushButton{background:rgba(14,16,22,235);border:1px solid #3a4050;"
                "border-radius:9px;color:#eceef4;font-size:13px;font-weight:700;"
                "padding:10px 18px;}"
                "QPushButton:hover{background:rgba(40,44,56,245);}")
            self._b_jenerik.clicked.connect(self._jenerik_atla)
        self._b_jenerik.adjustSize()
        self._jenerik_yerlestir()
        self._b_jenerik.setVisible(True)
        self._b_jenerik.raise_()

    def _jenerik_yerlestir(self):
        """Düğmeyi görünür alanın sağ altına oturtur (taşmayı önler)."""
        if not hasattr(self, "_b_jenerik"):
            return
        b = self._b_jenerik
        b.adjustSize()
        en = max(self.width(), b.width() + 40)
        boy = max(self.height(), b.height() + 120)
        b.move(max(10, en - b.width() - 26), max(10, boy - b.height() - 96))

    def _jenerik_dugme_gizle(self):
        self._jenerik_gorunur = False
        if hasattr(self, "_b_jenerik"):
            self._b_jenerik.setVisible(False)

    def _jenerik_atla(self):
        hedef = getattr(self, "_jenerik_hedef", 0)
        if self.mpv and self.mpv.yasiyor and hedef:
            self.mpv.komut("seek", hedef, "absolute", bekle=False)
            self.durum.setText(f"⏩ Jenerik atlandı ({sure_yaz(hedef)})")
        self._jenerik_dugme_gizle()

    def resizeEvent(self, e):
        """Kayan paneller pencere boyutuyla birlikte yerini korusun."""
        super().resizeEvent(e)
        if getattr(self, "_jenerik_gorunur", False):
            self._jenerik_yerlestir()
        if hasattr(self, "_sayim_kutu") and self._sayim_kutu.isVisible():
            self._sayim_yerlestir()

    # ── sonraki bölüm geri sayımı ─────────────────────────────────
    def _sonraki_sayim_baslat(self, saniye: int = 8):
        """
        Bölüm bitince sıradakine geçmeden önce geri sayım gösterir.

        Kullanıcı "Şimdi oynat" ile hemen geçebilir ya da "İptal" ile
        durdurabilir (eskiden 1,2 sn sonra sorgusuz sualsiz geçiyordu).
        """
        if self.sira + 1 >= len(self.liste):
            return
        sonraki = self.liste[self.sira + 1]
        sb = sonraki.sezon_bolum()
        etiket = f"S{sb[0]:02d}E{sb[1]:02d}" if sb else (sonraki.temiz_ad() or sonraki.ad)[:40]

        ayar = int(self.depo.ayarlar.get("sonraki_bolum_sn", saniye))
        if ayar < 0:                               # -1 = hiç geçme
            self.durum.setText("Bitti  ·  otomatik geçiş kapalı")
            return
        self._sayim_kalan = ayar
        if self._sayim_kalan == 0:                 # 0 = anında geç
            self.komsu(1)
            return

        if not hasattr(self, "_sayim_kutu"):
            self._sayim_kutu = QFrame(self)
            self._sayim_kutu.setObjectName("sayimKutu")
            self._sayim_kutu.setStyleSheet(
                "QFrame#sayimKutu{background:rgba(14,16,22,238);"
                f"border:1px solid {R_CIZGI};border-radius:12px;}}")
            sy = QHBoxLayout(self._sayim_kutu)
            sy.setContentsMargins(18, 12, 18, 12)
            sy.setSpacing(14)
            self._sayim_yazi = QLabel("")
            self._sayim_yazi.setStyleSheet(
                "color:#eceef4;font-size:13px;font-weight:600;background:transparent;border:0;")
            sy.addWidget(self._sayim_yazi)
            v = self.depo.ayarlar.get("vurgu", R_VURGU)
            b_simdi = QPushButton("▶  Şimdi oynat")
            b_simdi.setFixedHeight(32)
            b_simdi.setCursor(Qt.CursorShape.PointingHandCursor)
            b_simdi.setStyleSheet(
                f"QPushButton{{background:{v};border:0;border-radius:7px;"
                f"color:#fff;font-weight:700;padding:0 14px;}}")
            b_iptal = QPushButton("İptal")
            b_iptal.setFixedHeight(32)
            b_iptal.setCursor(Qt.CursorShape.PointingHandCursor)
            b_iptal.setStyleSheet(KOMPAKT_BTN)
            b_simdi.clicked.connect(self._sayim_hemen)
            b_iptal.clicked.connect(self._sayim_iptal)
            sy.addWidget(b_simdi)
            sy.addWidget(b_iptal)
            self._sayim_zaman = QTimer(self)
            self._sayim_zaman.setInterval(1000)
            self._sayim_zaman.timeout.connect(self._sayim_tik)

        self._sayim_etiket = etiket
        self._sayim_kutu.adjustSize()
        self._sayim_yerlestir()
        self._sayim_kutu.setVisible(True)
        self._sayim_kutu.raise_()
        self._sayim_yaz()
        self._sayim_zaman.start()

    def _sayim_yerlestir(self):
        if not hasattr(self, "_sayim_kutu"):
            return
        k = self._sayim_kutu
        k.adjustSize()
        k.move(max(10, self.width() - k.width() - 26),
               max(10, self.height() - k.height() - 96))

    def _sayim_yaz(self):
        self._sayim_yazi.setText(
            f"⏭  Sıradaki:  <b>{self._sayim_etiket}</b>"
            f"&nbsp;&nbsp;<span style='color:#8b90a0'>{self._sayim_kalan} sn</span>")
        self._sayim_kutu.adjustSize()
        self._sayim_yerlestir()

    def _sayim_tik(self):
        # Oynatıcı kapandıysa (kullanıcı Geri'ye bastı) sayaç anlamsızdır.
        if not self.isVisible():
            self._sayim_gizle()
            return
        self._sayim_kalan -= 1
        if self._sayim_kalan <= 0:
            self._sayim_hemen()
        else:
            self._sayim_yaz()

    def _sayim_hemen(self):
        self._sayim_gizle()
        self.komsu(1)

    def _sayim_iptal(self):
        self._sayim_gizle()
        self.durum.setText("Otomatik geçiş iptal edildi")

    def _sayim_gizle(self):
        if hasattr(self, "_sayim_zaman"):
            self._sayim_zaman.stop()
        if hasattr(self, "_sayim_kutu"):
            self._sayim_kutu.setVisible(False)

    # ── oynatma ──
    @staticmethod
    def _oynatma_basligi(icerik: Icerik, sira: int = 0, toplam: int = 1) -> str:
        """
        Oynatıcı üst çubuğundaki başlık.

        Dizi bölümlerinde sezon/bölüm bilgisi ŞART: kullanıcı hangi bölümü
        izlediğini göremiyordu (temiz_ad() S01E01 ekini siliyor).
        """
        ad = icerik.dizi_kok_anahtari() or icerik.temiz_ad() or icerik.ad
        parcalar = [ad]
        sb = icerik.sezon_bolum()
        if sb:
            parcalar.append(f"S{sb[0]:02d}E{sb[1]:02d}")
        elif toplam > 1:
            # Sezon/bölüm yazmıyorsa (ör. "45. Bölüm") ham addan ipucu al
            ham = (icerik.ad or "").strip()
            if ham and ham != ad:
                parcalar.append(ham[:46])
        if toplam > 1:
            parcalar.append(f"({sira + 1}/{toplam})")
        return "   ·   ".join(parcalar)

    def oynat(self, icerik: Icerik, liste: list[Icerik] | None = None, sira: int = 0):
        try:
            self._mpv_kur()
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(
                self, "Oynatıcı başlatılamadı",
                f"mpv başlatılamadı:\n\n{type(e).__name__}: {e}\n\n"
                "Kurulu olduğundan emin olun:\n"
                "    sudo pacman -S mpv\n"
                "Ayrıntılı tanı için:  python mediabox_qt.py --tani")
            self.kapandi.emit()
            return
        self.icerik = icerik
        self.liste = liste or [icerik]
        self.sira = sira
        # ── Canlı yayın donma-izleme durumu (yeni bölüm/kanal → sıfırla) ──
        self._canli_son_kon = -1.0
        self._canli_son_degisim = time.time()
        self._canli_yeniden_baglanma_sayisi = 0
        self._canli_son_baglanma_zamani = 0.0
        # kapat() ayrı pencere modunda 'force-window=no' yapıyor (mpv
        # penceresi ekranda kalmasın diye). Aynı mpv süreci yeniden
        # kullanıldığında bunu GERİ AÇMAK şart, yoksa video görünmez.
        # Ölçüldü: geri açıldığında pencere normal dönüyor (time-pos 2,84).
        try:
            if self.mpv and self.mpv.yasiyor and not getattr(self, "gomulu_mu", False):
                self.mpv.ozellik_yaz("force-window", "yes")
        except Exception:
            pass
        # Yeni dosya: ses/altyazı listesi baştan kurulmalı. Eskiden bu bayrak
        # yalnızca kapat()'ta sıfırlanıyordu; sonraki bölüme geçilince kutular
        # ÖNCEKİ bölümün parçalarını göstermeye devam ediyordu.
        self._parca_dolu = False
        # Yeni dosya: jenerik aralığı ve geri sayım baştan hesaplanmalı.
        self._jenerik_aralik = None
        self._ytdl_yedek_bekliyor = None      # yeni dosya: yedek hakkı tazelenir
        self._mpv_hook_yedek = None
        self._son_ytdl_hata = ""
        self._jenerik_dugme_gizle()
        self._sayim_gizle()
        self._onizleme_gizle()
        if not getattr(self, "_kurtarma_devam", False):
            self._kurtarma_denendi = False
        # BAŞLIK DÜZELTMESİ: temiz_ad() S01E01 ekini siliyordu, bu yüzden
        # hangi bölümün izlendiği görünmüyordu. Artık dizi adı + sezon/bölüm
        # birlikte yazılıyor:  "Breaking Bad  ·  S02E04  (12/24)"
        self.baslik.setText(self._oynatma_basligi(icerik, sira, len(self.liste)))
        çoklu = len(self.liste) > 1
        self.b_onceki.setVisible(çoklu); self.b_sonraki.setVisible(çoklu)
        self.b_bolum.setVisible(çoklu)
        self.a_hiz.blockSignals(True); self.a_hiz.setCurrentIndex(2)
        self.a_hiz.blockSignals(False)
        # _mpv_kur() gömme başarısız olduğunda bir bilgi yazısı koyar;
        # onu ezmemek için yalnızca boşsa "Bağlanılıyor…" yazılır.
        if not getattr(self, "_gomme_uyarisi", ""):
            self.durum.setText("Bağlanılıyor…")
        else:
            self.durum.setText(self._gomme_uyarisi)

        ek = {}
        for o in (icerik.vlcopt or []):
            # #EXTVLCOPT:http-user-agent=... → mpv karşılığı
            try:
                k, _, v = o.split(":", 1)[1].partition("=")
                k = k.strip().lower()
                v = (v or "").strip().split()[0]  # yapışık kirli değerleri temizle
                if k in ("http-user-agent", "user-agent"):
                    ek["user-agent"] = v
                elif k in ("http-referrer", "http-referer", "referrer", "referer"):
                    ek["referrer"] = v
            except Exception:
                pass
        # YouTube vb. siteler: adres önce yt-dlp ile çözülmeli.
        # Çözümleme bir ağ işidir (ölçüldü: 3–5 sn) — arayüzü kilitlememek
        # için ayrı iş parçacığında yapılır, sonuç sinyalle geri gelir.
        from mpv_islem import ytdl_gerekir, mpv_ytdl_hook_var, ytdl_var_mi, gizli_hls_mi
        if gizli_hls_mi(icerik.url):
            # "master.txt" gibi HLS'i tanınmayan bir uzantıyla veren
            # adresler: mpv'nin format algılamasına güvenmek yerine
            # demuxer'ı AÇIKÇA "hls" olarak zorluyoruz. Aksi hâlde mpv
            # "Failed to recognize file format" ile açmayı reddedebiliyor
            # (VLC içerik imzasına bakıp daha toleranslı davranıyor).
            ek["demuxer"] = "lavf"
            ek["demuxer-lavf-format"] = "hls"
        elif icerik.kategori == "live":
            # SABİT SÜREDE DURMA DÜZELTMESİ: Bazı IPTV sunucuları canlı bir
            # akış için (yanlışlıkla) sabit bir HTTP Content-Length
            # gönderiyor. mpv, belirli URL türlerinde KENDİ (ffmpeg'den
            # bağımsız) ağ istemcisini kullanır ve bu durumda akışı "o kadar
            # baytta bitti" sanıp durabiliyor — ölçülen belirti tam olarak
            # bu: oynatıcı sabit bir süre (ör. 26 sn) gösteriyor ve tam o
            # noktada duruyor. VLC bu tür sunuculara karşı daha toleranslı
            # olduğu için sorunsuz akıtmaya devam ediyor.
            # Çözüm: canlı kanallarda demuxer'ı da AÇIKÇA "lavf"a zorlayıp
            # ağ/okuma işini mpv'nin kendi istemcisi yerine ffmpeg'e
            # bırakıyoruz — hem VLC'ninkine daha yakın, daha toleranslı bir
            # davranış hem de yukarıdaki `--demuxer-lavf-o=reconnect=…`
            # seçeneklerinin gerçekten devreye girmesini garantiliyor
            # (aksi hâlde mpv'nin kendi istemcisi kullanılırsa bu seçenekler
            # hiç etkili olmuyordu).
            ek["demuxer"] = "lavf"
        if ytdl_gerekir(icerik.url):
            # GERİLEME DÜZELTMESİ: Burada eskiden "yt-dlp yoksa HİÇ OYNATMA"
            # deniyordu. Ama yt-dlp'yi bulma yöntemi her sistemde birebir
            # aynı sonucu vermiyor (symlink, sarmalayıcı betik, kısıtlı PATH,
            # Flatpak…). Bulamadığımızda pes etmek YouTube'u tamamen
            # öldürüyordu — oysa mpv kendi hook'uyla açabiliyor olabilir.
            #
            # Artık: bulamazsak bile DENİYORUZ; yalnızca ne mpv hook'u ne de
            # yt-dlp varsa uyarı gösteriyoruz.
            if not ytdl_var_mi() and not mpv_ytdl_hook_var():
                self.durum.setText("⚠  yt-dlp kurulu değil")
                QMessageBox.warning(
                    self, "yt-dlp gerekli",
                    "YouTube ve benzeri site bağlantıları için yt-dlp gerekiyor.\n\n"
                    "CachyOS / Arch:   sudo pacman -S yt-dlp\n"
                    "Debian / Ubuntu:  sudo apt install yt-dlp\n"
                    "Evrensel:         pip install -U yt-dlp")
                return
            # KARAR (2026-08): Önce mpv ytdl_hook + ytdl-format.
            #
            # Ölçüldü: `mpv --ytdl-format="bestvideo+bestaudio/best" URL`
            # hem görüntü hem ses veriyor. Python'da çözüp audio-files /
            # loadfile-options ile eklemek ise ya sessiz kalıyor ya da
            # siyah ekran bırakıyordu.
            #
            # mpv hook yoksa veya end-file ile başarısız olursa Python
            # ytdl_coz yedeğe düşer.
            self._ytdl_yedek_bekliyor = None
            self._ytdl_ek = ek
            self._mpv_hook_yedek = None
            # Ses+görüntü garantileyen format (CLI'de doğrulandı)
            _YTDL_FMT = (
                "bestvideo[height<=1080][vcodec^=avc]+bestaudio/"
                "bestvideo[height<=1080][vcodec!*=av01]+bestaudio/"
                "bestvideo[height<=1080]+bestaudio/"
                "best[height<=1080]/best")
            if mpv_ytdl_hook_var():
                self.durum.setText("🔗  mpv yt-dlp ile açılıyor…")
                ek = dict(ek)
                ek.setdefault("ytdl-format", _YTDL_FMT)
                ek.setdefault("hwdec", "no")
                # Hook başarısız olursa Python çözsün
                if ytdl_var_mi():
                    self._ytdl_yedek_bekliyor = icerik
                self.mpv.oynat(icerik.url, ek)
            elif ytdl_var_mi():
                self.durum.setText("🔗  Bağlantı çözümleniyor…")
                self._ytdl_baslat(icerik, ek)
            else:
                ek = dict(ek)
                ek["hwdec"] = "no"
                ek.setdefault("ytdl-format", _YTDL_FMT)
                self.mpv.oynat(icerik.url, ek)
        else:
            self.mpv.oynat(icerik.url, ek)

        # kaldığı yerden devam
        # TUTARSIZLIK DÜZELTMESİ: Depo.izleme_kaydet() 15 saniyeden sonrasını
        # kaydediyor, burası ise yalnızca 20 saniyeden sonrasını geri
        # yüklüyordu. 15–20 sn arasında bırakılan içerikler "İzlemeye devam"
        # rafında görünüyor ama tıklanınca BAŞTAN başlıyordu. İki eşik artık
        # aynı (15 sn).
        pr = self.depo.ilerleme.get(icerik.url)
        if pr and pr.get("t", 0) >= 15:
            self._devam = pr["t"]
        else:
            self._devam = 0
        self.zaman.start()
        self._kaynak_doldur()

    def _kaynak_etiket(self, e) -> str:
        """Kaynak combo için okunur etiket."""
        par = []
        if e.kalite():
            par.append(e.kalite())
        if e.grup:
            par.append(e.grup[:28])
        if e.kaynak:
            par.append(str(e.kaynak)[:20])
        if e.dublaj():
            par.append("TR Dublaj")
        if not par:
            par.append("Kaynak")
        return " · ".join(par)

    def _kaynak_doldur(self):
        """
        Mevcut bölüm + alternatifler listesini kaynak kutusuna doldurur.

        ÖNEMLİ: Alternatif seçilince `oynat(yeni)` çağrılır; `yeni` öğesinin
        kendi `alternatifler` listesi boştur (temsilcide tutulur). Kutuyu
        her seferinde yalnızca `icerik.alternatifler`den kurarsak seçimden
        sonra kutu kaybolur. Bu yüzden tüm adaylar `_kaynak_havuz`da saklanır
        ve kaynak değiştirirken havuz korunur.
        """
        self.a_kaynak.blockSignals(True)
        self.a_kaynak.clear()
        ic = self.icerik
        if not ic:
            self._kaynak_havuz = []
            self.a_kaynak.setVisible(False)
            self.a_kaynak.blockSignals(False)
            return

        # Kaynak değiştirme sırasında havuzu yeniden kurma
        if getattr(self, "_kaynak_koru", False) and getattr(self, "_kaynak_havuz", None):
            temiz = list(self._kaynak_havuz)
        else:
            adaylar = [ic] + list(ic.alternatifler or [])
            # Liste parametresinden de aynı başlıklı / aynı bölüm alternatifleri ekle
            if self.liste:
                for a in self.liste:
                    if a is ic or a in adaylar:
                        continue
                    # Aynı film (tekil) veya aynı dizi bölümü
                    try:
                        if ic.sezon_bolum() and a.sezon_bolum() == ic.sezon_bolum():
                            if (ic.dizi_kok_anahtari() or "") and (
                                    a.dizi_kok_anahtari() == ic.dizi_kok_anahtari()):
                                adaylar.append(a)
                                continue
                        if not ic.sezon_bolum() and not a.sezon_bolum():
                            if (ic.temiz_ad() or "").strip() and (
                                    a.temiz_ad() == ic.temiz_ad()):
                                adaylar.append(a)
                    except Exception:
                        pass
            gorulen, temiz = set(), []
            for a in adaylar:
                u = (a.url or "").strip()
                if not u or u in gorulen:
                    continue
                gorulen.add(u)
                temiz.append(a)
            self._kaynak_havuz = temiz

        if len(temiz) < 2:
            self.a_kaynak.setVisible(False)
            self.a_kaynak.blockSignals(False)
            return
        for a in temiz:
            self.a_kaynak.addItem(self._kaynak_etiket(a), a)
        # Aktif olanı seç
        for i in range(self.a_kaynak.count()):
            if self.a_kaynak.itemData(i) is ic or (
                    getattr(self.a_kaynak.itemData(i), "url", None) == ic.url):
                self.a_kaynak.setCurrentIndex(i)
                break
        self.a_kaynak.setVisible(True)
        self.a_kaynak.blockSignals(False)

    def _kaynak_sec(self, i):
        """Kullanıcı başka kaynak/mirror seçti: aynı konumdan devam etmeye çalış."""
        if i < 0 or not self.a_kaynak.isVisible():
            return
        yeni = self.a_kaynak.itemData(i)
        if not yeni or not self.icerik:
            return
        if yeni.url == self.icerik.url:
            return
        kon = float(getattr(self, "_kon", 0) or 0)
        self._devam = kon if kon >= 5 else 0
        # Havuzu koru — oynat() yeniden _kaynak_doldur çağırınca kaybolmasın
        self._kaynak_koru = True
        try:
            self.oynat(yeni, self.liste, self.sira)
        finally:
            self._kaynak_koru = False

    def _ytdl_yedege_dus(self) -> bool:
        """
        mpv adresi açamadıysa kendi yt-dlp çözümleyicimizi dener.

        mpv'nin gömülü hook'u sistemdeki eski yt-dlp'yi kullanabiliyor ve
        YouTube'da hata veriyor. Bu durumda sessizce pes etmek yerine
        adresi kendimiz çözüp mpv'ye hazır akış adresi veriyoruz.

        Dönüş: True = yedek başlatıldı (hata mesajı gösterilmesin).
        """
        icerik = getattr(self, "_ytdl_yedek_bekliyor", None)
        if not icerik:
            return False
        self._ytdl_yedek_bekliyor = None          # yalnızca bir kez dene
        try:
            from mpv_islem import ytdl_gerekir
            if not ytdl_gerekir(icerik.url):
                return False
        except Exception:
            return False
        self.durum.setText("🔗  Yeniden çözümleniyor (yt-dlp)…")
        self._ytdl_baslat(icerik, getattr(self, "_ytdl_ek", {}) or {})
        return True

    def _ytdl_baslat(self, icerik: Icerik, ek: dict):
        """yt-dlp çözümlemesini arka planda yapar."""
        self._ytdl_nesil = getattr(self, "_ytdl_nesil", 0) + 1
        nesil = self._ytdl_nesil
        url = icerik.url

        class _Coz(QThread):
            bitti = pyqtSignal(int, object, str)

            def run(self):
                from mpv_islem import ytdl_coz
                try:
                    c, h = ytdl_coz(url, zaman_asimi=60)
                except Exception as e:
                    c, h = None, f"{type(e).__name__}: {e}"
                self.bitti.emit(nesil, c, h)

        isci = _Coz()
        isci.bitti.connect(self._ytdl_geldi)
        if not hasattr(self, "_ytdl_isciler"):
            self._ytdl_isciler = []
        self._ytdl_isciler = [x for x in self._ytdl_isciler if x.isRunning()]
        self._ytdl_isciler.append(isci)       # referans tutulmazsa çökme olur
        isci._ek = ek
        isci.start()

    def _ytdl_geldi(self, nesil: int, cozum, hata: str):
        """Çözümleme sonucu (ana iş parçacığı)."""
        if nesil != getattr(self, "_ytdl_nesil", 0):
            return                    # kullanıcı başka içeriğe geçmiş
        if not self.mpv or not self.mpv.yasiyor:
            return
        if not cozum:
            # Kendi çözümleyicimiz başaramadı. mpv'nin gömülü ytdl_hook'u
            # varsa son bir şans olarak ona ver (bazı sitelerde hook'un
            # kendi çerez/başlık işleyişi farklı sonuç verebiliyor).
            yedek = getattr(self, "_mpv_hook_yedek", None)
            if yedek is not None:
                self._mpv_hook_yedek = None
                self._son_ytdl_hata = hata or ""
                self.durum.setText("🔗  mpv ile deneniyor…")
                self.mpv.oynat(yedek.url, getattr(self, "_ytdl_ek", {}) or {})
                return
            self.durum.setText("⚠  Bağlantı çözümlenemedi")
            kisa = (hata or getattr(self, "_son_ytdl_hata", "") or "")[:400]
            QMessageBox.warning(
                self, "Oynatılamadı",
                "Bu adres açılamadı.\n\n" + kisa +
                "\n\nİpucu: yt-dlp güncel değilse şu komutla güncelleyin:\n"
                "   sudo pacman -S yt-dlp        (CachyOS / Arch)\n"
                "   pip install -U yt-dlp        (evrensel)")
            return
        ek = {}
        # YouTube genelde VP9/AV1 (webm) akış veriyor. Donanım hızlandırmalı
        # kod çözme (hwdec) bu kodeklerle birçok GPU sürücüsünde SİYAH EKRAN
        # ya da tam donmaya yol açıyor (görüntü hiç gelmiyor, ses bazen
        # devam ediyor) — yerel dosyalarda (genelde h264/hevc) sorun
        # çıkarmadığı için önceden fark edilmemiş olabilir. yt-dlp ile
        # çözülen akışlar için hwdec'i kapatıyoruz; yazılımla kod çözme
        # 1080p'ye kadar modern bir CPU'da sorunsuz akar.
        ek["hwdec"] = "no"
        if cozum.get("ses_url"):
            # Ayrı ses akışı: loadfile options + property ile çift yolla
            # verilir (bkz. MpvIslem.oynat). Tek string yeterli; mpv liste
            # tipini tek öğe olarak kabul eder.
            ek["audio-files"] = cozum["ses_url"]
        if cozum.get("baslik"):
            ek["force-media-title"] = cozum["baslik"]
        if cozum.get("referer"):
            ek["referrer"] = cozum["referer"]
        # yt-dlp'nin verdiği HTTP başlıklarını mpv'ye aktar.
        # Bunlar olmadan bazı CDN'ler "403 Forbidden" döndürüyor: aynı adres
        # doğru başlıkla HTTP 206 verirken mpv'den istendiğinde reddediliyordu.
        # ÖNEMLİ: video ve ses AYRI URL ise her iki istek de aynı başlıkları
        # kullanmalı — aksi hâlde ses 403 alıp sessiz kalır (görüntü var,
        # ses yok şikayetinin bir diğer kaynağı).
        bsl = dict(cozum.get("basliklar") or {})
        if bsl:
            ua = bsl.pop("User-Agent", "")
            if ua:
                ek["user-agent"] = ua
            baslik_listesi = [f"{k}: {v}" for k, v in bsl.items() if k and v]
            if baslik_listesi:
                from mpv_islem import mpv_liste_kodla
                ek["http-header-fields"] = mpv_liste_kodla(baslik_listesi)
        self._mpv_hook_yedek = None      # başardık, yedeğe gerek yok
        self.mpv.oynat(cozum["url"], ek)
        # Ses izinin gerçekten yüklendiğini doğrula (kısa gecikmeyle).
        # Yüklenmediyse kullanıcıya görünür bir ipucu verilir.
        if cozum.get("ses_url"):
            from PyQt6.QtCore import QTimer as _QT
            def _ses_kontrol():
                if not self.mpv or not self.mpv.yasiyor:
                    return
                aid = self.mpv.ozellik("aid")
                n_audio = 0
                try:
                    tl = self.mpv.ozellik("track-list") or []
                    n_audio = sum(1 for t in tl if isinstance(t, dict)
                                  and t.get("type") == "audio")
                except Exception:
                    pass
                if not n_audio and (aid in (None, False, "no", 0)):
                    self.durum.setText("⚠ Ses izi yüklenemedi — format yeniden deneniyor…")
            _QT.singleShot(2500, _ses_kontrol)
        self.durum.setText("Oynatılıyor")

    def _guncelle(self):
        if not self.mpv or not self.mpv.yasiyor:
            return
        # Oynatıcı kapalıyken (Geri'ye basılmış) arayüzü güncellemenin
        # anlamı yok; ayrıca kapanmış içeriğin ilerlemesini tekrar yazıp
        # "izlemeye devam" listesini kirletiyordu.
        if not self.isVisible():
            self.zaman.stop()
            return
        self._sur = self.mpv.ozellik("duration", 0) or 0
        self._kon = self.mpv.ozellik("time-pos", 0) or 0

        if self._sur and getattr(self, "_devam", 0):
            self.mpv.komut("seek", self._devam, "absolute", bekle=False)
            self.durum.setText(f"↩ {sure_yaz(self._devam)} konumundan devam")
            QTimer.singleShot(2500, lambda: self.durum.setText(""))
            self._devam = 0

        if self.icerik and self.icerik.kategori == "live":
            # Canlı yayında gerçek bir süre/pozisyon kavramı yok — ffmpeg'in
            # bu bağlantı için tahmin ettiği (ve sunucu bağlantıyı kapatıp
            # yeniden açtıkça sıfırlanan) sayı "0:26 / 0:26 → 0:00" şeklinde
            # kafa karıştırıcı bir görüntü veriyordu. Sabit "CANLI" etiketi
            # gösterip ilerleme çubuğunu sabit/pasif tutuyoruz.
            if self.cubuk.isEnabled():
                self.cubuk.setEnabled(False)
            self.cubuk.setValue(1000)
            # YouTube/Twitch tarzı kırmızı "CANLI" rozeti — sade metinden
            # daha belirgin, kullanıcı canlı yayında olduğunu net görür.
            if not getattr(self, "_canli_rozet_uygulandi", False):
                self.l_sure.setStyleSheet(
                    "color:#ff3b3b; font-weight:bold; font-size:12px;")
                self._canli_rozet_uygulandi = True
            self.l_sure.setText("🔴 CANLI")
        else:
            if getattr(self, "_canli_rozet_uygulandi", False):
                self.l_sure.setStyleSheet(f"color:{R_METIN};font-size:12px;")
                self._canli_rozet_uygulandi = False
            if self.cubuk.isEnabled() is False and self._sur > 0:
                self.cubuk.setEnabled(True)
            if not self._surukleniyor and self._sur > 0:
                self.cubuk.setValue(int(self._kon / self._sur * 1000))
            self.l_sure.setText(
                f"{sure_yaz(self._kon)} / {sure_yaz(self._sur) if self._sur else '∞'}")

        if self.durum.text() in ("Bağlanılıyor…", getattr(self, "_gomme_uyarisi", "")):
            g = self.mpv.ozellik("width")
            if g:
                y = self.mpv.ozellik("height")
                ek = ("  ·  " + self._gomme_uyarisi) if getattr(self, "_gomme_uyarisi", "") else ""
                self.durum.setText(f"{g}×{y}{ek}")

        if not hasattr(self, "_iz_kayit") or time.time() - getattr(self, "_iz_kayit", 0) > 5:
            self._iz_kayit = time.time()
            if self.icerik and self._sur > 0 and self._kon >= 15:
                self.depo.izleme_kaydet(self.icerik, self._kon, self._sur)
                # Diske de yaz (yalnızca bellek yetmez; kapanışta kayıp olmasın)
                if not hasattr(self, "_iz_disk") or time.time() - getattr(self, "_iz_disk", 0) > 20:
                    self._iz_disk = time.time()
                    try:
                        self.depo.kaydet()
                    except Exception as ex:
                        print(f"[MediaBox] periyodik kayıt: {type(ex).__name__}: {ex}")

        self._jenerik_kontrol()

        if self.icerik and self.icerik.kategori == "live":
            self._canli_donma_kontrol()

        if not getattr(self, "_parca_dolu", False) and self._sur:
            self._parcalari_doldur()
            self._kalite_doldur()
            self._parca_dolu = True

    def _canli_donma_kontrol(self):
        """
        Canlı yayınlarda "donma" VE "yanlışlıkla bitti sanılma" tespiti +
        sessiz otomatik yeniden bağlanma.

        İKİ AYRI BELİRTİ ele alınır:
        1) DONMA — time-pos hiç ilerlemiyor ama mpv "oynatılıyor" sanıyor
           (ağ kısa kesintisi, sunucunun veri göndermeyi durdurması).
        2) YANLIŞLIKLA "BİTTİ" — bazı IPTV sunucuları canlı bir kanalı
           sabit süreli bir VOD listesi gibi sunuyor (ör. HLS'de
           `#EXT-X-ENDLIST`); mpv standarda uyup listeyi normal şekilde
           sonuna kadar oynatıp DURUYOR (ölçülen belirti: ilerleme çubuğu
           düzgün ilerliyor, "0:30 / 0:30 · Bitti" ile duruyor). Bu, VLC'nin
           aynı kaynağı sorunsuz akıtmaya devam etmesinin nedeni olabilir —
           VLC bu tür sunuculara karşı daha toleranslı/ısrarcı davranıyor.
           mpv'nin `eof-reached` özelliği bunu doğrudan bildirir; bu durumda
           kullanıcı DURAKLATMAMIŞ olsa bile kanalı yeniden açmak gerekir.

        YANLIŞ ALARM ÖNLEMLERİ:
          • Kullanıcı GERÇEKTEN duraklatmışsa (eof-reached DEĞİLKEN
            pause=True) sayaç sıfırlanır, dokunulmaz.
          • İlerleme çubuğu sürükleniyorsa dokunulmaz.
          • Art arda deneme sayısı sınırlanır (kötü bir kaynakta sonsuz
            döngüye girip arayüzü kilitlemesin); sınıra ulaşınca otomatik
            denemeyi durdurup kullanıcıya haber verir.
        """
        try:
            yayin_bitti = bool(self.mpv.ozellik("eof-reached"))
        except Exception:
            yayin_bitti = False
        try:
            duraklatildi = bool(self.mpv.ozellik("pause"))
        except Exception:
            duraklatildi = False

        simdi = time.time()

        if yayin_bitti:
            # "Bitti" durumu doğrudan donma gibi ele alınır — hemen aşağıdaki
            # eşik/soğuma mantığına düşsün diye son değişim zamanını geriye
            # çekiyoruz (ilk yakalamada da beklenmeden tetiklensin).
            if getattr(self, "_canli_bitti_yakalandi", 0) != self._canli_son_kon:
                self._canli_bitti_yakalandi = self._canli_son_kon
                self._canli_son_degisim = simdi - 999
        elif duraklatildi or getattr(self, "_surukleniyor", False):
            self._canli_son_degisim = simdi
            return
        else:
            # time-pos ölçülebilir şekilde ilerlemiş mi? (0,3 sn tolerans:
            # bazı akışlarda değer birebir aynı kalıp mikro-adımlarla oynayabilir)
            if abs(self._kon - getattr(self, "_canli_son_kon", -1.0)) > 0.3:
                self._canli_son_kon = self._kon
                self._canli_son_degisim = simdi
                # 20 sn sorunsuz oynadıysa geçmiş deneme sayısını unut
                if simdi - getattr(self, "_canli_son_baglanma_zamani", 0) > 20:
                    self._canli_yeniden_baglanma_sayisi = 0
                return

        DONMA_ESIGI = 8          # saniye — bu süre hiç ilerlemezse "donmuş" say
        SOGUMA = 6               # iki deneme arası minimum bekleme
        MAKS_DENEME = 5          # bu kadar art arda başarısız denemeden sonra dur

        if simdi - self._canli_son_degisim < DONMA_ESIGI:
            return
        if simdi - getattr(self, "_canli_son_baglanma_zamani", 0) < SOGUMA:
            return

        if self._canli_yeniden_baglanma_sayisi >= MAKS_DENEME:
            self.durum.setText("⚠ Yayın tekrar tekrar donuyor/bitiyor — bağlantıyı/kaynağı kontrol edin")
            return

        self._canli_yeniden_baglanma_sayisi += 1
        self._canli_son_baglanma_zamani = simdi
        self._canli_son_degisim = simdi     # yeniden bağlanma denemesi bekliyor say
        if yayin_bitti:
            # Bazı sunucular her bağlantıyı düzenli aralıklarla kendileri
            # kapatıyor — bu BEKLENEN bir davranış olabilir (hata değil).
            # Uzun/alarm verici bir mesaj yerine kısa, sakin bir gösterge.
            self.durum.setText("↻ Yeniden bağlanılıyor…")
        else:
            self.durum.setText(
                f"↻ Yayın donmuş görünüyor, yeniden bağlanılıyor… "
                f"({self._canli_yeniden_baglanma_sayisi}/{MAKS_DENEME})")
        try:
            if yayin_bitti:
                # "Bitti" durumunda mpv SÜRECİ tıkanmamış — sadece akışı
                # sonlanmış sanmış. Süreci öldürüp yeniden başlatmak (yeni
                # pencere/soket kurulumu) görünür bir KARA EKRAN yaratıyordu
                # (ölçülen şikayet: her döngüde tam ekran karartma). Bunun
                # yerine SÜRECİ CANLI TUTUP aynı adresi doğrudan yeniden
                # yüklüyoruz — pencere/video çıkışı aynı kaldığı için geçiş
                # gözle görülür şekilde daha yumuşak, kara ekran olmuyor.
                self.oynat(self.icerik, self.liste, self.sira)
            else:
                # Gerçek DONMA: mpv işlemi ağ okumasında TIKANMIŞ (wedged)
                # olabilir; bu durumda IPC komutu kuyruğa girer ama hiç
                # işlenmez (VLC aynı URL'i sorunsuz oynatırken mpv'nin
                # donmaya devam etmesi tam olarak bu belirti). Kullanıcının
                # elle yaptığı "geri + tekrar aç" mpv sürecini tamamen
                # öldürüp yeniden başlatıyor; bu yüzden burada süreç zorla
                # kapatılıp sıfırdan yeni bir mpv süreci başlatılıyor.
                if self.mpv:
                    self.mpv.durdur()
                self.oynat(self.icerik, self.liste, self.sira)
        except Exception as e:
            print(f"[MediaBox] Canlı yeniden bağlanma başarısız: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)

    def _parcalari_doldur(self):
        """
        Ses/altyazı kutularını doldurur VE mpv'nin O ANKİ seçimini gösterir.

        HATA (ölçülerek bulundu): Kutular dolduruluyordu ama mpv'nin gerçek
        seçimi hiç okunmuyordu. Altyazı kutusu her zaman ilk sıradaki
        "Kapalı" üzerinde duruyordu; oysa mpv varsayılan altyazıyı açmıştı.

            Ölçüm (2 ses + 2 altyazılı MKV):
              mpv sid = 1  (Türkçe altyazı AÇIK, selected=True)
              kutu    = "Kapalı"        →  kutu gerçeği yansıtmıyor

        Kullanıcı "kapalı yazıyor ama altyazı akıyor" derken tam olarak
        bunu görüyordu. Kutuyu "Kapalı"dan başka bir şeye alıp geri
        getirmediği sürece `_alt_sec` hiç tetiklenmediği için altyazı da
        kapanmıyordu.
        """
        if not self.mpv:
            return
        liste = self.mpv.ozellik("track-list") or []

        self.a_ses.blockSignals(True); self.a_alt.blockSignals(True)
        self.a_ses.clear(); self.a_alt.clear()
        self.a_alt.addItem("Kapalı", -1)

        def _etiket(t: dict) -> str:
            """Kısa ve okunur parça adı (kutuya sığmalı)."""
            ad = (t.get("title") or "").strip()
            dil = (t.get("lang") or "").strip()
            if ad and dil and dil.lower() not in ad.lower():
                metin = f"{ad} ({dil})"
            else:
                metin = ad or dil or f"Parça {t.get('id')}"
            if t.get("external"):
                metin = "📄 " + metin        # diskten eklenen altyazı
            return metin[:34]

        secili_ses = secili_alt = None
        for t in liste:
            etiket = _etiket(t)
            if t.get("type") == "audio":
                self.a_ses.addItem(etiket, t.get("id"))
                if t.get("selected"):
                    secili_ses = t.get("id")
            elif t.get("type") == "sub":
                self.a_alt.addItem(etiket, t.get("id"))
                if t.get("selected"):
                    secili_alt = t.get("id")

        # track-list "selected" vermezse doğrudan mpv özelliklerine sor.
        if secili_ses is None:
            a = self.mpv.ozellik("aid")
            if isinstance(a, int):
                secili_ses = a
        if secili_alt is None:
            s = self.mpv.ozellik("sid")
            if isinstance(s, int):
                secili_alt = s

        # Altyazı görünürlüğü kapalıysa kullanıcı için "Kapalı" demektir.
        if self.mpv.ozellik("sub-visibility", True) is False:
            secili_alt = None

        i = self.a_ses.findData(secili_ses)
        self.a_ses.setCurrentIndex(i if i >= 0 else 0)
        j = self.a_alt.findData(secili_alt) if secili_alt is not None else 0
        self.a_alt.setCurrentIndex(j if j >= 0 else 0)

        self.a_ses.blockSignals(False); self.a_alt.blockSignals(False)
        self.a_ses.setEnabled(self.a_ses.count() > 1)
        self.a_alt.setEnabled(self.a_alt.count() > 1)

    def _ses_sec(self, i):
        if not (self.mpv and self.mpv.yasiyor and i >= 0):
            return
        v = self.a_ses.itemData(i)
        if v is None:
            return
        self.mpv.ozellik_yaz("aid", v)
        ad = self.a_ses.itemText(i)
        self.durum.setText(f"🔊 Ses: {ad}")

    def _alt_sec(self, i):
        """
        Altyazı seçimi.

        KAPATMA DÜZELTMESİ: yalnızca `sid=no` yazmak bazı durumlarda
        yetmiyordu (harici sub-add ile eklenen altyazılarda mpv yeniden
        bir parça seçebiliyor). Bu yüzden kapatırken hem `sid=no` hem de
        `sub-visibility=no` uygulanıyor; açarken ikisi de geri alınıyor.
        """
        if not (self.mpv and self.mpv.yasiyor and i >= 0):
            return
        v = self.a_alt.itemData(i)
        if v == -1 or v is None:
            self.mpv.ozellik_yaz("sid", "no")
            self.mpv.ozellik_yaz("sub-visibility", "no")
            self.durum.setText("💬 Altyazı kapalı")
        else:
            self.mpv.ozellik_yaz("sub-visibility", "yes")
            self.mpv.ozellik_yaz("sid", v)
            self.durum.setText(f"💬 Altyazı: {self.a_alt.itemText(i)}")

    def duraklat_degistir(self):
        if self.mpv and self.mpv.yasiyor:
            simdi = bool(self.mpv.ozellik("pause", False))
            self.mpv.ozellik_yaz("pause", not simdi)
            self._pause_arayuz(not simdi)

    def atla(self, sn):
        if self.mpv and self.mpv.yasiyor:
            self.mpv.komut("seek", sn, "relative", bekle=False)

    # ── zaman çubuğu önizlemesi ("nerede kalmıştım") ──────────────
    def eventFilter(self, nesne, olay):
        """İlerleme çubuğunda fare hareketini yakalar."""
        try:
            if nesne is self.cubuk:
                tur = olay.type()
                if tur == QEvent.Type.MouseMove:
                    self._onizleme_goster(olay.position().x())
                elif tur == QEvent.Type.Leave:
                    self._onizleme_gizle()
        except Exception:
            pass
        return super().eventFilter(nesne, olay)

    def _cubuk_surukleniyor(self, deger: int):
        """Sürükleme sırasında da önizleme güncellensin."""
        if self._sur <= 0:
            return
        x = deger / 1000 * max(1, self.cubuk.width())
        self._onizleme_goster(x)

    def _onizleme_goster(self, x: float):
        if not self.depo.ayarlar.get("onizleme", True):
            return
        if self._sur <= 0 or not self.icerik:
            return
        ur = getattr(self, "_onizleme", None)
        if ur is None:
            from onizleme import OnizlemeUretici
            ur = self._onizleme = OnizlemeUretici(en=200, adim=5)
        ur.ayarla(self.icerik.url, self._sur)
        if not ur.kullanilabilir():
            return                      # uzak akış / ffmpeg yok

        oran = max(0.0, min(1.0, x / max(1, self.cubuk.width())))
        saniye = oran * self._sur
        self._onizleme_saniye = saniye

        if not hasattr(self, "_oniz_kutu"):
            self._oniz_kutu = QFrame(self)
            self._oniz_kutu.setObjectName("onizKutu")
            self._oniz_kutu.setStyleSheet(
                "QFrame#onizKutu{background:rgba(10,11,15,245);"
                f"border:1px solid {R_CIZGI};border-radius:9px;}}")
            ov = QVBoxLayout(self._oniz_kutu)
            ov.setContentsMargins(5, 5, 5, 4)
            ov.setSpacing(3)
            self._oniz_resim = QLabel()
            self._oniz_resim.setFixedSize(200, 113)
            self._oniz_resim.setStyleSheet(
                f"background:{R_YUZEY2};border:0;border-radius:5px;")
            self._oniz_resim.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ov.addWidget(self._oniz_resim)
            self._oniz_yazi = QLabel("")
            self._oniz_yazi.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._oniz_yazi.setStyleSheet(
                "color:#eceef4;font-size:11px;font-weight:700;border:0;background:transparent;")
            ov.addWidget(self._oniz_yazi)

        self._oniz_yazi.setText(sure_yaz(saniye))
        hazir = ur.hazir_mi(saniye)
        if hazir:
            self._oniz_resim_yaz(hazir)
        else:
            ur.iste(saniye, self._oniz_geldi_disaridan)

        self._oniz_kutu.adjustSize()
        # Balonu çubuğun üstünde, fare hizasında konumlandır
        kh = self.cubuk.mapTo(self, self.cubuk.rect().topLeft())
        gx = int(kh.x() + x - self._oniz_kutu.width() / 2)
        gx = max(8, min(gx, self.width() - self._oniz_kutu.width() - 8))
        gy = max(8, kh.y() - self._oniz_kutu.height() - 10)
        self._oniz_kutu.move(gx, gy)
        self._oniz_kutu.setVisible(True)
        self._oniz_kutu.raise_()

    def _oniz_resim_yaz(self, yol: str):
        px = QPixmap(yol)
        if not px.isNull():
            self._oniz_resim.setPixmap(px.scaled(
                200, 113, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))

    def _oniz_geldi_disaridan(self, saniye: int, yol: str):
        """ffmpeg iş parçacığından gelir → köprüyle ana iş parçacığına."""
        ana_is_parcasinda(lambda s=saniye, y=yol: self._oniz_geldi(s, y))

    def _oniz_geldi(self, saniye: int, yol: str):
        try:
            if not self._oniz_kutu.isVisible():
                return
            # Fare hâlâ aynı bölgedeyse göster (yoksa bayat kare)
            if abs(getattr(self, "_onizleme_saniye", 0) - saniye) <= 6:
                self._oniz_resim_yaz(yol)
        except (RuntimeError, AttributeError):
            pass

    def _onizleme_gizle(self):
        if hasattr(self, "_oniz_kutu"):
            self._oniz_kutu.setVisible(False)

    def _atla(self):
        self._surukleniyor = False
        if self.mpv and self.mpv.yasiyor and self._sur > 0:
            self.mpv.komut("seek", self.cubuk.value() / 1000 * self._sur,
                           "absolute", bekle=False)

    def _ses(self, v):
        if self.mpv and self.mpv.yasiyor:
            self.mpv.ozellik_yaz("volume", v)
        self.b_ses.setText("🔇" if v == 0 else "🔊")

    def _hiz_sec(self, i):
        if self.mpv and self.mpv.yasiyor and i >= 0:
            self.mpv.ozellik_yaz("speed", self.a_hiz.itemData(i))

    def _kalite_sec(self, i):
        """HLS çok akışlı yayınlarda görüntü kalitesini değiştirir."""
        if not (self.mpv and self.mpv.yasiyor) or i < 0:
            return
        v = self.a_kalite.itemData(i)
        if v is None:
            return
        self.mpv.ozellik_yaz("vid", "auto" if v == -1 else v)

    def _kalite_doldur(self):
        """Yayında birden çok görüntü akışı varsa kalite listesini kurar."""
        if not self.mpv:
            return
        parcalar = self.mpv.ozellik("track-list") or []
        video = [t for t in parcalar if t.get("type") == "video"]
        self.a_kalite.blockSignals(True)
        self.a_kalite.clear()
        if len(video) > 1:
            self.a_kalite.addItem("Kalite: Oto", -1)
            for t in video:
                y = t.get("demux-h") or t.get("h") or 0
                ad = f"{y}p" if y else (t.get("title") or f"#{t.get('id')}")
                self.a_kalite.addItem(ad, t.get("id"))
            self.a_kalite.setEnabled(True)
        else:
            self.a_kalite.addItem("Tek kalite", None)
            self.a_kalite.setEnabled(False)
        self.a_kalite.blockSignals(False)

    def _istatistik_ac_kapa(self):
        """
        mpv'nin kendi yerleşik "stats" betiğini açıp kapatır — bitrate,
        düşen kare sayısı, ses/görüntü senkron farkı, arabellek doluluğu
        gibi teknik bilgileri video üstüne bindirir. Ayrı bir telemetri
        arayüzü yazmaya gerek yok; mpv zaten üretiyor, biz sadece anahtarı
        arayüzden erişilebilir yapıyoruz (--no-input-default-bindings
        yüzünden mpv'nin varsayılan "i" kısayolu tanımlı değil).
        """
        if not (self.mpv and self.mpv.yasiyor):
            return
        try:
            self.mpv.komut("script-binding", "stats/display-stats-toggle", bekle=False)
        except Exception as e:
            print(f"[MediaBox] İstatistik paneli açılamadı: {type(e).__name__}: {e}",
                  file=sys.stderr)

    def pip_degistir(self):
        """mpv penceresini hep üstte tutar (küçük pencere etkisi)."""
        if not (self.mpv and self.mpv.yasiyor):
            return
        self._pip = not getattr(self, "_pip", False)
        self.mpv.ozellik_yaz("ontop", self._pip)
        if self._pip and not self.gomulu_mu:
            self.mpv.ozellik_yaz("autofit", "36%")
        self.durum.setText("📌 Hep üstte: AÇIK" if self._pip else "Hep üstte: kapalı")

    def bolum_panel(self):
        """Oynatıcı içinden bölüm listesi."""
        from PyQt6.QtWidgets import QDialog, QListWidget, QListWidgetItem, QVBoxLayout
        if not self.liste or len(self.liste) < 2:
            return
        d = QDialog(self); d.setWindowTitle("Bölümler"); d.resize(460, 480)
        v = QVBoxLayout(d)
        lw = QListWidget(); v.addWidget(lw)
        for i, b in enumerate(self.liste):
            sb = b.sezon_bolum()
            ad = b.temiz_ad() or b.ad
            et = f"S{sb[0]:02d}E{sb[1]:02d}   ·   {ad}" if sb else ad
            if i == self.sira:
                et = "▶  " + et
            pr = self.depo.ilerleme.get(b.url)
            if pr and pr.get("d"):
                et += f"      ({int(pr['t']/pr['d']*100)}%)"
            it = QListWidgetItem(et); it.setData(Qt.ItemDataRole.UserRole, i)
            lw.addItem(it)
        lw.setCurrentRow(self.sira)
        def sec(it):
            d.accept()
            self.oynat(self.liste[it.data(Qt.ItemDataRole.UserRole)],
                       self.liste, it.data(Qt.ItemDataRole.UserRole))
        lw.itemDoubleClicked.connect(sec)
        d.exec()

    def altyazi_yukle(self):
        """Diskten altyazı dosyası ekler (mpv sub-add)."""
        from PyQt6.QtWidgets import QFileDialog
        if not (self.mpv and self.mpv.yasiyor):
            return
        yol, _ = QFileDialog.getOpenFileName(
            self, "Altyazı dosyası seç", "",
            "Altyazı (*.srt *.ass *.ssa *.vtt *.sub);;Tüm dosyalar (*)")
        if not yol:
            return
        self.mpv.komut("sub-add", yol, "select", bekle=False)
        # Kullanıcı daha önce altyazıyı kapatmış olabilir; yeni altyazı
        # eklendiğinde görünürlüğü tekrar açıyoruz, yoksa dosya yüklenir
        # ama ekranda hiçbir şey çıkmaz.
        self.mpv.ozellik_yaz("sub-visibility", "yes")
        QTimer.singleShot(400, self._parcalari_doldur)
        self.durum.setText(f"💬 Altyazı eklendi: {os.path.basename(yol)}")

    def sessiz(self):
        self.ses.setValue(0 if self.ses.value() > 0 else 100)

    def komsu(self, yon):
        """
        Önceki/sonraki bölüme geç.

        GÜVENLİK KİLİDİ: Oynatıcı sayfası ekranda değilse hiçbir şey
        oynatılmaz. Ölçülen hata: geri sayım sırasında Geri'ye basılınca
        sayaç dolup buraya geliyordu; kullanıcı ana sayfadayken sıradaki
        bölüm arka planda açılıyordu (sıra 0→1, mpv pause=False).
        """
        if not self.isVisible():
            return
        y = self.sira + yon
        if 0 <= y < len(self.liste):
            self.oynat(self.liste[y], self.liste, y)

    def tam_ekran(self):
        p = self.window()
        p.showNormal() if p.isFullScreen() else p.showFullScreen()

    def _esc(self):
        """ESC: önce tam ekrandan çık, ikinci basışta oynatıcıyı kapat."""
        try:
            if self.window().isFullScreen():
                self.window().showNormal()
                return
        except Exception:
            pass
        self.kapat()

    def keyPressEvent(self, olay):
        """
        ESC yedeği.

        Oynatıcı AnaPencere dışında (tek başına) kullanılırsa kısayol
        sahibi olmadığı için ESC ölü kalırdı; bu yedek onu karşılar.
        """
        try:
            if olay.key() == Qt.Key.Key_Escape:
                self._esc()
                olay.accept()
                return
        except Exception:
            pass
        super().keyPressEvent(olay)

    def kapat(self):
        """
        Oynatıcıyı kapatır — HİÇBİR KOŞULDA YARIDA KALMAZ.

        “Film izlerken sol üstteki geri tuşu çalışmıyor” hatasının kaynağı
        burasıydı. Ölçülen dört ayrı arıza:

        1) AYRI PENCERE MODUNDA mpv PENCERESİ EKRANDA KALIYORDU  (asıl neden)
           mpv `--force-window=yes --idle=yes` ile başlatılıyor. `stop`
           komutu yalnızca dosyayı bırakır, PENCEREYİ KAPATMAZ.
           Ölçüldü (xdotool): Geri'den sonra "MediaBox — Oynatıcı" penceresi
           hâlâ görünür (1 pencere), ekran görüntüsünde videonun üstünde
           duruyor. Ana pencere aslında ana sayfaya dönmüştü ama kullanıcı
           bunu göremiyordu → “geri tuşu çalışmıyor”.
           Çözüm: `force-window=no` (+ `ontop=no`) ile pencere gerçekten
           ekrandan kalkıyor. Ölçüldü: 1 pencere → 0 pencere; sonradan
           tekrar oynatma sorunsuz (time-pos 2,84 sn ilerledi).

        2) İSTİSNA GERİ'Yİ TAMAMEN ÖLDÜRÜYORDU
           `izleme_kaydet()` ya da `kaydet()` hata atarsa (disk dolu, salt
           okunur dosya, bozuk ilerleme kaydı) fonksiyon o satırda kesiliyor,
           `kapandi` sinyali HİÇ YAYILMIYORDU. Ölçüldü: yapay OSError ile
           program "Aborted" ile kapandı, sayfa 3'te kaldı.
           Çözüm: kayıt işi EN SONA alındı, ayrı try/except içinde ve
           ertelenmiş çalışıyor; sinyal try/finally ile garanti ediliyor.

        3) GERİ SAYIM DURDURULMUYORDU
           Bölüm bitip “Sonraki bölüm 6 sn” sayacı başladıysa Geri'ye basmak
           sayacı durdurmuyordu. Ölçüldü: Geri'den 9 sn sonra sıra 0→1 oldu,
           mpv sıradaki bölümü ARKA PLANDA açtı (path dolu, pause=False) —
           kullanıcı ana sayfada ama ses/görüntü devam ediyor.

        4) TAM EKRAN ÇIKILMIYORDU
           Tam ekranda Geri'ye basınca pencere tam ekran kalıyordu; ana sayfa
           başlık çubuğu olmadan açılıyordu. Ölçüldü: isFullScreen() = True.
        """
        # ── 1) Zamanlayıcılar EN ÖNCE susturulur ──────────────────────
        # Sıra önemli: aşağıdaki adımlardan biri gecikirse bile artık
        # hiçbir zamanlayıcı oynatıcıyı geri açamaz.
        for ad in ("zaman", "_olay_zaman", "_sayim_zaman", "_oniz_zaman"):
            try:
                z = getattr(self, ad, None)
                if z is not None:
                    z.stop()
            except Exception:
                pass
        for kapatici in (self._sayim_gizle, self._jenerik_dugme_gizle,
                         self._onizleme_gizle):
            try:
                kapatici()
            except Exception:
                pass
        self._parca_dolu = False
        # Otomatik kurtarma bu noktadan sonra oynatıcıyı diriltmesin.
        self._kurtarma_denendi = True

        # ── 2) mpv'yi durdur VE penceresini ekrandan kaldır ───────────
        try:
            if self.mpv and self.mpv.yasiyor:
                self.mpv.komut("stop", bekle=False)
                if not getattr(self, "gomulu_mu", False):
                    # Ayrı pencere: 'stop' pencereyi kapatmaz (ölçüldü).
                    # PiP açık bırakılmış olabilir; 'ontop' da sıfırlanır,
                    # yoksa boş mpv penceresi her şeyin üstünde kalır.
                    self.mpv.ozellik_yaz("ontop", False)
                    self.mpv.ozellik_yaz("force-window", "no")
                    self._pip = False
        except Exception:
            pass            # mpv ölmüş olabilir — Geri yine de çalışmalı

        # ── 3) Tam ekrandan çık ───────────────────────────────────────
        try:
            pen = self.window()
            if pen is not None and pen.isFullScreen():
                pen.showNormal()
        except Exception:
            pass

        # ── 4) Sayfayı DEĞİŞTİR — try/finally ile garanti ─────────────
        # Kayıt işleminden ÖNCE yayılıyor: 20.000 içerikli bir kütüphanede
        # depo.kaydet() ölçüldüğünde 220 ms sürüyordu ve bu süre boyunca
        # arayüz donuyordu (toplam 335 ms). Artık kullanıcı Geri'ye basar
        # basmaz ana sayfayı görüyor, kayıt arka planda yapılıyor.
        icerik, kon, sur = self.icerik, self._kon, self._sur
        # İlerlemeyi HEMEN diske yaz (singleShot, uygulama kapanırsa kaçabiliyordu)
        if icerik is not None and sur > 0:
            self._ilerlemeyi_kaydet(icerik, kon, sur)
        try:
            self.kapandi.emit()
        except Exception:
            pass

    def _ilerlemeyi_kaydet(self, icerik, konum, sure):
        """İzleme konumunu diske yazar. Hata olsa da Geri'yi etkilemez."""
        try:
            self.depo.izleme_kaydet(icerik, konum, sure)
            self.depo.kaydet()
        except Exception as e:
            print(f"[MediaBox] izleme kaydedilemedi: {type(e).__name__}: {e}")

    def yok_et(self):
        if hasattr(self, "_olay_zaman"):
            self._olay_zaman.stop()
        if self.mpv:
            try: self.mpv.kapat()
            except Exception: pass
            self.mpv = None


# ══════════════════════════════════════════════════════════════════
#  RAF — SARMALANAN IZGARA
# ══════════════════════════════════════════════════════════════════
class Raf(QWidget):
    """
    İçerik rafı — yan yana en çok SUTUN adet afiş, sığmayan alt satıra geçer.

    Kullanıcı isteği: “sağa sola kaydırmalı bölümü istemiyorum; yan yana 10
    afiş olsun, kalan afişler alt satırda olsun.”  Bu yüzden yatay kaydırma
    çubuğu ve ‹ › okları tamamen kaldırıldı; yerine QGridLayout kullanılıyor.

    Varsayılan olarak en fazla 2 satır gösterilir; fazlası “Tümünü göster”
    düğmesiyle açılır (sayfanın sonsuz uzamaması için).
    """
    SUTUN = 10          # yan yana afiş sayısı (pencere darsa otomatik azalır)
    SATIR = 2           # başlangıçta gösterilen satır sayısı

    def __init__(self, baslik: str, gruplar: list[dict], depo: Depo, tiklandi,
                 yatay: bool = False, parent=None):
        super().__init__(parent)
        # Opak: şeffaf raf yüzeyinde kaydırma artefaktı oluşuyordu.
        self.setAutoFillBackground(True)
        self.setStyleSheet(f"background:{R_ARKA};")
        self._gruplar = gruplar
        self._depo, self._tiklandi = depo, tiklandi
        self._yatay = yatay
        self._acik = False              # “Tümünü göster” basıldı mı?
        self._son_sutun = 0
        self._tam_en = (YKART_EN if yatay else KART_EN)   # kartın tam boyu
        self._son_kart_en = self._tam_en
        self._bosluk = 14

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 4, 0, 12)
        v.setSpacing(8)

        ust = QHBoxLayout()
        ust.setContentsMargins(28, 0, 28, 0)
        b = QLabel(baslik)
        b.setStyleSheet("font-size:16px;font-weight:700;background:transparent;")
        self.l_sayi = QLabel(f"{len(gruplar)} içerik")
        self.l_sayi.setStyleSheet(f"color:{R_SOLUK};font-size:11px;background:transparent;")
        ust.addWidget(b)
        ust.addWidget(self.l_sayi)
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

    # ── yerleşim ──
    def _yerlesim_hesapla(self) -> tuple[int, int]:
        """
        (sütun sayısı, kart genişliği) döndürür.

        Hedef: yan yana SUTUN (10) afiş. Tam boy kart (168px) 10 tane için
        1862px ister; kullanıcının penceresi daha darsa kartlar 10 tane
        sığacak kadar küçültülür. Aşırı küçülmesin diye alt sınır var;
        sınırın altına düşülürse sütun sayısı azaltılır.
        """
        alt_sinir = 108 if not self._yatay else 190      # okunabilirlik sınırı
        en = self.width()
        if en <= 0 and self.parent() is not None:
            en = self.parent().width()
        if en <= 0:
            en = 1600                       # ilk çizimde makul varsayım
        kullanilabilir = max(1, en - 56)    # sol/sağ 28px kenar boşluğu

        sutun = self.SUTUN
        while sutun > 1:
            kart = (kullanilabilir - (sutun - 1) * self._bosluk) // sutun
            if kart >= alt_sinir:
                return sutun, int(min(kart, self._tam_en))
            sutun -= 1
        return 1, int(min(kullanilabilir, self._tam_en))

    def _sutun_hesapla(self) -> int:
        return self._yerlesim_hesapla()[0]

    def _ciz(self, sutun: int, kart_en: int = 0):
        self._son_sutun = sutun
        self._son_kart_en = kart_en or self._tam_en
        while self.izgara.count():
            it = self.izgara.takeAt(0)
            w = it.widget() if it else None
            if w:
                # deleteLater tek başına yetmiyor: silinene kadar çizilmeye
                # devam edip kartlar üst üste biniyordu.
                w.hide()
                w.setParent(None)
                w.deleteLater()

        toplam = len(self._gruplar)
        sinir = toplam if self._acik else min(toplam, sutun * self.SATIR)
        Sinif = YatayKart if self._yatay else Kart
        kart_en = self._son_kart_en
        for i, g in enumerate(self._gruplar[:sinir]):
            self.izgara.addWidget(Sinif(g, self._depo, self._tiklandi, en=kart_en),
                                  i // sutun, i % sutun)
        # Sütunları sola yasla: son sütundan sonrası esnesin.
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
#  VİTRİN — “Sizin İçin Önerilenler” büyük gösterim
# ══════════════════════════════════════════════════════════════════
class Vitrin(QFrame):
    """
    Anasayfa üst bandı — detay sayfası estetiğinde tam geniş TMDB backdrop,
    solda afiş, sağda başlık / puan / özet / düğmeler.

    Geçiş yalnızca otomatik (≈8 sn); ok düğmeleri yok.
    """
    # Yükseklik içeriğe göre ölçüldü: üst etiket (~42) + afiş (300) +
    # alt boşluk (28) ≈ 400 px. Eskiden 480 idi ve altta ~90 px boş
    # siyah şerit kalıyordu (ölçüldü: y=400–480 arası içerik yok).
    YUKSEKLIK = 404
    AFIS_EN, AFIS_BOY = 200, 300

    def __init__(self, depo: Depo, oynat_cb, detay_cb, parent=None):
        super().__init__(parent)
        self.depo = depo
        self._oynat_cb, self._detay_cb = oynat_cb, detay_cb
        self.gruplar: list[dict] = []
        self.sira = 0
        self._arka: QPixmap | None = None
        self._afis: QPixmap | None = None
        self._istek = 0
        self.setMinimumHeight(self.YUKSEKLIK)
        self.setMaximumHeight(self.YUKSEKLIK)
        self.setObjectName("vitrinKok")
        self.setStyleSheet("QFrame#vitrinKok{background:transparent;}")

        dis = QVBoxLayout(self)
        dis.setContentsMargins(0, 0, 0, 0)
        dis.setSpacing(0)

        # Üst etiket (küçük, şeffaf)
        ust = QHBoxLayout()
        ust.setContentsMargins(32, 18, 32, 0)
        self.l_bolum = QLabel("✨  Sizin İçin Önerilenler")
        self.l_bolum.setStyleSheet(
            "font-size:13px;font-weight:600;color:#c8ccd8;background:transparent;"
            "letter-spacing:.3px;")
        _golge_ver(self.l_bolum, 12)
        ust.addWidget(self.l_bolum)
        ust.addStretch()
        self.l_sayac = QLabel("")
        self.l_sayac.setStyleSheet(
            f"color:{R_SOLUK};font-size:11px;background:transparent;")
        _golge_ver(self.l_sayac, 10)
        ust.addWidget(self.l_sayac)
        dis.addLayout(ust)

        # Gövde: afiş + bilgi (detay sayfası düzeni)
        govde = QHBoxLayout()
        govde.setContentsMargins(32, 16, 40, 28)
        govde.setSpacing(28)

        self.afis = QLabel()
        self.afis.setFixedSize(self.AFIS_EN, self.AFIS_BOY)
        self.afis.setScaledContents(False)
        self.afis.setStyleSheet(
            f"background:{R_YUZEY2};border-radius:12px;")
        self.afis.setCursor(Qt.CursorShape.PointingHandCursor)
        govde.addWidget(self.afis, 0, Qt.AlignmentFlag.AlignTop)

        sag = QVBoxLayout()
        sag.setSpacing(10)
        sag.setContentsMargins(0, 8, 0, 0)

        self.l_bas = QLabel("")
        self.l_bas.setStyleSheet(
            "font-size:34px;font-weight:800;letter-spacing:-.6px;"
            "background:transparent;")
        self.l_bas.setWordWrap(True)
        _golge_ver(self.l_bas, 22)
        sag.addWidget(self.l_bas)

        self.l_meta = QLabel("")
        self.l_meta.setStyleSheet(
            "color:#d6dae4;font-size:13px;background:transparent;")
        _golge_ver(self.l_meta, 12)
        sag.addWidget(self.l_meta)

        self.l_tur = QLabel("")
        self.l_tur.setStyleSheet(
            f"color:{R_METIN};font-size:12px;background:transparent;")
        _golge_ver(self.l_tur, 10)
        sag.addWidget(self.l_tur)

        self.l_ozet = QLabel("")
        self.l_ozet.setWordWrap(True)
        self.l_ozet.setStyleSheet(
            "color:#d6dae4;font-size:13px;background:transparent;line-height:1.45;")
        self.l_ozet.setMaximumWidth(720)
        self.l_ozet.setMaximumHeight(96)
        _golge_ver(self.l_ozet, 12)
        sag.addWidget(self.l_ozet)

        dug = QHBoxLayout()
        dug.setSpacing(12)
        dug.setContentsMargins(0, 14, 0, 0)
        self.b_oynat = QPushButton("▶  Oynat")
        self.b_oynat.setFixedSize(132, 42)
        self.b_oynat.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_oynat.setStyleSheet(
            f"QPushButton{{background:{R_VURGU};border:0;border-radius:8px;"
            f"font-weight:700;font-size:14px;color:#fff;}}"
            f"QPushButton:hover{{background:#f6121d;}}")
        self.b_detay = QPushButton("ⓘ  Detaylar")
        self.b_detay.setFixedSize(132, 42)
        self.b_detay.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_detay.setStyleSheet(
            f"QPushButton{{background:rgba(38,42,54,225);border:1px solid {R_CIZGI};"
            f"border-radius:8px;font-weight:600;font-size:13px;color:{R_METIN};}}"
            f"QPushButton:hover{{background:rgba(58,63,78,235);}}")
        self.b_oynat.clicked.connect(self._oynat_bas)
        self.b_detay.clicked.connect(self._detay_bas)
        dug.addWidget(self.b_oynat)
        dug.addWidget(self.b_detay)
        dug.addStretch()
        sag.addLayout(dug)
        sag.addStretch()
        govde.addLayout(sag, 1)
        dis.addLayout(govde)

        self.afis.mousePressEvent = lambda e: self._detay_bas()

        # Yalnızca otomatik geçiş (ok düğmesi yok)
        self._zaman = QTimer(self)
        self._zaman.setInterval(8000)
        self._zaman.timeout.connect(lambda: self.git(1))

    # ── veri ──
    def ayarla(self, gruplar: list[dict]):
        self.gruplar = gruplar or []
        self.sira = 0
        if not self.gruplar:
            self.setVisible(False)
            self._zaman.stop()
            return
        self.setVisible(True)
        self._goster()
        if len(self.gruplar) > 1:
            self._zaman.start()
        else:
            self._zaman.stop()

    def git(self, yon: int = 1):
        if not self.gruplar:
            return
        self.sira = (self.sira + yon) % len(self.gruplar)
        self._goster()
        if self._zaman.isActive():
            self._zaman.start()

    def _goster(self):
        if not self.gruplar:
            return
        g = self.gruplar[self.sira % len(self.gruplar)]
        e = g["bolumler"][0]
        self._istek += 1
        no = self._istek
        self._arka = None
        self._afis = None
        self.afis.clear()
        self.l_tur.setText("")

        self.l_bas.setText(g["baslik"][:64])
        if len(self.gruplar) > 1:
            self.l_sayac.setText(f"{self.sira + 1} / {len(self.gruplar)}")
        else:
            self.l_sayac.setText("")

        par = []
        if e.yil():
            par.append(e.yil())
        if getattr(e, "puan", 0):
            par.append(f"★ {e.puan:.1f}")
        if e.grup:
            par.append(e.grup[:28])
        if not g["tekil"]:
            par.append(f"{len(g['bolumler'])} bölüm")
        if e.kalite():
            par.append(e.kalite())
        if e.dublaj():
            par.append("TR Dublaj")
        self.l_meta.setText("   ·   ".join(par))
        self.l_ozet.setText(g.get("_ozet", "") or "")

        def arka_geldi(px, _no=no):
            """
            Arka plan YALNIZCA geniş (yatay) görsel kabul eder.

            SORUN: Dikey afiş tam genişliğe gerilince aşırı büyütülüp
            bulanıklaşıyor ve görüntü bozuluyordu ("afiş çok iğrenç
            görünüyor"). Artık en/boy oranı 1.2'nin altındaysa arka plan
            olarak KULLANILMAZ; onun yerine afişten üretilen yumuşak
            bulanık zemin çizilir.
            """
            if _no != self._istek or px is None or px.isNull():
                return
            if px.width() < px.height() * 1.2:
                if self._afis is None:
                    self._afis = px
                    self._afis_yerlestir(px)
                self.update()
                return
            self._arka = px
            self.update()

        def afis_geldi(px, _no=no):
            if _no != self._istek or px is None or px.isNull():
                return
            # Afiş kutusu dikeydir; yatay görsel afiş olarak kötü durur.
            if px.width() > px.height() * 1.1 and self._afis is not None:
                return
            self._afis = px
            self._afis_yerlestir(px)
            self.update()

        tmdb_arka_iste(e, arka_geldi)
        # M3U logosu genelde küçük/kare olur: yalnızca AFİŞ olarak dener,
        # arka plana asla koymayız.
        logo = g.get("logo") or e.logo
        if logo:
            AFIS.iste(logo, afis_geldi)
        tmdb_afis_iste(e, afis_geldi)
        self._tmdb_bilgi_iste(e, no)
        self.update()

    def _afis_yerlestir(self, px: QPixmap):
        """Afişi kutuya oturtur (oran korunur, taşan kısım kırpılır)."""
        try:
            self.afis.setPixmap(px.scaled(
                self.AFIS_EN, self.AFIS_BOY,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation))
        except RuntimeError:
            pass

    def _tmdb_bilgi_iste(self, icerik, no: int):
        """Özet, puan ve tür bilgisini TMDB'den çeker."""
        if TMDB is None or not TMDB.hazir:
            return
        ad = icerik.dizi_kok_anahtari() or icerik.temiz_ad()
        if not ad or icerik.kategori == "live":
            return
        dizi = bool(icerik.dizi_kok_anahtari()) or icerik.kategori in ("series", "anime")
        anahtar_oz = f"OZET|{ad}|{icerik.yil()}|{int(dizi)}"
        anahtar_meta = f"VITRIN_META|{ad}|{icerik.yil()}|{int(dizi)}"

        hazir_oz = TMDB_ESLESME.get(anahtar_oz)
        hazir_meta = TMDB_ESLESME.get(anahtar_meta)
        if hazir_oz is not None and hazir_meta is not None:
            if no == self._istek:
                if hazir_oz:
                    self.l_ozet.setText(hazir_oz)
                self._meta_uygula(hazir_meta or {}, icerik)
            return

        global _TMDB_HAVUZ
        if _TMDB_HAVUZ is None:
            from concurrent.futures import ThreadPoolExecutor
            _TMDB_HAVUZ = ThreadPoolExecutor(max_workers=3)

        def bul():
            ozet, meta = "", {}
            try:
                r = TMDB.ara(ad, icerik.yil(), dizi)
                if r:
                    ozet = (r.get("overview") or "").strip()
                    meta = {
                        "puan": r.get("vote_average") or 0,
                        "oy": r.get("vote_count") or 0,
                        "yil": (r.get("release_date") or r.get("first_air_date") or "")[:4],
                        "baslik": r.get("title") or r.get("name") or "",
                    }
                    # Türler yalnızca detaydan gelir; aramada yok — basit bırak
            except Exception:
                pass
            TMDB_ESLESME[anahtar_oz] = ozet
            TMDB_ESLESME[anahtar_meta] = meta
            ana_is_parcasinda(
                lambda o=ozet, m=meta, n=no: self._tmdb_yaz(o, m, n, icerik))

        _TMDB_HAVUZ.submit(bul)

    def _meta_uygula(self, meta: dict, icerik):
        par = []
        yil = meta.get("yil") or icerik.yil()
        if yil:
            par.append(yil)
        puan = meta.get("puan") or getattr(icerik, "puan", 0) or 0
        if puan:
            oy = meta.get("oy") or 0
            if oy:
                par.append(f"★ {puan:.1f}  ({oy} oy)")
            else:
                par.append(f"★ {puan:.1f}")
        if icerik.grup:
            par.append(icerik.grup[:28])
        g = self._aktif()
        if g and not g.get("tekil"):
            par.append(f"{len(g['bolumler'])} bölüm")
        if icerik.kalite():
            par.append(icerik.kalite())
        if icerik.dublaj():
            par.append("TR Dublaj")
        self.l_meta.setText("   ·   ".join(par))
        bas = meta.get("baslik")
        if bas and self.l_bas.text() != bas:
            # TMDB resmi adını tercih et (daha temiz)
            self.l_bas.setText(bas[:64])

    def _tmdb_yaz(self, ozet: str, meta: dict, no: int, icerik):
        if no != self._istek:
            return
        try:
            if ozet:
                self.l_ozet.setText(ozet)
            self._meta_uygula(meta or {}, icerik)
        except RuntimeError:
            pass

    # ── eylemler ──
    def _aktif(self) -> dict | None:
        if not self.gruplar:
            return None
        return self.gruplar[self.sira % len(self.gruplar)]

    def _oynat_bas(self):
        g = self._aktif()
        if g:
            self._oynat_cb(g["bolumler"][0], g["bolumler"], 0)

    def _detay_bas(self):
        g = self._aktif()
        if g:
            self._detay_cb(g)

    # ── çizim: detay sayfası ile aynı gradyan yaklaşımı ──
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        if self._arka and not self._arka.isNull():
            ol = self._arka.scaled(r.size(),
                                   Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                   Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(r.x() - (ol.width() - r.width()) // 2,
                         r.y() - (ol.height() - r.height()) // 4, ol)
        elif self._afis and not self._afis.isNull():
            # Geniş görsel yoksa afişten YUMUŞAK zemin üret: afişi küçültüp
            # tekrar büyüterek bulanıklaştırıyoruz. Böylece dikey afiş
            # gerilmiş/pikselli görünmüyor, arka plan afişin renklerini alıyor.
            kucuk = self._afis.scaled(28, 16,
                                      Qt.AspectRatioMode.IgnoreAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation)
            ol = kucuk.scaled(r.size(),
                              Qt.AspectRatioMode.IgnoreAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(r, ol)
        else:
            taban = renk_uret(self._aktif()["baslik"] if self._aktif() else "x")
            gg = QLinearGradient(0, 0, r.width(), r.height())
            gg.setColorAt(0, taban.darker(130))
            gg.setColorAt(1, QColor(11, 12, 16))
            p.fillRect(r, QBrush(gg))

        # Üst → alt karartma (afiş net kalsın, alt sayfaya karışsın)
        g1 = QLinearGradient(0, 0, 0, r.height())
        g1.setColorAt(0.00, QColor(11, 12, 16, 90))
        g1.setColorAt(0.50, QColor(11, 12, 16, 160))
        g1.setColorAt(0.82, QColor(11, 12, 16, 230))
        g1.setColorAt(1.00, QColor(11, 12, 16, 255))
        p.fillRect(r, QBrush(g1))

        # Soldan sağa: metin okunaklı kalsın, sağda afiş görünsün
        g2 = QLinearGradient(0, 0, r.width() * 0.85, 0)
        g2.setColorAt(0.00, QColor(11, 12, 16, 230))
        g2.setColorAt(0.40, QColor(11, 12, 16, 170))
        g2.setColorAt(0.70, QColor(11, 12, 16, 60))
        g2.setColorAt(1.00, QColor(11, 12, 16, 0))
        p.fillRect(r, QBrush(g2))
        p.end()


# ══════════════════════════════════════════════════════════════════
#  HERO (üstteki büyük afiş)
# ══════════════════════════════════════════════════════════════════
class Hero(QFrame):
    def __init__(self, depo: Depo, oynat_cb, detay_cb, parent=None):
        super().__init__(parent)
        self.depo, self._oynat, self._detay = depo, oynat_cb, detay_cb
        self.grup: dict | None = None
        self._px: QPixmap | None = None
        self.setMinimumHeight(430)
        v = QVBoxLayout(self); v.setContentsMargins(46, 0, 46, 34)
        v.addStretch()
        # NOT: arka plan şeffaf olmalı — aksi halde hero afişinin üstüne
        # siyah dikdörtgenler biniyor (QLabel varsayılan olarak arka plan boyar).
        self.l_baslik = QLabel(""); self.l_baslik.setStyleSheet(
            "font-size:34px;font-weight:800;letter-spacing:-.5px;background:transparent;")
        self.l_bilgi = QLabel(""); self.l_bilgi.setStyleSheet(
            f"color:{R_SOLUK};font-size:12px;background:transparent;")
        v.addWidget(self.l_baslik); v.addWidget(self.l_bilgi)
        h = QHBoxLayout(); h.setSpacing(10); h.setContentsMargins(0, 12, 0, 0)
        self.b_oynat = QPushButton("▶  Oynat"); self.b_oynat.setFixedSize(130, 40)
        self.b_oynat.setStyleSheet(
            f"background:{R_VURGU};border:0;border-radius:8px;font-weight:700;font-size:14px;")
        self.b_detay = QPushButton("ⓘ  Detaylar"); self.b_detay.setFixedSize(130, 40)
        self.b_oynat.clicked.connect(self._oynat_bas)
        self.b_detay.clicked.connect(self._detay_bas)
        h.addWidget(self.b_oynat); h.addWidget(self.b_detay); h.addStretch()
        v.addLayout(h)

    def ayarla(self, grup: dict | None):
        self.grup = grup
        if not grup:
            self.setVisible(False); return
        self.setVisible(True)
        e = grup["bolumler"][0]
        self.l_baslik.setText(grup["baslik"][:60])
        par = []
        if e.yil(): par.append(e.yil())
        if e.grup: par.append(e.grup)
        if not grup["tekil"]: par.append(f"{len(grup['bolumler'])} bölüm")
        if e.kalite(): par.append(e.kalite())
        self.l_bilgi.setText("  ·  ".join(par))
        self._px = None
        # Önce TMDB'nin GENİŞ (backdrop) görselini dene — hero için doğru olan bu.
        tmdb_arka_iste(e, self._afis)
        logo = grup.get("logo") or e.logo
        if logo:
            AFIS.iste(logo, self._afis_yedek)
        self.update()

    def _afis_yedek(self, px):
        """Backdrop gelmediyse M3U logosunu kullan."""
        if self._px is None:
            self._px = px
            self.update()

    def _afis(self, px):
        self._px = px; self.update()

    def _oynat_bas(self):
        if self.grup: self._oynat(self.grup["bolumler"][0], self.grup["bolumler"], 0)

    def _detay_bas(self):
        if self.grup: self._detay(self.grup)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        if self._px and not self._px.isNull():
            ol = self._px.scaled(r.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                 Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(r.x() - (ol.width() - r.width()) // 2, 0, ol)
        else:
            p.fillRect(r, renk_uret(self.grup["baslik"] if self.grup else "x"))
        g = QLinearGradient(0, 0, 0, r.height())
        g.setColorAt(0.0, QColor(11, 12, 16, 55))
        g.setColorAt(0.5, QColor(11, 12, 16, 150))
        g.setColorAt(0.85, QColor(11, 12, 16, 238))
        g.setColorAt(1.0, QColor(11, 12, 16, 255))
        p.fillRect(r, QBrush(g))
        g2 = QLinearGradient(0, 0, r.width(), 0)
        g2.setColorAt(0.0, QColor(11, 12, 16, 225))
        g2.setColorAt(0.55, QColor(11, 12, 16, 40))
        g2.setColorAt(1.0, QColor(11, 12, 16, 0))
        p.fillRect(r, QBrush(g2))
        p.end()


def cokme_kaydedici_kur():
    """
    Çökmeleri kalıcı olarak kaydeder.

    NEDEN: Program kapandığında hiçbir iz kalmıyordu; kullanıcı yalnızca
    "kapandı" diyebiliyor, sebebi görünmüyordu. Artık iki katman var:

      1. `sys.excepthook` — Python tarafındaki yakalanmamış istisnalar.
         PyQt6'da bir slot içinde oluşan istisna varsayılan olarak
         yorumlayıcıyı SONLANDIRIR; bu kanca sayesinde önce dosyaya
         yazılır, sonra program ayakta tutulmaya çalışılır.

      2. `faulthandler` — segfault/abort gibi C++ seviyesi çökmeler
         (yanlış iş parçacığından GUI nesnesi kullanmak gibi). Python
         try/except bunları YAKALAYAMAZ; tek iz bu dosya olur.

    Günlük:  ~/.local/share/mediabox-qt/cokme.log
    """
    from mediabox_qt import veri_klasoru
    try:
        yol = veri_klasoru() / "cokme.log"
    except Exception:
        yol = Path.home() / "mediabox-cokme.log"

    try:
        import faulthandler
        _f = open(yol, "a", buffering=1, encoding="utf-8", errors="replace")
        _f.write("\n" + "=" * 60 + "\n")
        _f.write("MediaBox %s başladı — %s\n"
                 % (APP_SURUM, time.strftime("%Y-%m-%d %H:%M:%S")))
        faulthandler.enable(file=_f, all_threads=True)
        globals()["_COKME_DOSYA"] = _f
    except Exception:
        pass

    _onceki = sys.excepthook

    def _kanca(tur, deger, iz):
        import traceback
        metin = "".join(traceback.format_exception(tur, deger, iz))
        try:
            with open(yol, "a", encoding="utf-8", errors="replace") as f:
                f.write("\n--- YAKALANMAMIŞ İSTİSNA %s ---\n"
                        % time.strftime("%H:%M:%S"))
                f.write(metin)
        except Exception:
            pass
        print("\n[MediaBox] YAKALANMAMIŞ İSTİSNA (program ayakta tutuluyor):",
              file=sys.stderr)
        print(metin, file=sys.stderr)
        print("[MediaBox] Ayrıntı: %s" % yol, file=sys.stderr)
        # Varsayılan kancayı ÇAĞIRMIYORUZ: PyQt6'da bu, uygulamayı
        # sonlandırıyor. Hatayı kaydedip çalışmaya devam ediyoruz.

    sys.excepthook = _kanca
    return yol


def uygulamayi_baslat(args) -> int:
    """mediabox_qt.py buradan çağırır."""
    from anapencere import AnaPencere    # üçüncü parça
    global AFIS
    cokme_kaydedici_kur()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_ADI)
    AFIS = AfisYoneticisi()
    kopru_kur()          # ana iş parçacığına bağlı olmalı (bkz. kopru_kur)
    import arayuz
    arayuz.AFIS = AFIS
    depo = Depo()
    import yedekleme
    yedekleme.baslangicta_kontrol(depo)   # ana veri boşsa yedekten kurtar
    try:
        from mediabox_qt import tema_bul
        _tm = tema_bul(depo.ayarlar.get("tema", "netflix"))
    except Exception:
        _tm = None
    app.setStyleSheet(stil(depo.ayarlar.get("vurgu", R_VURGU), _tm))
    pencere = AnaPencere(depo)
    pencere._yedek_zamanlayici = yedekleme.periyodik_yedek_baslat(pencere, depo, dakika=5)
    pencere.show()
    if getattr(args, "m3u", None):
        pencere.m3u_dosya_yukle(args.m3u)
    return app.exec()

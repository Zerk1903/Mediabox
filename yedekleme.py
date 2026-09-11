#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MediaBox — Periyodik Yedekleme + Açılışta Otomatik Kurtarma
==============================================================
AMAÇ
    Depo.kaydet() yalnızca ana veri dosyasına (~/.local/share/mediabox-qt/
    mediabox.json) yazar. Bu dosya bir sebeple boşalır/bozulursa (yanlış
    "kaynağı değiştir" kullanımı, disk sorunu, XDG_DATA_HOME'un farklı bir
    ortamda farklı çözülmesi vb.) favoriler ve izleme ilerlemesi kaybolmuş
    gibi görünür.

    Bu modül İKİ ek güvenlik katmanı ekler:

    1) PERİYODİK YEDEK — programın kendi klasörüne (bu dosyanın yanına)
       `mediabox_yedek.json` adıyla, belirli aralıklarla (varsayılan 5 dk)
       ayrı bir kopya yazar. Böylece veri iki farklı yerde durur.

    2) AÇILIŞTA OTOMATİK KURTARMA — program açılırken ana veri neredeyse
       boşsa (içerik/favori/ilerleme yok) ama yedek dosyasında veri varsa,
       yedekten otomatik geri yükler ve ana dosyayı da onarır.

KULLANIM (arayuz.py içinde, Depo() oluşturulduktan hemen sonra):

    import yedekleme
    depo = Depo()
    yedekleme.baslangicta_kontrol(depo)
    ...
    pencere = AnaPencere(depo)
    pencere._yedek_zamanlayici = yedekleme.periyodik_yedek_baslat(pencere, depo)

NOT
    Yedek yazımı, ana `Depo.kaydet()` ile birebir aynı JSON şemasını kullanır
    (`_icerik_sozluk` / `_icerikleri_coz` fonksiyonları mediabox_qt'den
    import edilir) — böylece iki dosya birbirinin yerine geçebilir.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def program_klasoru() -> Path:
    """Bu modülün (dolayısıyla programın .py dosyalarının) bulunduğu klasör."""
    return Path(__file__).resolve().parent


YEDEK_YOLU = program_klasoru() / "mediabox_yedek.json"


# ══════════════════════════════════════════════════════════════════
#  YEDEK YAZMA
# ══════════════════════════════════════════════════════════════════
def _depo_sozluk(depo) -> dict:
    """Depo.kaydet() ile AYNI şema — iki dosya birbirinin yerine geçebilsin."""
    from mediabox_qt import _icerik_sozluk, APP_SURUM
    return {
        "surum": APP_SURUM,
        "icerikler": [_icerik_sozluk(x) for x in depo.icerikler],
        "kaynaklar": depo.kaynaklar,
        "favoriler": depo.favoriler,
        "son_izlenen": depo.son_izlenen,
        "ilerleme": depo.ilerleme,
        "etiketler": depo.etiketler,
        "kuyruk": depo.kuyruk,
        "istatistik": depo.istatistik,
        "ayarlar": depo.ayarlar,
        "_yedek_zamani": time.time(),
    }


def yedek_yaz(depo) -> bool:
    """
    Depoyu program klasöründeki yedek dosyaya ATOMİK olarak yazar.
    Hata olursa sessizce False döner — yedekleme asla programı çökertmemeli.
    """
    try:
        d = _depo_sozluk(depo)
        YEDEK_YOLU.parent.mkdir(parents=True, exist_ok=True)
        gecici = YEDEK_YOLU.with_suffix(".tmp")
        with open(gecici, "w", encoding="utf-8") as f:
            f.write(json.dumps(d, ensure_ascii=False))
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(gecici, YEDEK_YOLU)
        return True
    except Exception as e:
        print(f"[MediaBox] Yedek yazılamadı: {type(e).__name__}: {e}", file=sys.stderr)
        return False


# ══════════════════════════════════════════════════════════════════
#  AÇILIŞTA OTOMATİK KURTARMA
# ══════════════════════════════════════════════════════════════════
def _yedegi_oku() -> dict | None:
    if not YEDEK_YOLU.is_file():
        return None
    try:
        return json.loads(YEDEK_YOLU.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[MediaBox] Yedek okunamadı: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def _depo_bos_mu(depo) -> bool:
    """Tüm depo baştan mı boş? (ilk kurulum ya da tam veri kaybı)"""
    return (not depo.icerikler and not depo.favoriler
            and not depo.ilerleme and not depo.kuyruk)


def baslangicta_kontrol(depo) -> bool:
    """
    Program açılırken çağrılır (Depo() zaten kendi yukle()'sini yapmış olmalı).

    İKİ ayrı senaryoyu ele alır:
    1) Depo TAMAMEN boş (ilk kurulum ya da tam kayıp) → yedekte veri varsa
       tüm depoyu yedekten geri yükler.
    2) Depo kısmen boş — ör. içerik listesi duruyor ama FAVORİLER ya da
       İLERLEME (izlemeye devam) boşalmış — bunları da AYRI AYRI, sadece
       o alan gerçekten boşsa ve yedekte doluysa geri yükler. Bu, "kanal
       listesi duruyor ama favorilerim/izleme kayıtlarım gitti" durumunu
       da yakalar; önceki sürüm yalnızca dördü BİRDEN boşsa kurtarıyordu.

    Döner: herhangi bir alan gerçekten geri yüklendiyse True.
    """
    yedek = _yedegi_oku()
    if not yedek:
        return False

    if _depo_bos_mu(depo):
        if not (yedek.get("icerikler") or yedek.get("favoriler") or yedek.get("ilerleme")):
            return False
        try:
            from mediabox_qt import _icerikleri_coz
            depo.icerikler = _icerikleri_coz(yedek.get("icerikler", []))
            depo.kaynaklar = yedek.get("kaynaklar", [])
            depo.favoriler = yedek.get("favoriler", [])
            depo.son_izlenen = yedek.get("son_izlenen", [])
            depo.ilerleme = yedek.get("ilerleme", {})
            depo.etiketler = yedek.get("etiketler", {})
            depo.kuyruk = yedek.get("kuyruk", [])
            depo.istatistik = yedek.get("istatistik", {})
            depo.ayarlar.update(yedek.get("ayarlar", {}))
            depo.kaydet()
            print(f"[MediaBox] Tam veri kaybı — yedekten TAMAMEN kurtarıldı "
                  f"({len(depo.icerikler)} içerik, {len(depo.favoriler)} favori, "
                  f"{len(depo.ilerleme)} izleme kaydı).", file=sys.stderr)
            return True
        except Exception as e:
            print(f"[MediaBox] Tam kurtarma başarısız: {type(e).__name__}: {e}",
                  file=sys.stderr)
            return False

    # ── Kısmi kurtarma: yalnızca gerçekten boşalmış alanları doldur ──
    kurtarildi = False
    alanlar = [
        ("favoriler", "favoriler"),
        ("ilerleme", "ilerleme"),
        ("son_izlenen", "son_izlenen"),
        ("kuyruk", "kuyruk"),
        ("etiketler", "etiketler"),
    ]
    for depo_alani, yedek_anahtari in alanlar:
        mevcut = getattr(depo, depo_alani)
        yedekteki = yedek.get(yedek_anahtari)
        if not mevcut and yedekteki:
            setattr(depo, depo_alani, yedekteki)
            kurtarildi = True
            print(f"[MediaBox] '{depo_alani}' boştu, yedekten kurtarıldı "
                  f"({len(yedekteki)} kayıt).", file=sys.stderr)

    if kurtarildi:
        try:
            depo.kaydet()
        except Exception as e:
            print(f"[MediaBox] Kısmi kurtarma sonrası kayıt başarısız: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
    return kurtarildi


# ══════════════════════════════════════════════════════════════════
#  PERİYODİK YEDEKLEME (QTimer)
# ══════════════════════════════════════════════════════════════════
def periyodik_yedek_baslat(parent_widget, depo, dakika: int = 5):
    """
    `dakika` aralıkla hem ana dosyayı (depo.kaydet) hem de program
    klasöründeki yedeği (yedek_yaz) tazeler.

    Döndürülen QTimer nesnesini bir yere ata (ör. pencere._yedek_zamanlayici),
    yoksa Python çöp toplayıcısı zamanlayıcıyı yok edip durdurabilir.
    """
    from PyQt6.QtCore import QTimer

    def _guvenli_yedekle():
        try:
            depo.kaydet()
        except Exception as e:
            print(f"[MediaBox] Periyodik kayıt (ana): {type(e).__name__}: {e}",
                  file=sys.stderr)
        yedek_yaz(depo)      # kendi içinde try/except'li

    zaman = QTimer(parent_widget)
    zaman.setInterval(max(1, dakika) * 60 * 1000)
    zaman.timeout.connect(_guvenli_yedekle)
    zaman.start()
    return zaman

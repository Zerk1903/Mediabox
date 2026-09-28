#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MediaBox — Yedek Klasörü + Açılışta Otomatik Kurtarma
======================================================
Kullanıcı bir YEDEK KLASÖRÜ seçer (Dropbox/Drive/Syncthing klasörü, USB,
ağ diski… fark etmez). Program tüm verisini oraya yazar:

    <klasör>/mediabox_yedek.json              ← en güncel yedek
    <klasör>/mediabox_yedek_2026-09-28.json   ← günlük kopya (son 7 gün)

Format sonrası: Ayarlar → "Yedek klasöründen geri yükle" → klasörü göster.
Tüm kütüphane, favoriler, izleme ilerlemesi ve ayarlar geri gelir.

Klasör seçilmemişse eski davranış sürer: yedek programın kendi klasörüne
(bu dosyanın yanına) yazılır, böylece veri yine iki yerde durur.

GÜVENLİK
    • Yazma atomiktir (önce .tmp, sonra os.replace).
    • Depo boşalmışsa dolu yedeğin ÜZERİNE YAZILMAZ.
    • Açılışta ana veri boşsa (ya da favori/ilerleme boşalmışsa) yedekten
      otomatik kurtarılır.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

ANA_AD = "mediabox_yedek.json"
GUNLUK_KALIP = re.compile(r"^mediabox_yedek_(\d{4}-\d{2}-\d{2})\.json$")
GUNLUK_SAKLA = 7


def program_klasoru() -> Path:
    """Bu modülün (dolayısıyla programın .py dosyalarının) bulunduğu klasör."""
    return Path(__file__).resolve().parent


# Klasör seçilmemişse kullanılan yedek konumu (geriye dönük uyum)
YEDEK_YOLU = program_klasoru() / ANA_AD


# ══════════════════════════════════════════════════════════════════
#  KONUM
# ══════════════════════════════════════════════════════════════════
def yedek_klasoru(depo) -> Path | None:
    """Kullanıcının seçtiği yedek klasörü (yoksa None)."""
    k = str((getattr(depo, "ayarlar", {}) or {}).get("yedek_klasoru") or "").strip()
    return Path(k).expanduser() if k else None


def hedef_yol(depo) -> Path:
    """Ana yedek dosyasının yolu (klasör seçiliyse orası, yoksa program klasörü)."""
    k = yedek_klasoru(depo)
    return (k / ANA_AD) if k else YEDEK_YOLU


# ══════════════════════════════════════════════════════════════════
#  YEDEK YAZMA
# ══════════════════════════════════════════════════════════════════
def _depo_sozluk(depo) -> dict:
    """Depo.kaydet() ile AYNI şema — dosyalar birbirinin yerine geçebilsin."""
    from mediabox_qt import _icerik_sozluk, APP_SURUM
    return {
        "surum": APP_SURUM,
        "yedek_turu": "tam",
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


def _atomik_yaz(yol: Path, metin: str) -> None:
    yol.parent.mkdir(parents=True, exist_ok=True)
    gecici = yol.with_suffix(".tmp")
    with open(gecici, "w", encoding="utf-8") as f:
        f.write(metin)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(gecici, yol)


def _eski_gunlukleri_temizle(klasor: Path) -> None:
    try:
        dosyalar = sorted(
            (p for p in klasor.iterdir() if GUNLUK_KALIP.match(p.name)),
            key=lambda p: p.name, reverse=True)
        for p in dosyalar[GUNLUK_SAKLA:]:
            try:
                p.unlink()
            except OSError:
                pass
    except OSError:
        pass


def yedek_yaz(depo, hata_ver: bool = False) -> bool:
    """
    Depoyu yedek konumuna yazar. Sessizce False döner (hata_ver=True ise
    istisna fırlatır — "Şimdi yedekle" düğmesi nedenini gösterebilsin).
    """
    try:
        yol = hedef_yol(depo)
        # KORUMA: depo boşalmışsa (hata/bozulma) dolu yedeğin üzerine yazma
        if _depo_bos_mu(depo):
            eski = _yedegi_oku(yol)
            if eski and (eski.get("icerikler") or eski.get("favoriler")
                         or eski.get("ilerleme")):
                return False
        metin = json.dumps(_depo_sozluk(depo), ensure_ascii=False)
        _atomik_yaz(yol, metin)
        # Günlük kopya (yalnızca kullanıcı klasörü seçtiyse)
        if yedek_klasoru(depo):
            gunluk = yol.parent / f"mediabox_yedek_{date.today().isoformat()}.json"
            if not gunluk.exists() or gunluk.stat().st_mtime < time.time() - 86400:
                _atomik_yaz(gunluk, metin)
            _eski_gunlukleri_temizle(yol.parent)
        return True
    except Exception as e:
        print(f"[MediaBox] Yedek yazılamadı: {type(e).__name__}: {e}", file=sys.stderr)
        if hata_ver:
            raise
        return False


# ══════════════════════════════════════════════════════════════════
#  YEDEK OKUMA
# ══════════════════════════════════════════════════════════════════
def _yedegi_oku(yol: Path) -> dict | None:
    try:
        if not Path(yol).is_file():
            return None
        d = json.loads(Path(yol).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except Exception as e:
        print(f"[MediaBox] Yedek okunamadı ({yol}): {type(e).__name__}: {e}",
              file=sys.stderr)
        return None


def klasordeki_en_yeni_yedek(klasor: str | Path) -> tuple[dict | None, Path | None]:
    """
    Klasördeki en uygun yedeği bulur: önce mediabox_yedek.json, o okunamazsa
    en yeni günlük kopya. Döner: (veri, dosya_yolu)
    """
    klasor = Path(klasor)
    adaylar = [klasor / ANA_AD]
    try:
        gunluk = sorted((p for p in klasor.iterdir() if GUNLUK_KALIP.match(p.name)),
                        key=lambda p: p.name, reverse=True)
        adaylar += gunluk
    except OSError:
        pass
    for p in adaylar:
        d = _yedegi_oku(p)
        if d and "icerikler" in d:
            return d, p
    return None, None


def yedek_ozeti(d: dict) -> str:
    """Kullanıcıya gösterilecek kısa özet."""
    z = d.get("_yedek_zamani") or d.get("zaman") or 0
    try:
        tarih = time.strftime("%d.%m.%Y %H:%M", time.localtime(float(z))) if z else "?"
    except Exception:
        tarih = "?"
    return (f"{len(d.get('icerikler') or [])} içerik · "
            f"{len(d.get('favoriler') or [])} favori · "
            f"{len(d.get('ilerleme') or {})} izleme kaydı\n"
            f"Yedek tarihi: {tarih}")


# ══════════════════════════════════════════════════════════════════
#  AÇILIŞTA OTOMATİK KURTARMA
# ══════════════════════════════════════════════════════════════════
def _depo_bos_mu(depo) -> bool:
    """Tüm depo baştan mı boş? (ilk kurulum ya da tam veri kaybı)"""
    return (not depo.icerikler and not depo.favoriler
            and not depo.ilerleme and not depo.kuyruk)


def baslangicta_kontrol(depo) -> bool:
    """
    Program açılırken çağrılır (Depo() kendi yukle()'sini yapmış olmalı).

    1) Depo TAMAMEN boşsa ve yedekte veri varsa → tümü geri yüklenir.
    2) Depo kısmen boşsa (favori/ilerleme/kuyruk… boşalmış) → yalnızca
       o alanlar, yedekte doluysa, geri yüklenir.

    Döner: herhangi bir alan geri yüklendiyse True.
    """
    yol = hedef_yol(depo)
    yedek = _yedegi_oku(yol)
    if not yedek:
        return False

    if _depo_bos_mu(depo):
        if not (yedek.get("icerikler") or yedek.get("favoriler") or yedek.get("ilerleme")):
            return False
        try:
            yedegi_uygula(depo, yedek)
            print(f"[MediaBox] Tam veri kaybı — yedekten TAMAMEN kurtarıldı "
                  f"({len(depo.icerikler)} içerik, {len(depo.favoriler)} favori, "
                  f"{len(depo.ilerleme)} izleme kaydı).", file=sys.stderr)
            return True
        except Exception as e:
            print(f"[MediaBox] Tam kurtarma başarısız: {type(e).__name__}: {e}",
                  file=sys.stderr)
            return False

    kurtarildi = False
    for alan in ("favoriler", "ilerleme", "son_izlenen", "kuyruk", "etiketler"):
        mevcut = getattr(depo, alan)
        yedekteki = yedek.get(alan)
        if not mevcut and yedekteki:
            setattr(depo, alan, yedekteki)
            kurtarildi = True
            print(f"[MediaBox] '{alan}' boştu, yedekten kurtarıldı "
                  f"({len(yedekteki)} kayıt).", file=sys.stderr)
    if kurtarildi:
        try:
            depo.kaydet()
        except Exception as e:
            print(f"[MediaBox] Kısmi kurtarma sonrası kayıt başarısız: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
    return kurtarildi


def yedegi_uygula(depo, yedek: dict, yedek_klasoru_yolu: str = "") -> None:
    """
    Yedek sözlüğünü depoya yazar ve diske kaydeder.

    Bu bilgisayardaki TMDB anahtarı ve Dropbox oturumu (varsa) korunur;
    yedek klasörü yolu da verildiyse ayar olarak kaydedilir.
    """
    from mediabox_qt import _icerikleri_coz
    korunan = {k: depo.ayarlar.get(k) for k in
               ("dropbox_app_key", "dropbox_refresh_token", "dropbox_hesap_adi")
               if depo.ayarlar.get(k)}
    depo.icerikler = _icerikleri_coz(yedek.get("icerikler", []))
    depo.kaynaklar = yedek.get("kaynaklar", [])
    depo.favoriler = yedek.get("favoriler", [])
    depo.son_izlenen = yedek.get("son_izlenen", [])
    depo.ilerleme = yedek.get("ilerleme", {})
    depo.etiketler = yedek.get("etiketler", {})
    depo.kuyruk = yedek.get("kuyruk", [])
    depo.istatistik = yedek.get("istatistik", {})
    depo.ayarlar.update(yedek.get("ayarlar", {}) or {})
    depo.ayarlar.update(korunan)
    if yedek_klasoru_yolu:
        depo.ayarlar["yedek_klasoru"] = str(yedek_klasoru_yolu)
    depo.kaydet()


# ══════════════════════════════════════════════════════════════════
#  PERİYODİK YEDEKLEME (QTimer)
# ══════════════════════════════════════════════════════════════════
def periyodik_yedek_baslat(parent_widget, depo, dakika: int = 5):
    """
    `dakika` aralıkla hem ana dosyayı (depo.kaydet) hem de yedek
    konumunu (yedek_yaz) tazeler. Dönen QTimer'ı bir yere ata
    (ör. pencere._yedek_zamanlayici), yoksa çöp toplayıcı durdurur.
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

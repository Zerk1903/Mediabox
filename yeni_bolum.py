#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MediaBox — Yeni Bölüm Takibi
=============================
İzlediğiniz dizilerin yeni bölümü yayınlandığında haber verir.

NASIL ÇALIŞIR
    TMDB dizi ucu üç alan döndürür (ölçüldü, hepsi geliyor):
        status                 → "Returning Series" / "Ended"
        last_episode_to_air    → yayınlanmış SON bölüm
        next_episode_to_air    → yayınlanacak SONRAKİ bölüm

    Elimizdeki en yüksek bölüm ile TMDB'nin son bölümü karşılaştırılır.
    TMDB daha ilerideyse "yeni bölüm var" denir.

ÖLÇÜM ÖRNEĞİ
    Breaking Bad    durum=Ended             son=S05E16 2013-09-29
    Severance       durum=Returning Series  son=S02E10 2025-03-20
    Rick and Morty  durum=Returning Series  son=S09E10 2026-07-26

KAYNAK TASARRUFU
    • Yalnızca izleme geçmişi/favori/kuyrukta olan diziler kontrol edilir.
    • "Ended" (bitmiş) diziler atlanır.
    • Sonuçlar depoya yazılır; varsayılan 12 saatte bir yenilenir.
"""

from __future__ import annotations

import time
from datetime import date, datetime


def _tarih(metin: str):
    try:
        return datetime.strptime((metin or "")[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def izlenen_diziler(depo) -> dict[str, list]:
    """
    Kullanıcının ilgilendiği diziler: {dizi_anahtari: [bölümler]}

    İlgi ölçütü: izleme geçmişi, yarım kalan bölüm, favori ya da kuyruk.
    """
    ilgi = set(depo.ilerleme.keys()) | set(depo.son_izlenen) | \
        set(depo.favoriler) | set(getattr(depo, "kuyruk", []))
    if not ilgi:
        return {}
    gruplar: dict[str, list] = {}
    ilgili_anahtar = set()
    for e in depo.icerikler:
        a = e.dizi_kok_anahtari()
        if not a:
            continue
        gruplar.setdefault(a, []).append(e)
        if e.url in ilgi:
            ilgili_anahtar.add(a)
    return {a: b for a, b in gruplar.items() if a in ilgili_anahtar}


def elimizdeki_son(bolumler) -> tuple[int, int]:
    """Elimizdeki en yüksek (sezon, bölüm)."""
    en = (0, 0)
    for b in bolumler:
        sb = b.sezon_bolum()
        if sb and (sb[0], sb[1]) > en:
            en = (sb[0], sb[1])
    return en


def kontrol_et(depo, istemci, sinir: int = 40) -> list[dict]:
    """
    Yeni bölüm taraması yapar. Sonuç listesi döndürür:
        [{ad, tmdb_id, bende:(s,e), tmdb:(s,e), tarih, sonraki, sonraki_tarih}]
    """
    if not (istemci and getattr(istemci, "hazir", False)):
        return []
    diziler = izlenen_diziler(depo)
    if not diziler:
        return []

    bugun = date.today()
    bulunan = []
    sayac = 0
    for ad, bolumler in diziler.items():
        if sayac >= sinir:
            break
        tid = 0
        for b in bolumler:
            tid = getattr(b, "tmdb_id", 0) or 0
            if tid:
                break
        if not tid:
            # TMDB eşleşmesi yoksa adla ara (tek seferlik)
            try:
                r = istemci.ara(ad, bolumler[0].yil(), True)
                tid = (r or {}).get("id", 0) or 0
                if tid:
                    for b in bolumler:
                        b.tmdb_id = tid
            except Exception:
                tid = 0
        if not tid:
            continue

        sayac += 1
        try:
            d = istemci.detay(tid, True)
        except Exception:
            continue
        if not d:
            continue

        durum = (d.get("status") or "").lower()
        son = d.get("last_episode_to_air") or {}
        gel = d.get("next_episode_to_air") or {}
        if not son and not gel:
            continue

        tmdb_se = (son.get("season_number", 0) or 0, son.get("episode_number", 0) or 0)
        bende = elimizdeki_son(bolumler)
        yayin = _tarih(son.get("air_date"))

        yeni_var = tmdb_se > bende and tmdb_se != (0, 0)
        # Gelecekte yayınlanacak bölümü "çıkmış" saymayalım
        if yeni_var and yayin and yayin > bugun:
            yeni_var = False

        if yeni_var:
            bulunan.append({
                "ad": ad,
                "tmdb_id": tid,
                "bende": bende,
                "tmdb": tmdb_se,
                "baslik": son.get("name") or "",
                "tarih": son.get("air_date") or "",
                "durum": durum,
                "sonraki": (gel.get("season_number"), gel.get("episode_number")) if gel else None,
                "sonraki_tarih": gel.get("air_date") if gel else "",
            })

    depo.ayarlar["yeni_bolum_son_kontrol"] = time.time()
    depo.ayarlar["yeni_bolum_sonuc"] = bulunan
    try:
        depo.kaydet()
    except Exception:
        pass
    return bulunan


def kontrol_zamani_geldi(depo, saat: int = 12) -> bool:
    """Son taramanın üstünden yeterli süre geçti mi?"""
    if not depo.ayarlar.get("yeni_bolum_bildirim", True):
        return False
    son = float(depo.ayarlar.get("yeni_bolum_son_kontrol", 0) or 0)
    return (time.time() - son) > saat * 3600


def bekleyen_sonuc(depo) -> list[dict]:
    """Son taramanın sonucu (arayüz rozet için kullanır)."""
    s = depo.ayarlar.get("yeni_bolum_sonuc")
    return s if isinstance(s, list) else []


def okundu_isaretle(depo, ad: str = ""):
    """Bildirimi kapat: tek dizi ya da (ad boşsa) hepsi."""
    kalan = [] if not ad else [x for x in bekleyen_sonuc(depo) if x.get("ad") != ad]
    depo.ayarlar["yeni_bolum_sonuc"] = kalan
    try:
        depo.kaydet()
    except Exception:
        pass
    return kalan


def ozet_metni(sonuc: list[dict]) -> str:
    """Bildirim için kısa özet."""
    if not sonuc:
        return ""
    if len(sonuc) == 1:
        x = sonuc[0]
        s, e = x["tmdb"]
        return f"{x['ad']} — S{s:02d}E{e:02d} yayınlandı"
    return f"{len(sonuc)} dizinin yeni bölümü yayınlandı"

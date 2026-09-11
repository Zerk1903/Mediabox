#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MediaBox — Fragman (Trailer) Desteği
=====================================
TMDB'den YouTube fragmanlarını bulur ve programın kendi oynatıcısında
(mpv + yt-dlp) açar.

NEDEN İKİ İSTEK?
    TMDB'nin `videos` ucu dile duyarlıdır ve iki liste ÖRTÜŞMEZ:

        Inception   tr-TR → 0 sonuç,  varsayılan → 27 video (3 trailer)
        Breaking Bad tr-TR → 1 Türkçe fragman, varsayılan → 0
        Oppenheimer tr-TR → 2 (biri "Türkçe Alt Yazılı"), varsayılan → 51

    Yalnızca birine bakmak fragmanı kaçırıyor. Bu yüzden ikisi de
    çekilip birleştirilir; Türkçe olanlar listenin başına alınır.

SIRALAMA ÖNCELİĞİ
    1. Türkçe (tr) fragmanlar
    2. Resmi (official) olanlar
    3. Tür: Trailer > Teaser > Clip > diğer
    4. Yeni tarihli olanlar
"""

from __future__ import annotations

TUR_SIRASI = {"trailer": 0, "teaser": 1, "clip": 2, "featurette": 3,
              "behind the scenes": 4, "bloopers": 5}


def youtube_adresi(anahtar: str) -> str:
    return f"https://www.youtube.com/watch?v={anahtar}"


def _puan(v: dict) -> tuple:
    """Sıralama anahtarı — küçük olan önce gelir."""
    dil = (v.get("iso_639_1") or "").lower()
    tur = (v.get("type") or "").lower()
    return (
        0 if dil == "tr" else 1,
        0 if v.get("official") else 1,
        TUR_SIRASI.get(tur, 9),
        # Yeni tarih önce (ters sıralama için negatif etki)
        _tarih_ters(v.get("published_at") or ""),
    )


def _tarih_ters(metin: str) -> str:
    """Yeni tarihler önce gelsin diye karakterleri ters çevirir."""
    try:
        # "2023-07-20..." → sıralamada büyük olan önce olmalı
        return "".join(chr(255 - ord(c)) for c in metin[:10])
    except Exception:
        return ""


def fragmanlari_getir(istemci, tmdb_id: int, dizi: bool = False) -> list[dict]:
    """
    Fragman listesi döndürür: [{ad, anahtar, url, tur, dil, resmi, tarih}]

    Hem Türkçe hem varsayılan dil sorgulanır, sonuçlar birleştirilir.
    """
    if not (istemci and getattr(istemci, "hazir", False) and tmdb_id):
        return []
    tur = "tv" if dizi else "movie"
    toplam: dict[str, dict] = {}

    # DİKKAT: `_istek` parametre verilmezse varsayılan dili (tr-TR) ekler.
    # Bu yüzden İngilizce listeyi almak için dili AÇIKÇA "en-US" yapmak
    # gerekir; yoksa iki istek de aynı sonucu döndürür ve Inception gibi
    # yapımlarda hiç fragman bulunamaz (ölçüldü: 0 fragman).
    for dil in ("tr-TR", "en-US"):
        try:
            d = istemci._istek(f"/{tur}/{tmdb_id}/videos", language=dil)
        except Exception:
            d = None
        for v in (d or {}).get("results", []):
            if (v.get("site") or "").lower() != "youtube":
                continue
            anahtar = v.get("key")
            if not anahtar or anahtar in toplam:
                continue
            toplam[anahtar] = v

    liste = sorted(toplam.values(), key=_puan)
    cikti = []
    for v in liste:
        cikti.append({
            "ad": (v.get("name") or "Fragman").strip(),
            "anahtar": v["key"],
            "url": youtube_adresi(v["key"]),
            "tur": (v.get("type") or "").strip(),
            "dil": (v.get("iso_639_1") or "").lower(),
            "resmi": bool(v.get("official")),
            "tarih": (v.get("published_at") or "")[:10],
        })
    return cikti


def en_iyi(fragmanlar: list[dict]) -> dict | None:
    """Listedeki en uygun fragman (zaten sıralı gelir)."""
    return fragmanlar[0] if fragmanlar else None


def etiket(f: dict) -> str:
    """Menü/liste için okunur etiket."""
    par = []
    if f.get("dil") == "tr":
        par.append("TR")
    if f.get("resmi"):
        par.append("resmi")
    t = (f.get("tur") or "").lower()
    if t and t != "trailer":
        par.append(f.get("tur"))
    ek = f"  ({' · '.join(par)})" if par else ""
    return f"{f['ad'][:52]}{ek}"

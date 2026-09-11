#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MediaBox — TMDB İstemcisi
==========================
Afiş, arka plan görseli, özet, puan, tür ve oyuncu kadrosu bilgilerini
themoviedb.org üzerinden çeker.

TASARIM
    • Tüm ağ işleri arka planda; arayüz asla donmaz.
    • Disk önbelleği: aynı film ikinci kez sorgulanmaz (varsayılan 14 gün).
    • Eşleştirme M3U adından yapılır: "Yıldızlararası (2014) 1080p" →
      ad "Yıldızlararası" + yıl "2014" olarak temizlenip aranır.
    • Dizi mi film mi olduğu içeriğin kategorisinden anlaşılır.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

TABAN = "https://api.themoviedb.org/3"
GORSEL = "https://image.tmdb.org/t/p/"

# Görsel boyutları
AFIS_BOY = "w342"        # kart afişi
AFIS_BUYUK = "w500"      # detay sayfası
ARKA_BOY = "w1280"       # arka plan (backdrop)
KISI_BOY = "w185"        # oyuncu fotoğrafı


def afis_url(yol: str, boy: str = AFIS_BOY) -> str:
    if not yol:
        return ""
    return f"{GORSEL}{boy}{yol}"


class TmdbIstemci:
    """TMDB sorguları + disk önbelleği."""

    def __init__(self, anahtar: str, onbellek_klasoru: Path, dil: str = "tr-TR",
                 gecerlilik_gun: int = 14):
        self.anahtar = (anahtar or "").strip()
        self.dil = dil
        self.klasor = Path(onbellek_klasoru)
        self.klasor.mkdir(parents=True, exist_ok=True)
        self.gecerlilik = gecerlilik_gun * 86400
        self._bellek: dict[str, dict] = {}
        self._kilit = threading.Lock()
        self._son_hata = ""

    @property
    def hazir(self) -> bool:
        return len(self.anahtar) >= 20

    # ── önbellek ───────────────────────────────────────────────────
    def _dosya(self, anahtar: str) -> Path:
        import hashlib
        ad = hashlib.md5(anahtar.encode("utf-8")).hexdigest()
        return self.klasor / f"{ad}.json"

    def _oku(self, anahtar: str):
        with self._kilit:
            if anahtar in self._bellek:
                return self._bellek[anahtar]
        p = self._dosya(anahtar)
        if not p.is_file():
            return None
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
        if time.time() - d.get("_zaman", 0) > self.gecerlilik:
            return None
        veri = d.get("veri")
        with self._kilit:
            self._bellek[anahtar] = veri
        return veri

    def _yaz(self, anahtar: str, veri):
        with self._kilit:
            self._bellek[anahtar] = veri
        try:
            self._dosya(anahtar).write_text(
                json.dumps({"_zaman": time.time(), "veri": veri}, ensure_ascii=False),
                encoding="utf-8")
        except Exception:
            pass

    def onbellek_temizle(self) -> int:
        n = 0
        with self._kilit:
            self._bellek.clear()
        for p in self.klasor.glob("*.json"):
            try:
                p.unlink(); n += 1
            except OSError:
                pass
        return n

    # ── ağ ─────────────────────────────────────────────────────────
    def _istek(self, yol: str, **parametre):
        if not self.hazir:
            self._son_hata = "TMDB anahtarı girilmemiş"
            return None
        import requests
        p = {"api_key": self.anahtar, "language": self.dil}
        p.update(parametre)
        try:
            r = requests.get(f"{TABAN}{yol}", params=p, timeout=12)
            if r.status_code == 401:
                self._son_hata = "TMDB anahtarı geçersiz"
                return None
            if r.status_code != 200:
                self._son_hata = f"TMDB HTTP {r.status_code}"
                return None
            return r.json()
        except Exception as e:
            self._son_hata = f"{type(e).__name__}: {e}"
            return None

    @property
    def son_hata(self) -> str:
        return self._son_hata

    # ── arama & detay ──────────────────────────────────────────────
    def ara(self, ad: str, yil: str = "", dizi: bool = False):
        """Ada göre arar, en olası eşleşmeyi döndürür."""
        ad = (ad or "").strip()
        if not ad:
            return None
        anahtar = f"ara|{'tv' if dizi else 'movie'}|{ad}|{yil}"
        onb = self._oku(anahtar)
        if onb is not None:
            return onb or None

        tur = "tv" if dizi else "movie"
        p = {"query": ad, "include_adult": "false"}
        if yil:
            p["first_air_date_year" if dizi else "year"] = yil
        d = self._istek(f"/search/{tur}", **p)
        sonuc = (d or {}).get("results") or []

        # Yılla bulunamadıysa yılsız dene
        if not sonuc and yil:
            d = self._istek(f"/search/{tur}", query=ad, include_adult="false")
            sonuc = (d or {}).get("results") or []

        if not sonuc:
            self._yaz(anahtar, {})
            return None

        en_iyi = self._en_iyi_eslesme(sonuc, ad, yil)
        if en_iyi:
            en_iyi["media_type"] = tur
        self._yaz(anahtar, en_iyi or {})
        return en_iyi

    @staticmethod
    def _en_iyi_eslesme(sonuclar: list, ad: str, yil: str):
        """Ad benzerliği + yıl uyumu + popülerliğe göre puanlar."""
        def sadelestir(s):
            s = (s or "").lower()
            for a, b in (("ı","i"),("İ","i"),("ş","s"),("ğ","g"),("ü","u"),("ö","o"),("ç","c")):
                s = s.replace(a, b)
            return re.sub(r"[^a-z0-9]+", "", s)

        hedef = sadelestir(ad)
        en_iyi, en_puan = None, -1.0
        for r in sonuclar[:12]:
            baslik = r.get("title") or r.get("name") or ""
            orj = r.get("original_title") or r.get("original_name") or ""
            puan = 0.0
            for b in (baslik, orj):
                s = sadelestir(b)
                if not s:
                    continue
                if s == hedef:
                    puan = max(puan, 100)
                elif hedef and (hedef in s or s in hedef):
                    puan = max(puan, 70)
            tarih = (r.get("release_date") or r.get("first_air_date") or "")[:4]
            if yil and tarih:
                fark = abs(int(yil) - int(tarih)) if tarih.isdigit() else 9
                puan += 25 if fark == 0 else (10 if fark == 1 else -12 * min(fark, 4))
            puan += min(r.get("popularity", 0), 60) / 12.0
            if puan > en_puan:
                en_puan, en_iyi = puan, r
        return en_iyi if en_puan > 8 else (sonuclar[0] if sonuclar else None)

    def detay(self, tmdb_id: int, dizi: bool = False):
        """Tam detay: özet, tür, süre, oyuncu kadrosu, benzer içerikler."""
        if not tmdb_id:
            return None
        tur = "tv" if dizi else "movie"
        anahtar = f"detay|{tur}|{tmdb_id}"
        onb = self._oku(anahtar)
        if onb is not None:
            return onb or None
        d = self._istek(f"/{tur}/{tmdb_id}",
                        append_to_response="credits,recommendations,external_ids")
        if d:
            self._yaz(anahtar, d)
        return d

    def sezon(self, tmdb_id: int, sezon_no: int):
        """
        Bir sezonun BÖLÜM listesi: Türkçe ad, özet, kare görsel, puan, tarih.

        Ölçüldü (Breaking Bad S2): 13 bölüm, adlar Türkçe geliyor
        ("Yedi Yüz Otuz Yedi", "Ölü Arının İğnesi"), still_path mevcut.
        """
        if not tmdb_id:
            return None
        anahtar = f"sezon|{tmdb_id}|{sezon_no}"
        onb = self._oku(anahtar)
        if onb is not None:
            return onb or None
        d = self._istek(f"/tv/{tmdb_id}/season/{sezon_no}")
        if d:
            self._yaz(anahtar, d)
        return d

    def bolum_haritasi(self, tmdb_id: int, sezonlar) -> dict:
        """
        {(sezon, bölüm): {...}} sözlüğü döndürür.

        `sezonlar` yalnızca kullanıcının elindeki sezon numaralarıdır;
        böylece 8 sezonluk bir dizide gereksiz istek yapılmaz.
        """
        harita = {}
        for s in sorted(set(sezonlar)):
            veri = self.sezon(tmdb_id, s)
            for e in (veri or {}).get("episodes", []):
                harita[(e.get("season_number"), e.get("episode_number"))] = e
        return harita

    def kisi(self, kisi_id: int):
        """Oyuncu bilgisi + filmografi."""
        if not kisi_id:
            return None
        anahtar = f"kisi|{kisi_id}"
        onb = self._oku(anahtar)
        if onb is not None:
            return onb or None
        d = self._istek(f"/person/{kisi_id}", append_to_response="combined_credits")
        if d:
            self._yaz(anahtar, d)
        return d

    # ── keşif rafları ──────────────────────────────────────────────
    def raf(self, tip: str, sayfa: int = 1):
        """
        Ana sayfa rafları.
        tip: trend | vizyon | populer | enyi | dizi_populer | turk
             | kacirdiklarin | kacirdiklarin_dizi
        """
        # ÖNBELLEK SÜRÜMÜ — anahtarın parçasıdır.
        # Sorgu ölçütü değiştiğinde (ör. vote_count eşiği) eski önbellek
        # dosyası hâlâ geçerli sayılıyor ve ESKİ sonuçlar dönüyordu.
        # Ölçüldü: dizi eşiği 500→1500 yapıldığı hâlde raf hâlâ 809 oylu
        # yapımı gösteriyordu. Sürümü artırmak tüm eski kayıtları geçersiz
        # kılar; kullanıcının elle önbellek temizlemesi gerekmez.
        anahtar = f"raf|v2|{tip}|{sayfa}|{self.dil}"
        onb = self._oku(anahtar)
        if onb is not None:
            return onb or []

        if tip == "trend":
            d = self._istek("/trending/all/week", page=sayfa)
        elif tip == "vizyon":
            d = self._istek("/movie/now_playing", page=sayfa, region="TR")
        elif tip == "populer":
            d = self._istek("/movie/popular", page=sayfa)
        elif tip == "enyi":
            d = self._istek("/movie/top_rated", page=sayfa)
        elif tip == "dizi_populer":
            d = self._istek("/tv/popular", page=sayfa)
        elif tip == "turk":
            d = self._istek("/discover/movie", page=sayfa,
                            with_original_language="tr",
                            sort_by="popularity.desc")
        elif tip == "kacirdiklarin":
            # “Bunları da İzle” rafının kaynağı: YÜKSEK PUANLI FİLMLER.
            #
            # Neden /movie/top_rated DEĞİL: TMDB'nin hazır listesi az oylu
            # yapımları da yukarı taşıyor (ölçüldü: 895 oyla 9,2 puanlı bir
            # başlık ilk sırada). Böyle bir liste “en iyiler” hissi vermez.
            # discover + vote_count.gte=1500 ile eleyince liste oturuyor
            # (ölçüldü: 8,3–8,9 aralığı, 60 filmin hepsinde afiş var).
            d = self._istek("/discover/movie", page=sayfa,
                            sort_by="vote_average.desc",
                            **{"vote_count.gte": 1500,
                               "vote_average.gte": 7.0})
        elif tip == "kacirdiklarin_dizi":
            # Dizide eşik daha da kritik: 500 oyla denenince listenin
            # başına hiç tanınmayan yapımlar geliyordu (ölçüldü: 809 oyla
            # 9,4 puanlı bir başlık 1. sırada). 1500'e çıkarınca liste
            # Breaking Bad / Arcane / Chernobyl seviyesine oturdu.
            d = self._istek("/discover/tv", page=sayfa,
                            sort_by="vote_average.desc",
                            **{"vote_count.gte": 1500,
                               "vote_average.gte": 7.0})
        else:
            return []
        sonuc = (d or {}).get("results") or []
        self._yaz(anahtar, sonuc)
        return sonuc

    def yuksek_puanli(self, sayfa_sayisi: int = 3, dizi: bool = False) -> list:
        """
        Birden çok sayfayı birleştirip yüksek puanlı yapım listesi döndürür.

        Tek sayfa 20 sonuç veriyor; “bende olmayanlar” süzgecinden sonra
        geriye çok az kalabiliyor. Bu yüzden birkaç sayfa birleştirilir.
        Ölçüldü: 3 sayfa = 60 film, 489 ms (önbelleğe alındıktan sonra ~0 ms).
        """
        tip = "kacirdiklarin_dizi" if dizi else "kacirdiklarin"
        toplam, gorulen = [], set()
        for s in range(1, max(1, sayfa_sayisi) + 1):
            for o in (self.raf(tip, s) or []):
                k = o.get("id")
                if k and k not in gorulen:
                    gorulen.add(k)
                    toplam.append(o)
        return toplam

    def benzer(self, tmdb_id: int, dizi: bool = False):
        d = self.detay(tmdb_id, dizi) or {}
        return (d.get("recommendations") or {}).get("results") or []


# ══════════════════════════════════════════════════════════════════
#  GÖRSEL İNDİRME (disk önbellekli)
# ══════════════════════════════════════════════════════════════════
def gorsel_indir(url: str, klasor: Path) -> bytes | None:
    """Görseli indirir ve diske yazar. Zaten varsa diskten okur."""
    if not url:
        return None
    import hashlib
    klasor = Path(klasor); klasor.mkdir(parents=True, exist_ok=True)
    p = klasor / (hashlib.md5(url.encode()).hexdigest() + ".img")
    if p.is_file():
        try:
            return p.read_bytes()
        except OSError:
            pass
    try:
        import requests
        r = requests.get(url, timeout=15, headers={"User-Agent": "MediaBox/1.0"})
        if r.status_code == 200 and len(r.content) > 200:
            try:
                p.write_bytes(r.content)
            except OSError:
                pass
            return r.content
    except Exception:
        pass
    return None

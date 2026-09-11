#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MediaBox — Dropbox Entegrasyonu
=================================
Kullanıcının Dropbox hesabına bir kez bağlanıp, hesaptaki tüm .m3u /
.m3u8 dosyalarını otomatik bulur ve içeriklerini indirir.

NEDEN "APP KEY" GEREKİYOR?
    Dropbox, her uygulamanın kendi App Console'da (dropbox.com/developers/apps)
    kayıtlı bir "App key"e (client_id) sahip olmasını zorunlu kılar — bu,
    kullanıcı adı/şifre değildir, yalnızca "bu bağlantı MediaBox'tan geliyor"
    diyen bir kimliktir. Anthropic/Claude bu anahtarı MediaBox için önceden
    kayıt ettiremez; her kullanıcı kendi ücretsiz App key'ini almalıdır
    (KURULUM notuna bakın).

NEDEN YEREL SUNUCU/REDIRECT YOK?
    Basit OAuth2 "authorization code" akışında Dropbox, redirect_uri
    verilmezse doğrulama kodunu SAYFANIN ÜZERİNDE gösterir; kullanıcı bu
    kodu kopyalayıp programa yapıştırır. Bu, yerel bir HTTP sunucusu açıp
    port çakışması/firewall sorunlarıyla uğraşmaktan çok daha güvenilir.

TOKEN YÖNETİMİ
    Dropbox'ın verdiği "access token" birkaç saat sonra geçersiz olur.
    Bu yüzden `token_access_type=offline` istenir; bu bir de kalıcı bir
    "refresh token" verir. Yalnızca refresh_token diske kaydedilir; her
    kullanımda ondan taze bir access_token üretilir.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import time
import webbrowser
from pathlib import Path

YETKI_URL = "https://www.dropbox.com/oauth2/authorize"
TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"
API_TABAN = "https://api.dropboxapi.com/2"
ICERIK_TABAN = "https://content.dropboxapi.com/2"

M3U_UZANTILARI = (".m3u", ".m3u8")


class DropboxHata(Exception):
    pass


# ══════════════════════════════════════════════════════════════════
#  OAuth2 — PKCE (client secret gerekmez)
# ══════════════════════════════════════════════════════════════════
def _pkce_uret() -> tuple[str, str]:
    """(code_verifier, code_challenge) üretir."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(40)).rstrip(b"=").decode()
    sindirim = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(sindirim).rstrip(b"=").decode()
    return verifier, challenge


def yetkilendirme_baslat(app_key: str) -> tuple[str, str]:
    """
    Tarayıcıda Dropbox onay sayfasını açar.
    Döner: (code_verifier, yetki_url) — verifier'ı sonraki adım için sakla.
    """
    app_key = (app_key or "").strip()
    if not app_key:
        raise DropboxHata("App key boş olamaz")
    verifier, challenge = _pkce_uret()
    url = (f"{YETKI_URL}?client_id={app_key}&response_type=code"
           f"&token_access_type=offline"
           f"&code_challenge={challenge}&code_challenge_method=S256")
    webbrowser.open(url)
    return verifier, url


def kod_ile_token_al(app_key: str, verifier: str, kod: str) -> dict:
    """
    Kullanıcının yapıştırdığı kodu access_token + refresh_token'a çevirir.
    Döner: {"access_token":…, "refresh_token":…, "expires_in":…}
    """
    import requests
    kod = (kod or "").strip()
    if not kod:
        raise DropboxHata("Kod boş olamaz")
    r = requests.post(TOKEN_URL, data={
        "code": kod, "grant_type": "authorization_code",
        "client_id": app_key, "code_verifier": verifier,
    }, timeout=20)
    if r.status_code != 200:
        raise DropboxHata(f"Kod doğrulanamadı (HTTP {r.status_code}): {r.text[:200]}")
    return r.json()


def erisim_tokeni_yenile(app_key: str, refresh_token: str) -> str:
    """refresh_token'dan taze bir access_token üretir."""
    import requests
    r = requests.post(TOKEN_URL, data={
        "grant_type": "refresh_token", "refresh_token": refresh_token,
        "client_id": app_key,
    }, timeout=20)
    if r.status_code != 200:
        raise DropboxHata(f"Erişim tokeni yenilenemedi (HTTP {r.status_code}): {r.text[:200]}")
    return r.json()["access_token"]


# ══════════════════════════════════════════════════════════════════
#  Dosya tarama / indirme
# ══════════════════════════════════════════════════════════════════
def m3u_dosyalarini_bul(access_token: str) -> list[dict]:
    """
    Hesaptaki TÜM klasörleri tarar, .m3u/.m3u8 dosyalarını döndürür.
    [{"ad":…, "yol":…, "boyut":…}]
    """
    import requests
    basl = {"Authorization": f"Bearer {access_token}"}
    bulunan, imleç = [], None
    while True:
        if imleç is None:
            r = requests.post(f"{API_TABAN}/files/list_folder", headers=basl,
                              json={"path": "", "recursive": True,
                                    "include_media_info": False}, timeout=30)
        else:
            r = requests.post(f"{API_TABAN}/files/list_folder/continue",
                              headers=basl, json={"cursor": imleç}, timeout=30)
        if r.status_code != 200:
            raise DropboxHata(f"Dosyalar listelenemedi (HTTP {r.status_code}): {r.text[:200]}")
        d = r.json()
        for g in d.get("entries", []):
            if g.get(".tag") != "file":
                continue
            ad = g.get("name", "")
            if ad.lower().endswith(M3U_UZANTILARI):
                bulunan.append({"ad": ad, "yol": g.get("path_lower", ""),
                                "boyut": g.get("size", 0)})
        if not d.get("has_more"):
            break
        imleç = d.get("cursor")
    return bulunan


def dosya_metni_al(access_token: str, yol: str) -> str:
    """Bir Dropbox dosyasının içeriğini metin olarak indirir."""
    import requests
    basl = {"Authorization": f"Bearer {access_token}",
            "Dropbox-API-Arg": json.dumps({"path": yol})}
    r = requests.post(f"{ICERIK_TABAN}/files/download", headers=basl, timeout=60)
    if r.status_code != 200:
        raise DropboxHata(f"'{yol}' indirilemedi (HTTP {r.status_code})")
    return r.content.decode("utf-8", "ignore")


def hesap_adi_al(access_token: str) -> str:
    """Bağlı hesabın görünen adını döndürür (kullanıcıya onay göstermek için)."""
    import requests
    r = requests.post(f"{API_TABAN}/users/get_current_account",
                      headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
    if r.status_code != 200:
        return ""
    return ((r.json().get("name") or {}).get("display_name") or "")


def kategori_tahmin_dosya_adindan(dosya_adi: str) -> str | None:
    """
    Dropbox'taki M3U dosyasının ADINA bakarak kategori tahmini yapar.

    NEDEN GEREKLİ: `m3u_ayristir()`'in kendi satır-bazlı tahmini
    (`kategori_tahmin`) yalnızca her satırın ad/grup metnine bakıyor;
    bir dizi bölümünün adında "Sezon/Bölüm" gibi net bir ipucu yoksa
    yanlışlıkla "film" sayılabiliyor (ölçülen şikayet: "filmler ve
    diziler bölümü birbirine girdi"). Oysa kullanıcılar genelde
    dosyalarını zaten türe göre ayırıyor (ör. "Diziler.m3u",
    "Filmler.m3u", "TG_Diziler.m3u"). Dosya adı net bir tür belirtiyorsa
    o dosyadaki TÜM içerik satır satır tahmine bırakılmadan doğrudan bu
    kategoriye atanır — çok daha güvenilir.
    """
    ad = (dosya_adi or "").lower()
    if any(k in ad for k in ("dizi", "diziler", "series", "show")):
        return "series"
    if any(k in ad for k in ("anime", "animasyon")):
        return "anime"
    if any(k in ad for k in ("canlı", "canli", "live", "tv kanal", "kanallar")):
        return "live"
    if any(k in ad for k in ("film", "filmler", "movie", "sinema")):
        return "movie"
    return None      # ipucu yok: satır bazlı otomatik tahmine bırak


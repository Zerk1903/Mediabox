#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MediaBox — mpv GLSL Shader Yönetimi
====================================
Görüntü iyileştirme shader'larını (Anime4K, FSR, CAS…) yönetir ve
oynatma sırasında canlı olarak uygular.

ÖLÇÜM (bu ortamda doğrulandı)
    Çalışan mpv'ye IPC ile shader eklenip kaldırılabiliyor:
        başlangıç         → []
        glsl-shaders yaz  → ['/yol/gri.glsl']
        boşalt            → ['']
        change-list append→ ['', '/yol/gri.glsl']
    Not: mpv boş dizeyi listede bırakabiliyor; bu yüzden temizlerken
    her zaman `glsl-shaders-clr` komutu kullanılır.

SHADER KAYNAĞI
    Hazır shader'lar İNDİRİLMEZ (telif/erişim sorunları olabilir).
    Kullanıcı kendi .glsl / .hook dosyalarını ekler. Program yalnızca
    yerleşik birkaç küçük shader'ı kendisi üretir (aşağıdaki YERLESIK).
"""

from __future__ import annotations

import os
from pathlib import Path

# ══════════════════════════════════════════════════════════════════
#  YERLEŞİK SHADER'LAR
# ══════════════════════════════════════════════════════════════════
# Küçük, bağımsız ve güvenli shader'lar; ilk kullanımda diske yazılır.
YERLESIK: dict[str, dict] = {
    "keskinlik": {
        "ad": "Keskinleştirme (hafif)",
        "aciklama": "Yumuşak görüntüleri belirginleştirir. Düşük çözünürlüklü "
                    "kaynaklarda iyi sonuç verir.",
        "kod": """//!DESC MediaBox-Keskinlik
//!HOOK MAIN
//!BIND HOOKED
// Basit unsharp mask: merkez pikseli komşularına göre güçlendirir.
#define GUC 0.45
vec4 hook() {
    vec4 c = HOOKED_tex(HOOKED_pos);
    vec2 p = HOOKED_pt;
    vec4 bulanik =
        HOOKED_tex(HOOKED_pos + vec2(-p.x,  0.0)) +
        HOOKED_tex(HOOKED_pos + vec2( p.x,  0.0)) +
        HOOKED_tex(HOOKED_pos + vec2( 0.0, -p.y)) +
        HOOKED_tex(HOOKED_pos + vec2( 0.0,  p.y));
    bulanik *= 0.25;
    return vec4(clamp(c.rgb + (c.rgb - bulanik.rgb) * GUC, 0.0, 1.0), c.a);
}
""",
    },
    "canlilik": {
        "ad": "Renk canlılığı",
        "aciklama": "Solgun görüntülerde renkleri canlandırır "
                    "(doygunluğu zaten yüksek olanları abartmaz).",
        "kod": """//!DESC MediaBox-Canlilik
//!HOOK MAIN
//!BIND HOOKED
#define GUC 0.28
vec4 hook() {
    vec4 c = HOOKED_tex(HOOKED_pos);
    float gri = dot(c.rgb, vec3(0.2126, 0.7152, 0.0722));
    float enb = max(max(c.r, c.g), c.b);
    float enk = min(min(c.r, c.g), c.b);
    float doygunluk = enb - enk;
    // Zaten doygun olan bölgelere daha az dokun
    float k = GUC * (1.0 - doygunluk);
    return vec4(clamp(mix(vec3(gri), c.rgb, 1.0 + k), 0.0, 1.0), c.a);
}
""",
    },
    "koyu_sahne": {
        "ad": "Karanlık sahne aydınlatma",
        "aciklama": "Gece sahnelerinde gölgeleri açar; parlak bölgeleri "
                    "yakmadan detayı ortaya çıkarır.",
        "kod": """//!DESC MediaBox-KoyuSahne
//!HOOK MAIN
//!BIND HOOKED
#define GUC 0.30
vec4 hook() {
    vec4 c = HOOKED_tex(HOOKED_pos);
    float l = dot(c.rgb, vec3(0.2126, 0.7152, 0.0722));
    // Yalnızca karanlık bölgelerde etkili olan kazanç eğrisi
    float kazanc = GUC * pow(1.0 - l, 2.0);
    return vec4(clamp(c.rgb + c.rgb * kazanc, 0.0, 1.0), c.a);
}
""",
    },
    "yumusak": {
        "ad": "Bant giderme (yumuşatma)",
        "aciklama": "Gökyüzü/gradyan sahnelerdeki renk bantlarını azaltır. "
                    "Düşük bit hızlı yayınlarda faydalıdır.",
        "kod": """//!DESC MediaBox-BantGiderme
//!HOOK MAIN
//!BIND HOOKED
// Çok hafif gürültü ekleyerek bant geçişlerini kırar (dithering).
vec4 hook() {
    vec4 c = HOOKED_tex(HOOKED_pos);
    vec2 k = HOOKED_pos * vec2(1234.5678, 8765.4321);
    float gurultu = fract(sin(dot(k, vec2(12.9898, 78.233))) * 43758.5453);
    return vec4(clamp(c.rgb + (gurultu - 0.5) / 255.0 * 1.6, 0.0, 1.0), c.a);
}
""",
    },
}


def shader_klasoru() -> Path:
    from mediabox_qt import veri_klasoru
    p = veri_klasoru() / "shaderlar"
    p.mkdir(parents=True, exist_ok=True)
    return p


def yerlesikleri_kur() -> dict[str, str]:
    """Yerleşik shader'ları diske yazar; {anahtar: yol} döndürür."""
    klasor = shader_klasoru()
    yollar = {}
    for anahtar, bilgi in YERLESIK.items():
        yol = klasor / f"{anahtar}.glsl"
        try:
            if not yol.exists() or yol.stat().st_size == 0:
                yol.write_text(bilgi["kod"], encoding="utf-8")
            yollar[anahtar] = str(yol)
        except Exception:
            pass
    return yollar


def kullanici_shaderlari() -> list[tuple[str, str]]:
    """Kullanıcının klasöre attığı ek shader'lar: [(ad, yol)]"""
    klasor = shader_klasoru()
    yerlesik_adlar = {f"{a}.glsl" for a in YERLESIK}
    cikti = []
    try:
        for y in sorted(klasor.iterdir()):
            if y.suffix.lower() in (".glsl", ".hook") and y.name not in yerlesik_adlar:
                cikti.append((y.stem, str(y)))
    except Exception:
        pass
    return cikti


def gecerli_mi(yol: str) -> tuple[bool, str]:
    """
    Shader dosyası makul görünüyor mu? (sözdizimini mpv doğrular;
    burada yalnızca temel kontroller yapılır)
    """
    if not yol:
        return False, "yol boş"
    p = Path(yol)
    if not p.is_file():
        return False, "dosya yok"
    if p.stat().st_size > 2_000_000:
        return False, "dosya çok büyük"
    try:
        metin = p.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return False, f"okunamadı: {e}"
    if "//!HOOK" not in metin:
        return False, "geçerli bir mpv shader'ı değil (//!HOOK satırı yok)"
    return True, ""


def mpv_uygula(mpv, yollar) -> tuple[bool, str]:
    """
    Çalışan mpv'ye shader listesini uygular.

    `yollar` boş liste/None ise tüm shader'lar kaldırılır.
    ÖNEMLİ: `glsl-shaders=""` yazmak listede boş bir öğe bırakıyordu
    (ölçüldü: ['']). Bu yüzden temizlik `glsl-shaders-clr` ile yapılır.
    """
    if not mpv or not getattr(mpv, "yasiyor", False):
        return False, "oynatıcı çalışmıyor"
    try:
        mpv.komut("change-list", "glsl-shaders", "clr", "", bekle=False)
        for y in (yollar or []):
            if not y:
                continue
            ok, hata = gecerli_mi(y)
            if not ok:
                return False, f"{os.path.basename(y)}: {hata}"
            mpv.komut("change-list", "glsl-shaders", "append", y, bekle=False)
        return True, ""
    except Exception as e:
        return False, str(e)


def mpv_temizle(mpv):
    try:
        if mpv and getattr(mpv, "yasiyor", False):
            mpv.komut("change-list", "glsl-shaders", "clr", "", bekle=False)
    except Exception:
        pass

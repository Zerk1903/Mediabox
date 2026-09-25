import sys
import os
import re
import json
import time
import hashlib
import tempfile
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from PyQt6.QtWidgets import (QApplication, QMainWindow, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget, QMenu, QHeaderView,
                             QFileDialog, QMessageBox, QLineEdit, QAbstractItemView,
                             QInputDialog, QDialog, QLabel, QHBoxLayout, QPushButton,
                             QProgressDialog, QComboBox, QScrollArea, QCheckBox, QGroupBox,
                             QFormLayout, QFrame, QRadioButton, QButtonGroup, QSizePolicy)
from PyQt6.QtGui import QColor, QPixmap, QIcon, QShortcut, QKeySequence
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize

# =====================================================================
#  TMDB API ANAHTARINIZI BURAYA GİRİN
# =====================================================================
TMDB_API_KEY = "a7403e3d62a41bf85e1e53b9df4d684f"
TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w200"
# =====================================================================

# =====================================================================
#  EXTINF yardımcıları
# =====================================================================
def _extinf_name(line: str) -> str:
    """
    #EXTINF satırından içerik adını çıkarır.

    Ad, TIRNAK DIŞINDAKİ ilk virgülden sonra gelen kısımdır. Öznitelik
    değerleri (group-title, tvg-logo ...) virgül içerebilir; naif bir
    split(",", 1) bu durumda adı yanlış yerden böler.

        #EXTINF:-1 group-title="Love, Death & Robots",Love, Death & Robots S01E01
                                     ^ burada bölmemeli      ^ ad buradan başlar
    """
    in_quotes = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == "," and not in_quotes:
            return line[i + 1:].strip()
    return ""


def _norm_url(url: str) -> str:
    """
    URL'yi karşılaştırma için normalleştirir.

    Amaç: gerçekten AYNI adresi yakalamak, farklı kaynakları değil.
    - baştaki/sondaki boşluklar atılır
    - şema ve alan adı küçük harfe indirilir (yol kısmı büyük/küçük duyarlı kalır)
    - sondaki '/' yok sayılır
    Sorgu parametreleri KORUNUR; token/kalite farkı olan adresler ayrı sayılır.
    """
    u = (url or "").strip()
    if not u:
        return ""
    m = re.match(r'^([A-Za-z][A-Za-z0-9+.\-]*://)([^/?#]*)(.*)$', u, flags=re.DOTALL)
    if m:
        scheme, host, rest = m.group(1).lower(), m.group(2).lower(), m.group(3)
        u = scheme + host + rest
    while u.endswith("/"):
        u = u[:-1]
    return u


def _m3u_attr(value: str) -> str:
    """
    Öznitelik değerini M3U için güvenli hale getirir.

    Çift tırnak öznitelik sınırlayıcısıdır; değerin içinde kalırsa satır
    bozulur ve okuma sırasında ad/grup karışır. Virgül SORUN DEĞİLDİR
    (tırnak içinde kalır), bu yüzden dokunulmaz.
    """
    return str(value or "").replace('"', "'").replace("\n", " ").replace("\r", " ")


# Sütun indeksleri (kolay referans)
COL_CHECK  = 0   # ✔ checkbox sütunu
COL_LOGO   = 1
COL_GROUP  = 2
COL_NAME   = 3
COL_YEAR   = 4
COL_TMDBID = 5
COL_URL    = 6
COL_UA     = 7   # User-Agent (EXTVLCOPT:http-user-agent=)
COL_REF    = 8   # Referer   (EXTVLCOPT:http-referrer=)
COL_STATUS = 9

# Sık kullanılan User-Agent ön tanımları (etiket, değer)
UA_PRESETS = [
    ("VLC (varsayılan)",        "VLC/3.0.18 LibVLC/3.0.18"),
    ("Android ExoPlayer",       "ExoPlayerLib/2.15.1"),
    ("Chrome (Windows)",        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    ("Android (Mobil)",         "Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"),
    ("IPTV Smarters",           "IPTVSmartersPlayer"),
    ("okhttp (Android app)",    "okhttp/4.9.0"),
]

# TMDB tür ID → Türkçe karşılıkları
GENRE_MAP = {
    28: "Aksiyon", 12: "Macera", 16: "Animasyon", 35: "Komedi",
    80: "Suç", 99: "Belgesel", 18: "Drama", 10751: "Aile",
    14: "Fantezi", 36: "Tarih", 27: "Korku", 10402: "Müzik",
    9648: "Gizem", 10749: "Romantik", 878: "Bilim Kurgu",
    10770: "TV Film", 53: "Gerilim", 10752: "Savaş", 37: "Western",
    # TV türleri
    10759: "Aksiyon & Macera", 10762: "Çocuk", 10763: "Haberler",
    10764: "Reality", 10765: "Sci-Fi & Fantezi", 10766: "Pembe Dizi",
    10767: "Talk Show", 10768: "Savaş & Politika",
}


def clean_name(name: str) -> str:
    """
    Kanal/içerik adından teknik etiketleri ve bölüm bilgilerini temizler.
    Japonca/Çince/Kiril gibi Latin dışı karakterler korunur (TMDB araması için).
    """
    # S06-E10 / S01E01 / S01 E01 gibi bir kalıp varsa, ondan ÖNCE gelen kısmı al
    m = re.search(r'\bS\d{1,2}[\s\-]*E\d{1,2}\b', name, flags=re.IGNORECASE)
    if m:
        name = name[:m.start()]

    # Sezon/bölüm kalıpları: 1x05 vb.
    name = re.sub(r'\b\d{1,2}x\d{1,2}\b', '', name, flags=re.IGNORECASE)
    # Türkçe bölüm formatları: "1.Bölüm", "2. Sezon", "Sezon 1", "Bölüm 3"
    name = re.sub(r'\b\d+\.\s*(Bölüm|Sezon)\b', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\b(Bölüm|Sezon|Episode|Season)\s*\d+\b', '', name, flags=re.IGNORECASE)
    # Teknik etiketler
    name = re.sub(
        r'\b(HD|FHD|UHD|4K|SD|TR|EN|DE|HEVC|H264|H265|AAC|\d{3,4}p|x265|x264|BluRay|WEBRip|HDTV)\b',
        '', name, flags=re.IGNORECASE
    )
    # Nokta/alt çizgi dönüşümü — sadece Latin alfabesiyse yap (Japonca bozmamak için)
    latin_ratio = sum(1 for c in name if c.isascii() and c.isalpha()) / max(len([c for c in name if c.isalpha()]), 1)
    if latin_ratio > 0.7:
        name = re.sub(r'[._]', ' ', name)
    # Parantez içi yıl: (2023) veya [2023]
    name = re.sub(r'[\[\(]\d{4}[\]\)]', '', name)
    # Sonda kalan yılı sil
    name = re.sub(r'\b(19|20)\d{2}\b', '', name)
    # Birden fazla boşluğu tek boşluğa indir
    name = re.sub(r'\s{2,}', ' ', name)
    return name.strip(" .-_|")


# =====================================================================
#  SEZON/BÖLÜM BİÇİMİ NORMALLEŞTİRME
#  "Meghan 1. Sezon 1. Bölüm (2022)"  ->  "Meghan S01E01 (2022)"
#  Yalnızca sezon/bölüm ifadesi değişir; dizi adı ve yıl korunur.
# =====================================================================

# Desteklenen kalıplar (öncelik sırasıyla denenir).
# Her kalıp (sezon, bölüm) gruplarını yakalar.
_SE_PATTERNS = [
    # 1. Sezon 2. Bölüm  |  1.Sezon 2.Bolum  |  1 Sezon 2 Bölüm
    (re.compile(r'(?<!\d)(\d{1,3})\s*\.?\s*sezon\s*[-–—,:]?\s*(\d{1,3})\s*\.?\s*b[oö]l[uü]m(?!\w)',
                re.IGNORECASE), 'sezon_bolum'),
    # Sezon 1 Bölüm 2
    (re.compile(r'sezon\s*(\d{1,3})\s*[-–—,:]?\s*b[oö]l[uü]m\s*(\d{1,3})(?!\w)',
                re.IGNORECASE), 'sezon_bolum'),
    # Season 1 Episode 2
    (re.compile(r'season\s*(\d{1,3})\s*[-–—,:]?\s*episode\s*(\d{1,3})(?!\w)',
                re.IGNORECASE), 'sezon_bolum'),
    # 1. Season 2. Episode
    (re.compile(r'(?<!\d)(\d{1,3})\s*\.?\s*season\s*[-–—,:]?\s*(\d{1,3})\s*\.?\s*episode(?!\w)',
                re.IGNORECASE), 'sezon_bolum'),
    # 1x02  (yaygın kısa biçim)
    (re.compile(r'(?<![\w])(\d{1,3})\s*x\s*(\d{1,3})(?![\w])',
                re.IGNORECASE), 'sezon_bolum'),
]

# Yalnızca bölüm bilgisi olanlar (sezon yok) -> S01 varsayılır
_EP_ONLY_PATTERNS = [
    re.compile(r'(?<!\d)(\d{1,3})\s*\.?\s*b[oö]l[uü]m(?!\w)', re.IGNORECASE),
    re.compile(r'b[oö]l[uü]m\s*(\d{1,3})(?!\w)',              re.IGNORECASE),
    re.compile(r'episode\s*(\d{1,3})(?!\w)',                  re.IGNORECASE),
    re.compile(r'(?<!\d)(\d{1,3})\s*\.?\s*episode(?!\w)',     re.IGNORECASE),
]

# Zaten doğru biçimde mi?  (S01E01 / S1E1 / s01 e01)
_ALREADY_SE = re.compile(r'\bS\d{1,3}\s*[-_ ]?\s*E\d{1,3}\b', re.IGNORECASE)


def normalize_season_episode(name: str, default_season: int = 1):
    """
    Sezon/bölüm ifadesini S01E01 biçimine çevirir.

    Döner: (yeni_ad, degisti_mi)

    - "Meghan 1. Sezon 1. Bölüm (2022)" -> "Meghan S01E01 (2022)"
    - "Dizi Sezon 2 Bölüm 10"           -> "Dizi S02E10"
    - "Show 1x05"                       -> "Show S01E05"
    - "Dizi 7. Bölüm"                   -> "Dizi S01E07"   (sezon yoksa varsayılan)
    - Zaten "S01E01" ise dokunmaz.
    """
    if not name:
        return name, False
    original = name

    # Zaten doğru biçimdeyse karışma
    if _ALREADY_SE.search(name):
        return name, False

    season = episode = None
    for rx, _kind in _SE_PATTERNS:
        m = rx.search(name)
        if m:
            try:
                season  = int(m.group(1))
                episode = int(m.group(2))
            except (ValueError, IndexError):
                continue
            name = name[:m.start()] + '\x00' + name[m.end():]
            break

    if season is None:
        # Sezon yok, sadece bölüm var mı?
        for rx in _EP_ONLY_PATTERNS:
            m = rx.search(name)
            if m:
                try:
                    episode = int(m.group(1))
                except (ValueError, IndexError):
                    continue
                season = default_season
                name = name[:m.start()] + '\x00' + name[m.end():]
                break

    if season is None or episode is None:
        return original, False

    # Mantıksız değerleri reddet (yıl vb. yanlış yakalanmasın)
    if not (0 <= season <= 99) or not (0 <= episode <= 999):
        return original, False

    tag = 'S%02dE%02d' % (season, episode)
    name = name.replace('\x00', tag, 1)

    # Etiket çevresindeki artık ayraçları temizle
    name = re.sub(r'\s*[-–—|:]\s*(?=' + tag + r')', ' ', name)
    name = re.sub(r'(?<=' + tag + r')\s*[-–—|:]\s*', ' ', name)
    name = re.sub(r'\s{2,}', ' ', name).strip(' .-_|')

    return name, (name != original)


def is_latin(text: str) -> bool:
    """Metnin büyük çoğunluğu Latin alfabesiyse True döner."""
    if not text:
        return False
    latin_count = sum(1 for c in text if c.isascii() or '\u00C0' <= c <= '\u024F')
    return latin_count / len(text) > 0.6


def _detect_search_langs(q: str) -> list:
    """
    Sorgunun karakterlerine göre hangi TMDB arama dillerini deneyeceğimizi belirler.
    Her zaman en-US dahil edilir (TMDB'de en geniş kapsam).
    """
    langs = []
    for ch in q:
        cp = ord(ch)
        # Çince (CJK Unified)
        if 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF:
            if "zh-CN" not in langs: langs.append("zh-CN")
            if "zh-TW" not in langs: langs.append("zh-TW")
        # Japonca (Hiragana / Katakana)
        elif 0x3040 <= cp <= 0x30FF:
            if "ja-JP" not in langs: langs.append("ja-JP")
        # Korece
        elif 0xAC00 <= cp <= 0xD7AF or 0x1100 <= cp <= 0x11FF:
            if "ko-KR" not in langs: langs.append("ko-KR")
        # Kiril
        elif 0x0400 <= cp <= 0x04FF:
            if "ru-RU" not in langs: langs.append("ru-RU")
        # Arapça / Farsça
        elif 0x0600 <= cp <= 0x06FF:
            if "ar-AE" not in langs: langs.append("ar-AE")
    # en-US her zaman son sıraya ekle (fallback)
    if "en-US" not in langs:
        langs.append("en-US")
    return langs


def _tmdb_get_tr_name(tmdb_id: str, media_type: str, en_fallback: str) -> str:
    """
    Verilen TMDB ID için önce Türkçe adı çeker.
    Türkçe ad Latin değilse İngilizce ada döner.
    """
    try:
        r = requests.get(
            f"{TMDB_BASE_URL}/{media_type}/{tmdb_id}",
            params={"api_key": TMDB_API_KEY, "language": "tr-TR"},
            timeout=6
        )
        data = r.json()
        tr = data.get("title") or data.get("name") or ""
        if tr and is_latin(tr):
            return tr
    except Exception:
        pass
    return en_fallback


def tmdb_fetch(name: str):
    """
    TMDB'de arama yapar.

    Akış:
    1. Addan yıl bilgisini çıkar (arama doğruluğu için).
    2. Ad Latin ise: tr-TR ile direkt ara.
    3. Ad Latin değilse (Çince/Japonca/Kiril vb.):
       a. Orijinal dil(ler)iyle ara (zh-CN, ja-JP, ko-KR vb.)
       b. Bulunan ID ile tr-TR detay çek
       c. Türkçe yoksa → en-US ile aynı ID'nin İngilizce adını kullan
    4. Her adımda yıl filtreli dene, sonuç yoksa yılsız tekrar dene.
    5. movie önce, tv sonra (yıl varsa); yıl yoksa tv önce, movie sonra.
    """
    # ── Addan yıl çıkar ──────────────────────────────────────────────
    year_match = re.search(r'[\(\[]((19|20)\d{2})[\)\]]', name)
    search_year = year_match.group(1) if year_match else None

    q = clean_name(name)
    if not q:
        return None

    q_is_latin = is_latin(q)
    media_order = ["movie", "tv"] if search_year else ["tv", "movie"]

    # ── Yardımcı: tek bir (lang, media_type) kombinasyonu dene ───────
    def _try_search(lang: str, media_type: str, with_year: bool):
        params = {"api_key": TMDB_API_KEY, "query": q, "language": lang}
        if with_year and search_year:
            if media_type == "movie":
                params["primary_release_year"] = search_year
            else:
                params["first_air_date_year"] = search_year
        try:
            r = requests.get(
                f"{TMDB_BASE_URL}/search/{media_type}",
                params=params, timeout=6
            )
            return r.json().get("results", [])
        except Exception:
            return []

    # ── Latin ad: tr-TR ile ara ───────────────────────────────────────
    if q_is_latin:
        for media_type in media_order:
            results = _try_search("tr-TR", media_type, with_year=True)
            if not results and search_year:
                results = _try_search("tr-TR", media_type, with_year=False)
            if not results:
                continue

            item = results[0]
            tmdb_id  = str(item.get("id", ""))
            tr_name_raw = item.get("name") or item.get("title") or ""
            en_name  = tr_name_raw  # fallback

            # Türkçe sonuç Latin değilse İngilizce çek
            if not is_latin(tr_name_raw):
                try:
                    en_r = requests.get(
                        f"{TMDB_BASE_URL}/{media_type}/{tmdb_id}",
                        params={"api_key": TMDB_API_KEY, "language": "en-US"}, timeout=6
                    )
                    en_name = en_r.json().get("title") or en_r.json().get("name") or tr_name_raw
                except Exception:
                    pass
                tr_name = en_name
            else:
                tr_name = tr_name_raw

            poster_path = item.get("poster_path", "") or ""
            date_str    = item.get("first_air_date") or item.get("release_date") or ""
            genre_ids   = item.get("genre_ids", [])
            return {
                "logo":       (TMDB_IMAGE_BASE + poster_path) if poster_path else "",
                "group":      GENRE_MAP.get(genre_ids[0], "Diğer") if genre_ids else "Diğer",
                "tr_name":    tr_name,
                "year":       date_str[:4] if date_str else (search_year or ""),
                "tmdb_id":    tmdb_id,
                "media_type": media_type,
            }
        return None

    # ── Latin dışı ad: orijinal dil(ler) + en-US ile dene ────────────
    search_langs = _detect_search_langs(q)

    for media_type in media_order:
        found_item = None
        found_en_name = ""

        for lang in search_langs:
            results = _try_search(lang, media_type, with_year=True)
            if not results and search_year:
                results = _try_search(lang, media_type, with_year=False)
            if results:
                found_item = results[0]
                found_en_name = found_item.get("title") or found_item.get("name") or ""
                break  # İlk başarılı dilde bulduk

        if not found_item:
            continue

        tmdb_id     = str(found_item.get("id", ""))
        poster_path = found_item.get("poster_path", "") or ""
        date_str    = found_item.get("first_air_date") or found_item.get("release_date") or ""
        genre_ids   = found_item.get("genre_ids", [])

        # İngilizce ad: eğer bulunan ad zaten Latin değilse en-US detay çek
        if not is_latin(found_en_name):
            try:
                en_r = requests.get(
                    f"{TMDB_BASE_URL}/{media_type}/{tmdb_id}",
                    params={"api_key": TMDB_API_KEY, "language": "en-US"}, timeout=6
                )
                en_data = en_r.json()
                found_en_name = en_data.get("title") or en_data.get("name") or found_en_name
            except Exception:
                pass

        # Türkçe ad çek (Latin ise kullan, değilse İngilizce'ye dön)
        tr_name = _tmdb_get_tr_name(tmdb_id, media_type, en_fallback=found_en_name)

        return {
            "logo":       (TMDB_IMAGE_BASE + poster_path) if poster_path else "",
            "group":      GENRE_MAP.get(genre_ids[0], "Diğer") if genre_ids else "Diğer",
            "tr_name":    tr_name,
            "year":       date_str[:4] if date_str else (search_year or ""),
            "tmdb_id":    tmdb_id,
            "media_type": media_type,
        }

    return None



# =====================================================================
#  TABLO POSTER ÖNİZLEMESİ
#  Logo sütunundaki URL'yi küçük bir görsele çevirir. Yalnızca EKRANDA
#  GÖRÜNEN satırlar indirilir; sonuçlar diskte önbelleğe alınır.
# =====================================================================
THUMB_W, THUMB_H = 40, 60
THUMB_DIR = os.path.join(tempfile.gettempdir(), "m3u_editor_thumbs")


def _thumb_file(url: str) -> str:
    """URL için sabit önbellek dosya yolu."""
    h = hashlib.md5(url.encode("utf-8", "ignore")).hexdigest()
    return os.path.join(THUMB_DIR, h + ".png")


class _ThumbLoader(QThread):
    """Verilen URL listesini paralel indirir, her biri hazır olunca sinyal yollar."""
    ready = pyqtSignal(str, QPixmap)   # (url, pixmap)

    def __init__(self, urls, parent=None):
        super().__init__(parent)
        self._urls = list(urls)
        self._stop = False

    def stop(self):
        self._stop = True

    def _one(self, url):
        if self._stop:
            return None
        path = _thumb_file(url)
        # 1) Diskte var mı?
        if os.path.exists(path):
            px = QPixmap(path)
            if not px.isNull():
                return px
        # 2) İndir
        try:
            raw = requests.get(url, timeout=6).content
            px = QPixmap()
            px.loadFromData(raw)
            if px.isNull():
                return None
            px = px.scaled(THUMB_W, THUMB_H,
                           Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
            try:
                os.makedirs(THUMB_DIR, exist_ok=True)
                px.save(path, "PNG")
            except Exception:
                pass
            return px
        except Exception:
            return None

    def run(self):
        if not self._urls:
            return
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = {ex.submit(self._one, u): u for u in self._urls}
            for fut in as_completed(futs):
                if self._stop:
                    break
                url = futs[fut]
                try:
                    px = fut.result()
                except Exception:
                    px = None
                if px is not None and not px.isNull():
                    self.ready.emit(url, px)


class _PosterWorker(QThread):
    """Poster görselini arka planda indirir."""
    done = pyqtSignal(QPixmap)

    def __init__(self, url: str):
        super().__init__()
        self._url = url

    def run(self):
        try:
            raw = requests.get(self._url, timeout=5).content
            px  = QPixmap()
            px.loadFromData(raw)
            if not px.isNull():
                self.done.emit(px)
            else:
                self.done.emit(QPixmap())
        except Exception:
            self.done.emit(QPixmap())


# ──────────────────────────────────────────────────────────────────────
#  ARKA PLAN İŞÇİSİ
# ──────────────────────────────────────────────────────────────────────
class TMDBWorker(QThread):
    result_ready = pyqtSignal(int, dict)   # row, data_dict
    not_found    = pyqtSignal(int)         # row — TMDB'de bulunamadı
    progress     = pyqtSignal(int, str)    # done_count, current_name
    finished     = pyqtSignal(int, int)    # success, fail

    # TMDB API rate-limit güvenli paralel thread sayısı
    MAX_WORKERS = 10

    def __init__(self, rows_data, options):
        """
        rows_data : [(row_index, kanal_adı), ...]
        options   : dict — hangi alanlar güncellensin
        """
        super().__init__()
        self.rows_data = rows_data
        self.options   = options
        self._stop     = False
        self._executor = None

    def stop(self):
        self._stop = True
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=True)

    def run(self):
        success = fail = 0
        done_lock = __import__('threading').Lock()
        done_count = [0]  # mutable counter for threads

        def fetch_one(row_index, channel_name):
            if self._stop:
                return
            data = tmdb_fetch(channel_name)
            with done_lock:
                done_count[0] += 1
                current_done = done_count[0]
            self.progress.emit(current_done, channel_name)
            return row_index, channel_name, data

        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            self._executor = executor
            futures = {
                executor.submit(fetch_one, row_index, channel_name): (row_index, channel_name)
                for row_index, channel_name in self.rows_data
            }
            for future in as_completed(futures):
                if self._stop:
                    break
                try:
                    result = future.result()
                    if result is None:
                        continue
                    row_index, channel_name, data = result
                    if data:
                        self.result_ready.emit(row_index, data)
                        success += 1
                    else:
                        self.not_found.emit(row_index)
                        fail += 1
                except Exception:
                    fail += 1

        self.finished.emit(success, fail)


# ──────────────────────────────────────────────────────────────────────
#  MANUEL TMDB ARAMA PENCERESİ  (tüm bilgileri seçebilir)
# ──────────────────────────────────────────────────────────────────────
class TMDBSearchDialog(QDialog):
    def __init__(self, parent=None, channel_name="", bulk_count=0):
        """
        bulk_count > 1 ise toplu mod: başlıkta kaç satıra uygulanacağı gösterilir
        ve bilgi bandı eklenir.
        """
        super().__init__(parent)
        self._bulk_count = bulk_count
        title = (f"TMDB'de Ara — {bulk_count} Satıra Uygula" if bulk_count > 1
                 else "TMDB'de Ara & Uygula")
        self.setWindowTitle(title)
        self.setMinimumSize(750, 520 if bulk_count > 1 else 480)
        self.is_dark = getattr(parent, "is_dark_mode", True)
        self.selected_data = {}

        layout = QVBoxLayout(self)

        # --- Toplu mod bilgi bandı ---
        if bulk_count > 1:
            info_bar = QLabel(
                f"ℹ️  Toplu mod aktif — seçtiğiniz TMDB sonucu <b>{bulk_count} satıra</b> uygulanacak.<br>"
                "Her satırın bölüm bilgisi (S01 E01 vb.) ayrı ayrı korunur; sadece dizi/film adı değişir."
            )
            info_bar.setTextFormat(Qt.TextFormat.RichText)
            info_bar.setWordWrap(True)
            info_bar.setStyleSheet(
                "background:#1a4a6e;color:#aed6f1;border-radius:5px;padding:8px;font-size:11px"
            )
            layout.addWidget(info_bar)

        # --- Arama satırı ---
        row1 = QHBoxLayout()
        self.search_input = QLineEdit(channel_name)
        self.type_combo   = QComboBox()
        self.type_combo.addItems(["TV Dizisi", "Film"])
        self.search_btn = QPushButton("🔍 Ara")
        self.search_btn.clicked.connect(self.do_search)
        self.search_input.returnPressed.connect(self.do_search)
        row1.addWidget(QLabel("Arama:"))
        row1.addWidget(self.search_input, 1)
        row1.addWidget(self.type_combo)
        row1.addWidget(self.search_btn)
        layout.addLayout(row1)

        # --- Sonuç kartları (kaydırmalı) ---
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(240)
        self.cards_widget = QWidget()
        self.cards_layout = QHBoxLayout(self.cards_widget)
        self.cards_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        scroll.setWidget(self.cards_widget)
        layout.addWidget(scroll)

        # --- Seçilen bilgiler ---
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine); layout.addWidget(sep)
        self.info_label = QLabel("Henüz seçim yapılmadı.")
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        # --- Butonlar ---
        btn_row = QHBoxLayout()
        ok_label   = f"✅ {bulk_count} Satıra Uygula" if bulk_count > 1 else "✅ Uygula"
        ok_btn     = QPushButton(ok_label)
        cancel_btn = QPushButton("İptal")
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self._apply_theme()

    def _apply_theme(self):
        if self.is_dark:
            self.setStyleSheet("""
                QDialog,QWidget{background:#2b2b2b;color:white}
                QLineEdit,QComboBox{background:#3c3f41;color:white;border:1px solid #555;padding:5px}
                QPushButton{background:#444;color:white;border:1px solid #666;padding:6px 14px}
                QPushButton:hover{background:#555}
                QLabel{color:white}
                QScrollArea{border:none}
            """)

    def do_search(self):
        # Eski kartları temizle
        while self.cards_layout.count():
            w = self.cards_layout.takeAt(0).widget()
            if w: w.deleteLater()

        query = self.search_input.text().strip()
        if not query:
            return

        media_type = "tv" if self.type_combo.currentIndex() == 0 else "movie"
        try:
            resp = requests.get(
                f"{TMDB_BASE_URL}/search/{media_type}",
                params={"api_key": TMDB_API_KEY, "query": query, "language": "tr-TR"},
                timeout=8
            )
            results = resp.json().get("results", [])[:8]
        except Exception as e:
            QMessageBox.critical(self, "Hata", str(e))
            return

        if not results:
            self.cards_layout.addWidget(QLabel("  Sonuç bulunamadı."))
            return

        for item in results:
            poster_path = item.get("poster_path", "")
            title_raw   = item.get("name") or item.get("title") or "?"
            date_str    = item.get("first_air_date") or item.get("release_date") or ""
            year        = date_str[:4] if date_str else ""
            genre_ids   = item.get("genre_ids", [])
            genre       = GENRE_MAP.get(genre_ids[0], "Diğer") if genre_ids else "Diğer"
            group       = genre
            tmdb_id     = str(item.get("id", ""))
            logo_url    = (TMDB_IMAGE_BASE + poster_path) if poster_path else ""
            # Eğer Türkçe ad Latin değilse (Japonca/Çince vb.), İngilizce detay çek
            if not is_latin(title_raw):
                try:
                    en_data = requests.get(
                        f"{TMDB_BASE_URL}/{media_type}/{tmdb_id}",
                        params={"api_key": TMDB_API_KEY, "language": "en-US"}, timeout=6
                    ).json()
                    en_title = en_data.get("title") or en_data.get("name") or title_raw
                    # Türkçe detay da çek
                    tr_data = requests.get(
                        f"{TMDB_BASE_URL}/{media_type}/{tmdb_id}",
                        params={"api_key": TMDB_API_KEY, "language": "tr-TR"}, timeout=6
                    ).json()
                    tr_title = tr_data.get("title") or tr_data.get("name") or ""
                    title = tr_title if (tr_title and is_latin(tr_title)) else en_title
                except Exception:
                    title = title_raw
            else:
                title = title_raw

            data = {
                "logo": logo_url, "group": group,
                "tr_name": title, "year": year,
                "tmdb_id": tmdb_id, "media_type": media_type,
            }

            card = QWidget()
            card.setFixedWidth(110)
            card.setStyleSheet("QWidget{border:2px solid #555;border-radius:4px;padding:2px}"
                               "QWidget:hover{border-color:#4a9fd4}")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(3, 3, 3, 3)
            cl.setSpacing(3)

            img = QLabel("⏳")
            img.setFixedSize(100, 148)
            img.setAlignment(Qt.AlignmentFlag.AlignCenter)
            img.setStyleSheet("border:none;background:#1a1a1a;font-size:28px")

            # Posteri arka planda indir
            if logo_url:
                self._load_poster_async(img, logo_url)

            lbl = QLabel(f"{title[:16]}{'…' if len(title)>16 else ''}\n{year}  |  {group}")
            lbl.setWordWrap(True)
            lbl.setStyleSheet("border:none;font-size:9px;color:#ccc")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            btn = QPushButton("Seç")
            btn.setStyleSheet("border:none;background:#0561a8;color:white;padding:3px;border-radius:3px")
            btn.clicked.connect(lambda _, d=data, t=title: self._select(d, t))

            cl.addWidget(img)
            cl.addWidget(lbl)
            cl.addWidget(btn)
            self.cards_layout.addWidget(card)

    def _load_poster_async(self, label: QLabel, url: str):
        """Posteri arka plan thread'inde indirir, hazır olunca QLabel'e yazar."""
        if not hasattr(self, '_poster_workers'):
            self._poster_workers = []
        worker = _PosterWorker(url)
        # Worker bitince hem posteri yaz hem listeden çıkar (GC'den koru)
        def _on_done(px, lbl=label, w=worker):
            self._set_poster(lbl, px)
            try:
                self._poster_workers.remove(w)
            except ValueError:
                pass
        worker.done.connect(_on_done)
        worker.finished.connect(worker.deleteLater)
        self._poster_workers.append(worker)
        worker.start()

    def _set_poster(self, label: QLabel, pixmap: QPixmap):
        if pixmap and not pixmap.isNull():
            label.setText("")
            label.setPixmap(pixmap.scaled(
                100, 148,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            ))
        else:
            label.setText("🎬")

    def _select(self, data, title):
        self.selected_data = data
        self.info_label.setText(
            f"✅  <b>{data['tr_name']}</b>  ({data['year']})  |  "
            f"Tür: {data['group']}  |  TMDB ID: {data['tmdb_id']}"
        )
        self.info_label.setTextFormat(Qt.TextFormat.RichText)


# ──────────────────────────────────────────────────────────────────────
#  TOPLU BUL & DEĞİŞTİR PENCERESİ
# ──────────────────────────────────────────────────────────────────────
class FindReplaceDialog(QDialog):
    """
    Regex destekli toplu bul & değiştir.
    Sütun seçimi : İçerik Adı, URL, Tür/Grup, Logo, Tümü
    Kapsam       : Tikli / Görünür / Tüm satırlar
    """

    # (etiket, col_index)  — None = tümü
    COL_CHOICES = [
        ("İçerik Adı", COL_NAME),
        ("URL",        COL_URL),
        ("Tür/Grup",   COL_GROUP),
        ("Logo URL",   COL_LOGO),
        ("User-Agent", COL_UA),
        ("Referer",    COL_REF),
        ("Tümü",       None),
    ]

    def __init__(self, parent=None, checked_count=0, visible_count=0, total_count=0):
        super().__init__(parent)
        self.setWindowTitle("Toplu Bul & Değiştir")
        self.setMinimumWidth(600)
        self.is_dark  = getattr(parent, "is_dark_mode", True)
        self._editor  = parent          # IPTVEditor referansı
        self._ready   = False           # __init__ tamamlanana kadar önizlemeyi engelle

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # ── Bul / Değiştir alanları ───────────────────────────────────
        find_box = QGroupBox("Arama & Değiştirme")
        fl = QFormLayout(find_box)
        fl.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.find_input    = QLineEdit()
        self.find_input.setPlaceholderText("Aranacak metin veya regex  (örn:  TR \\|  veya  S\\d+E\\d+)")
        self.replace_input = QLineEdit()
        self.replace_input.setPlaceholderText("Değiştirilecek metin  (boş bırakılırsa siler)")

        self.chk_regex = QCheckBox("Regex kullan")
        self.chk_case  = QCheckBox("Büyük/küçük harf duyarlı")
        opt_row = QHBoxLayout()
        opt_row.addWidget(self.chk_regex)
        opt_row.addWidget(self.chk_case)
        opt_row.addStretch()

        fl.addRow("Bul:",      self.find_input)
        fl.addRow("Değiştir:", self.replace_input)
        fl.addRow("",          opt_row)
        layout.addWidget(find_box)

        # ── Sütun seçimi ──────────────────────────────────────────────
        col_box = QGroupBox("Hangi Sütunda?")
        col_hl  = QHBoxLayout(col_box)
        self._col_group = QButtonGroup(self)
        for i, (label, _) in enumerate(self.COL_CHOICES):
            rb = QRadioButton(label)
            col_hl.addWidget(rb)
            self._col_group.addButton(rb, i)
        self._col_group.button(0).setChecked(True)
        layout.addWidget(col_box)

        # ── Kapsam seçimi ─────────────────────────────────────────────
        scope_box = QGroupBox("Hangi Satırlarda?")
        scope_hl  = QHBoxLayout(scope_box)
        self._scope_group = QButtonGroup(self)
        scopes = [
            f"Tikli ({checked_count})",
            f"Görünür ({visible_count})",
            f"Tümü ({total_count})",
        ]
        for i, lbl in enumerate(scopes):
            rb = QRadioButton(lbl)
            scope_hl.addWidget(rb)
            self._scope_group.addButton(rb, i)
        default_scope = 0 if checked_count > 0 else 1
        self._scope_group.button(default_scope).setChecked(True)
        layout.addWidget(scope_box)

        # ── Önizleme bandı ────────────────────────────────────────────
        self.preview_label = QLabel("Bul alanını doldurun…")
        self.preview_label.setWordWrap(True)
        self.preview_label.setMinimumHeight(60)
        self._set_preview_style("idle")
        layout.addWidget(self.preview_label)

        # ── Butonlar ─────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self.apply_btn = QPushButton("✅ Uygula")
        can_btn        = QPushButton("Kapat")
        self.apply_btn.clicked.connect(self._apply)
        can_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(self.apply_btn)
        btn_row.addWidget(can_btn)
        layout.addLayout(btn_row)

        self._apply_theme()

        # Sinyalleri en sona bağla — __init__ tamamlandıktan sonra
        self._ready = True
        self.find_input.textChanged.connect(self._update_preview)
        self.replace_input.textChanged.connect(self._update_preview)
        self.chk_regex.toggled.connect(self._update_preview)
        self.chk_case.toggled.connect(self._update_preview)
        # QButtonGroup.idToggled: sadece seçilen buton için tetiklenir (PyQt6)
        self._col_group.idToggled.connect(self._on_group_toggled)
        self._scope_group.idToggled.connect(self._on_group_toggled)

    def _on_group_toggled(self, _id, checked):
        """QButtonGroup radio değişiminde — sadece 'checked=True' olanı işle."""
        if checked:
            self._update_preview()

    # ── Yardımcılar ───────────────────────────────────────────────────
    def _set_preview_style(self, kind):
        styles = {
            "idle":    "background:#1a3a1a;color:#aaffaa;border-radius:4px;padding:7px;font-size:11px",
            "error":   "background:#3a1a1a;color:#ffaaaa;border-radius:4px;padding:7px;font-size:11px",
            "nomatch": "background:#3a2a1a;color:#ffcc88;border-radius:4px;padding:7px;font-size:11px",
            "match":   "background:#1a3a1a;color:#aaffaa;border-radius:4px;padding:7px;font-size:11px",
        }
        self.preview_label.setStyleSheet(styles.get(kind, styles["idle"]))

    def _get_selected_col(self):
        """Seçili sütun indexi. None = tümü."""
        return self.COL_CHOICES[self._col_group.checkedId()][1]

    def _get_target_rows(self):
        e     = self._editor
        scope = self._scope_group.checkedId()   # 0=tikli 1=görünür 2=tümü
        if scope == 0:
            return e._checked_rows()
        elif scope == 1:
            return [r for r in range(e.table.rowCount()) if not e.table.isRowHidden(r)]
        else:
            return list(range(e.table.rowCount()))

    def _get_target_cols(self):
        col = self._get_selected_col()
        return [COL_NAME, COL_URL, COL_GROUP, COL_LOGO, COL_UA, COL_REF] if col is None else [col]

    def _build_pattern(self):
        """Geçerli pattern döner, boş/hatalıysa None."""
        text = self.find_input.text()
        if not text:
            return None
        flags = 0 if self.chk_case.isChecked() else re.IGNORECASE
        try:
            src = text if self.chk_regex.isChecked() else re.escape(text)
            return re.compile(src, flags)
        except re.error:
            return None

    def _update_preview(self):
        if not self._ready:
            return
        pattern = self._build_pattern()
        if pattern is None:
            if self.find_input.text() and self.chk_regex.isChecked():
                self.preview_label.setText("⚠️  Geçersiz regex ifadesi")
                self._set_preview_style("error")
            else:
                self.preview_label.setText("Bul alanını doldurun…")
                self._set_preview_style("idle")
            return

        rows = self._get_target_rows()
        cols = self._get_target_cols()
        repl = self.replace_input.text()

        match_cells   = 0
        match_rows    = 0
        preview_lines = []

        for row in rows:
            row_hit = False
            for col in cols:
                val = self._editor._get_cell(row, col)
                if not pattern.search(val):
                    continue
                match_cells += 1
                row_hit = True
                if len(preview_lines) < 3:
                    new_val  = pattern.sub(repl, val)
                    col_name = next(l for l, c in self.COL_CHOICES if c == col) if col is not None else "?"
                    old_str  = val[:50] + ("…" if len(val) > 50 else "")
                    new_str  = new_val[:50] + ("…" if len(new_val) > 50 else "")
                    preview_lines.append(f"  [{col_name}]  {old_str}  →  {new_str}")
            if row_hit:
                match_rows += 1

        if match_rows == 0:
            self.preview_label.setText("🔍 Eşleşme bulunamadı")
            self._set_preview_style("nomatch")
        else:
            extra = f"\n  … ve {match_rows - 3} satır daha" if match_rows > 3 else ""
            txt = f"🔍 {match_rows} satır / {match_cells} hücre etkilenecek\n" \
                  + "\n".join(preview_lines) + extra
            self.preview_label.setText(txt)
            self._set_preview_style("match")

    def _apply(self):
        pattern = self._build_pattern()
        if pattern is None:
            QMessageBox.warning(self, "Uyarı", "Bul alanını doldurun veya geçerli bir regex girin.")
            return

        rows  = self._get_target_rows()
        cols  = self._get_target_cols()
        repl  = self.replace_input.text()

        changed_cells = 0
        changed_rows  = 0
        for row in rows:
            row_changed = False
            for col in cols:
                val     = self._editor._get_cell(row, col)
                new_val = pattern.sub(repl, val)
                if new_val != val:
                    self._editor._set_cell(row, col, new_val)
                    changed_cells += 1
                    row_changed   = True
            if row_changed:
                changed_rows += 1

        if changed_rows == 0:
            QMessageBox.information(self, "Sonuç", "Eşleşen içerik bulunamadı, hiçbir şey değiştirilmedi.")
        else:
            QMessageBox.information(
                self, "✅ Tamamlandı",
                f"{changed_rows} satırda {changed_cells} hücre güncellendi."
            )
            self._update_preview()

    def _apply_theme(self):
        if self.is_dark:
            self.setStyleSheet("""
                QDialog,QWidget{background:#2b2b2b;color:white}
                QGroupBox{color:white;border:1px solid #555;margin-top:6px;padding:8px}
                QGroupBox::title{subcontrol-origin:margin;left:8px;padding:0 4px}
                QLineEdit{background:#3c3f41;color:white;border:1px solid #555;padding:5px}
                QPushButton{background:#444;color:white;border:1px solid #666;padding:6px 14px}
                QPushButton:hover{background:#555}
                QCheckBox{color:white}
                QRadioButton{color:white}
                QLabel{color:white}
            """)


# ──────────────────────────────────────────────────────────────────────
#  TOPLU DÜZENLEME PENCERESİ
# ──────────────────────────────────────────────────────────────────────
class BulkEditDialog(QDialog):
    """Seçili veya tüm görünür satırlarda Tür, Logo ve İçerik Adı'nı toplu değiştirir."""

    def __init__(self, parent=None, selected_count=0, visible_count=0):
        super().__init__(parent)
        self.setWindowTitle("Toplu Düzenle")
        self.setMinimumWidth(520)
        self.is_dark = getattr(parent, "is_dark_mode", True)
        self.result  = {}   # {"col": COL_X, "value": "..."} listesi döner

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # ── Kapsam seçimi ──────────────────────────────────────────────
        scope_box = QGroupBox("Hangi Satırlara Uygulanacak?")
        scope_layout = QVBoxLayout(scope_box)
        self.rb_selected = QPushButton(f"☑  Tikli / Seçili Satırlar ({selected_count} satır)")
        self.rb_visible  = QPushButton(f"📋  Tüm Görünür Satırlar ({visible_count} satır)")
        self.rb_selected.setCheckable(True)
        self.rb_visible.setCheckable(True)
        self.rb_selected.setChecked(True)
        self._scope_btns = [self.rb_selected, self.rb_visible]
        self.rb_selected.clicked.connect(lambda: self._set_scope(0))
        self.rb_visible.clicked.connect(lambda:  self._set_scope(1))
        scope_layout.addWidget(self.rb_selected)
        scope_layout.addWidget(self.rb_visible)
        layout.addWidget(scope_box)

        # ── Alan giriş formu ───────────────────────────────────────────
        form_box = QGroupBox("Değiştirilecek Alanlar  (boş bırakılanlar değişmez)")
        form = QFormLayout(form_box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.chk_group = QCheckBox("Tür / Grup")
        self.inp_group = QLineEdit()
        self.inp_group.setPlaceholderText("örn:  Lucifer  veya  Drama")
        self.chk_group.toggled.connect(self.inp_group.setEnabled)
        self.inp_group.setEnabled(False)
        row_g = QHBoxLayout(); row_g.addWidget(self.chk_group); row_g.addWidget(self.inp_group, 1)
        form.addRow(row_g)

        self.chk_logo = QCheckBox("Logo URL")
        self.inp_logo = QLineEdit()
        self.inp_logo.setPlaceholderText("https://image.tmdb.org/…")
        self.chk_logo.toggled.connect(self.inp_logo.setEnabled)
        self.inp_logo.setEnabled(False)
        row_l = QHBoxLayout(); row_l.addWidget(self.chk_logo); row_l.addWidget(self.inp_logo, 1)
        form.addRow(row_l)

        self.chk_name = QCheckBox("İçerik Adı")
        self.inp_name = QLineEdit()
        self.inp_name.setPlaceholderText("Sadece dizi/film adı (bölüm bilgisi korunur)")
        self.chk_name.toggled.connect(self.inp_name.setEnabled)
        self.inp_name.setEnabled(False)
        # Bölüm bilgisini koru seçeneği
        self.chk_keep_ep = QCheckBox("Bölüm bilgisini koru  (S01 E01 gibi)")
        self.chk_keep_ep.setChecked(True)
        row_n = QHBoxLayout(); row_n.addWidget(self.chk_name); row_n.addWidget(self.inp_name, 1)
        form.addRow(row_n)
        form.addRow("", self.chk_keep_ep)

        layout.addWidget(form_box)

        # ── Butonlar ───────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        ok  = QPushButton("✅ Uygula")
        can = QPushButton("İptal")
        ok.clicked.connect(self._on_ok)
        can.clicked.connect(self.reject)
        btn_row.addStretch(); btn_row.addWidget(ok); btn_row.addWidget(can)
        layout.addLayout(btn_row)

        self._apply_theme()

    def _set_scope(self, idx):
        for i, btn in enumerate(self._scope_btns):
            btn.setChecked(i == idx)

    def scope(self):
        """'selected' veya 'visible' döner."""
        return "selected" if self.rb_selected.isChecked() else "visible"

    def _on_ok(self):
        fields = []
        if self.chk_group.isChecked() and self.inp_group.text().strip():
            fields.append({"col": COL_GROUP, "value": self.inp_group.text().strip()})
        if self.chk_logo.isChecked() and self.inp_logo.text().strip():
            fields.append({"col": COL_LOGO,  "value": self.inp_logo.text().strip()})
        if self.chk_name.isChecked() and self.inp_name.text().strip():
            fields.append({
                "col":      COL_NAME,
                "value":    self.inp_name.text().strip(),
                "keep_ep":  self.chk_keep_ep.isChecked(),
            })
        if not fields:
            QMessageBox.warning(self, "Uyarı", "En az bir alan seçip değer girin.")
            return
        self.result = fields
        self.accept()

    def _apply_theme(self):
        if self.is_dark:
            self.setStyleSheet("""
                QDialog,QWidget{background:#2b2b2b;color:white}
                QGroupBox{color:white;border:1px solid #555;margin-top:6px;padding:6px}
                QGroupBox::title{subcontrol-origin:margin;left:8px;padding:0 4px}
                QLineEdit{background:#3c3f41;color:white;border:1px solid #555;padding:5px}
                QPushButton{background:#444;color:white;border:1px solid #666;padding:6px 14px}
                QPushButton:hover{background:#555}
                QPushButton:checked{background:#0561a8;border-color:#0461a0}
                QCheckBox{color:white}
                QLabel{color:white}
            """)


# ──────────────────────────────────────────────────────────────────────
#  PARALEL LİNK KONTROL İŞÇİSİ
# ──────────────────────────────────────────────────────────────────────
class LinkCheckWorker(QThread):
    result_ready = pyqtSignal(int, str, object)   # row, status_text, QColor
    progress     = pyqtSignal(int, int, str)      # done, total, url
    finished     = pyqtSignal(int, int, int)      # active, dead, error

    def __init__(self, rows_urls, workers=30, timeout=8):
        """
        rows_urls : [(row_index, url), ...]
        workers   : eş zamanlı bağlantı sayısı
        timeout   : saniye cinsinden bağlantı zaman aşımı
        """
        super().__init__()
        self.rows_urls = rows_urls
        self.workers   = workers
        self.timeout   = timeout
        self._stop     = False

    def stop(self):
        self._stop = True

    def _check_one(self, row, url):
        """Tek bir linki kontrol eder; (row, status, color) döner."""
        ok_codes = {200, 206, 301, 302, 307, 308}
        player_hdr = {
            "User-Agent": "VLC/3.0.18 LibVLC/3.0.18",
            "Range": "bytes=0-1",
            "Connection": "close",
        }
        try:
            r = requests.get(url, timeout=self.timeout, headers=player_hdr,
                             allow_redirects=True, stream=True)
            r.close()
            if r.status_code in ok_codes:
                return row, "AKTİF ✅", QColor(0, 150, 0)
            else:
                return row, f"HATA {r.status_code}", QColor(150, 0, 0)
        except Exception:
            return row, "KAPALI 🛑", QColor(100, 100, 100)

    def run(self):
        total  = len(self.rows_urls)
        done   = 0
        active = dead = error = 0

        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {
                executor.submit(self._check_one, row, url): (row, url)
                for row, url in self.rows_urls
            }
            for future in as_completed(futures):
                if self._stop:
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
                row, status, color = future.result()
                done += 1
                _, url = futures[future]

                if "AKTİF" in status:
                    active += 1
                elif "KAPALI" in status:
                    dead += 1
                else:
                    error += 1

                self.result_ready.emit(row, status, color)
                self.progress.emit(done, total, url)

        self.finished.emit(active, dead, error)


# ──────────────────────────────────────────────────────────────────────
#  ANA PENCERE
# ──────────────────────────────────────────────────────────────────────
class IPTVEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pro M3U Editor v25 — TMDB Paralel Entegrasyon")
        self.setGeometry(100, 100, 1400, 780)
        self.is_dark_mode = True
        self.tmdb_worker      = None
        self.link_check_worker = None
        self._check_running   = False
        self._check_paused_at = 0

        cw = QWidget()
        self.setCentralWidget(cw)
        vl = QVBoxLayout(cw)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        # search_bar ribbon panel içinde _build_toolbar'da oluşturuluyor
        self.search_bar = None   # geçici placeholder — _build_toolbar override eder
        self._build_toolbar()   # panel vl'ye insertWidget(0,...) ile ekleniyor

        # Tablo — 10 sütun (0: checkbox, 1-9: veri)
        self.table = QTableWidget()
        self.table.setColumnCount(10)
        self.table.setHorizontalHeaderLabels(
            ["✔", "Logo (URL)", "Tür/Grup", "İçerik Adı", "Yıl", "tvg-id (TMDB)", "URL", "User-Agent", "Referer", "Durum"]
        )
        # Header'a tümünü seç/kaldır tiki
        self._all_checked = False
        self.table.horizontalHeader().sectionClicked.connect(self._header_check_toggle)

        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.AllEditTriggers)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        hh = self.table.horizontalHeader()
        hh.setMinimumSectionSize(24)
        hh.setSectionResizeMode(COL_CHECK,  QHeaderView.ResizeMode.Fixed)
        for col in range(1, 9):
            hh.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(COL_CHECK, 30)
        self._col_ratios = [None, 0.11, 0.06, 0.13, 0.03, 0.05, 0.26, 0.13, 0.13, 0.10]  # None = fixed
        # User-Agent / Referer sütunları varsayılan olarak gizli — sadece
        # gerektiğinde (🕵️ UA/Referer Göster butonu ile) açılır.
        self._ua_ref_visible = False
        self.table.setColumnHidden(COL_UA, True)
        self.table.setColumnHidden(COL_REF, True)
        self._fixing = False
        hh.sectionResized.connect(self._fix_last_col)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        vl.addWidget(self.table)

        # ── Canlı istatistik paneli (statusbar sağı) ──────────────────
        self._stat_total   = QLabel("Toplam: 0")
        self._stat_visible = QLabel("Görünür: 0")
        self._stat_checked = QLabel("Tikli: 0")
        self._stat_tmdb_ok = QLabel("TMDB ✅: 0")
        self._stat_tmdb_no = QLabel("❌: 0")
        for lbl in (self._stat_total, self._stat_visible,
                    self._stat_checked, self._stat_tmdb_ok, self._stat_tmdb_no):
            lbl.setStyleSheet("padding:0 8px;font-size:12px")
            self.statusBar().addPermanentWidget(lbl)

        sep1 = QFrame(); sep1.setFrameShape(QFrame.Shape.VLine)
        sep1.setStyleSheet("color:#666")
        self.statusBar().addPermanentWidget(sep1)

        # Tablo değişince istatistikleri güncelle
        self.table.itemChanged.connect(self._schedule_stats)
        self.table.model().rowsInserted.connect(self._schedule_stats)
        self.table.model().rowsRemoved.connect(self._schedule_stats)
        self._stats_timer = QTimer(self)
        self._stats_timer.setSingleShot(True)
        self._stats_timer.setInterval(150)   # 150 ms debounce
        self._stats_timer.timeout.connect(self._update_stats)

        # ── Geri alma / otomatik kaydetme / sürükle-bırak ──
        self._init_undo()
        self._init_autosave()
        self._init_thumbs()
        self.table.setIconSize(QSize(THUMB_W, THUMB_H))
        self.table.verticalHeader().setDefaultSectionSize(THUMB_H + 6)
        self.setAcceptDrops(True)

        # Kısayollar
        QShortcut(QKeySequence.StandardKey.Undo, self, activated=self.undo_action)
        QShortcut(QKeySequence.StandardKey.Redo, self, activated=self.redo_action)
        QShortcut(QKeySequence("Ctrl+Y"),        self, activated=self.redo_action)
        QShortcut(QKeySequence("Ctrl+S"),        self, activated=self.save_m3u)

        self._apply_theme()
        # Açılışta kurtarılmamış çalışma varsa sor (pencere göründükten sonra)
        QTimer.singleShot(400, self._offer_recovery)
        QTimer.singleShot(900, self._load_visible_thumbs)
        if TMDB_API_KEY == "BURAYA_API_KEYINIZI_YAZIN":
            self.statusBar().showMessage(
                "⚠️  TMDB API Key girilmedi!  →  '🔑 API Key' butonuna tıklayın.", 12000
            )

    # ── Canlı İstatistik Paneli ───────────────────────────────────────
    def _schedule_stats(self, *_args):
        """Debounce: çok sık tetiklenmeyi önlemek için timer'ı yeniden başlat."""
        self._stats_timer.start()

    def _update_stats(self):
        """Statusbar sağ tarafındaki canlı sayaçları günceller."""
        total   = self.table.rowCount()
        visible = sum(1 for r in range(total) if not self.table.isRowHidden(r))
        checked = sum(
            1 for r in range(total)
            if self.table.item(r, COL_CHECK)
            and self.table.item(r, COL_CHECK).checkState() == Qt.CheckState.Checked
        )
        tmdb_ok = sum(1 for r in range(total) if self._tmdb_state(r) == "ok")
        tmdb_no = sum(1 for r in range(total) if self._tmdb_state(r) == "fail")
        self._stat_total.setText(f"Toplam: {total}")
        vis_style = "padding:0 8px;font-size:12px;color:#ffcc00;font-weight:bold" if visible < total else "padding:0 8px;font-size:12px"
        self._stat_visible.setText(f"Görünür: {visible}")
        self._stat_visible.setStyleSheet(vis_style)
        chk_style = "padding:0 8px;font-size:12px;color:#4fc3f7;font-weight:bold" if checked > 0 else "padding:0 8px;font-size:12px"
        self._stat_checked.setText(f"Tikli: {checked}")
        self._stat_checked.setStyleSheet(chk_style)
        self._stat_tmdb_ok.setText(f"TMDB ✅: {tmdb_ok}")
        self._stat_tmdb_no.setText(f"❌: {tmdb_no}")

    # ── Klavye kısayolları ────────────────────────────────────────────
    # ══════════════════════════════════════════════════════════════════
    #  TABLO POSTER ÖNİZLEMESİ
    #  Logo hücresine küçük görsel koyar. Sadece EKRANDA GÖRÜNEN satırlar
    #  indirilir (3000 satırlık listede 3000 istek atmamak için).
    # ══════════════════════════════════════════════════════════════════
    def _init_thumbs(self):
        self._thumbs_on   = True          # varsayılan: açık
        self._thumb_cache = {}            # url -> QPixmap (bellek içi)
        self._thumb_loader = None
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(250)   # kaydırma bitince yükle
        self._thumb_timer.timeout.connect(self._load_visible_thumbs)
        # Kaydırma / boyut değişince tetikle
        self.table.verticalScrollBar().valueChanged.connect(
            lambda *_: self._thumb_timer.start())
        self.table.model().rowsInserted.connect(lambda *_: self._thumb_timer.start())

    def _visible_row_range(self):
        """Görünen ilk ve son satır (biraz tamponla)."""
        vp = self.table.viewport()
        first = self.table.rowAt(0)
        last  = self.table.rowAt(vp.height() - 1)
        if first < 0:
            first = 0
        if last < 0:
            last = min(self.table.rowCount() - 1, first + 40)
        return max(0, first - 5), min(self.table.rowCount() - 1, last + 5)

    def _load_visible_thumbs(self):
        """Görünen satırların posterlerini yükle (önce önbellek, sonra indir)."""
        if not getattr(self, "_thumbs_on", False):
            return
        if self.table.rowCount() == 0:
            return
        first, last = self._visible_row_range()
        need = []
        for row in range(first, last + 1):
            if self.table.isRowHidden(row):
                continue
            url = (self._get_cell(row, COL_LOGO) or "").strip()
            if not url or not url.lower().startswith(("http://", "https://")):
                continue
            px = self._thumb_cache.get(url)
            if px is not None:
                self._apply_thumb_to_row(row, px)
            elif url not in need:
                need.append(url)
        if not need:
            return
        # Önceki yükleyici hâlâ çalışıyorsa durdur
        if self._thumb_loader is not None and self._thumb_loader.isRunning():
            self._thumb_loader.stop()
        self._thumb_loader = _ThumbLoader(need, self)
        self._thumb_loader.ready.connect(self._on_thumb_ready)
        self._thumb_loader.start()

    def _on_thumb_ready(self, url, px):
        self._thumb_cache[url] = px
        # Bu URL'ye sahip TÜM satırlara uygula (aynı poster tekrarlanabilir)
        for row in range(self.table.rowCount()):
            if (self._get_cell(row, COL_LOGO) or "").strip() == url:
                self._apply_thumb_to_row(row, px)

    def _apply_thumb_to_row(self, row, px):
        it = self.table.item(row, COL_LOGO)
        if it is None:
            return
        it.setIcon(QIcon(px))

    def _clear_all_thumbs(self):
        for row in range(self.table.rowCount()):
            it = self.table.item(row, COL_LOGO)
            if it is not None:
                it.setIcon(QIcon())

    def _set_row_height(self, px):
        """
        Satır yüksekliğini değiştirir ve kaydırma aralığını YENİDEN HESAPLATIR.

        BUG (v24 ve öncesi): Sadece setDefaultSectionSize() çağrılıyordu.
        Tablo aşağı kaydırılmış durumdayken satırlar kısalınca toplam içerik
        yüksekliği düşer, ama Qt (ScrollPerItem modunda) kaydırma çubuğunun
        maksimumunu kendiliğinden güncellemez. Kaydırma konumu listenin
        sonunun ötesinde kalır ve TABLO TAMAMEN BOŞ görünür.

        Ölçüm (3000 satır, en alta kaydırılmış):
            dss 66 -> 24 :  sb.value=2991  sb.max=2991  rowAt(0) = -1  (BOŞ EKRAN)
            updateGeometries() sonrası: sb.max=2976  rowAt(0)=2975    (DÜZGÜN)

        Çözüm: yüksekliği değiştirdikten sonra updateGeometries() ile kaydırma
        aralığını tazele, sonra konumu geçerli aralığa kırp ve o an ekranda olan
        satırı koru (kullanıcı listede yerini kaybetmesin).
        """
        t  = self.table
        vh = t.verticalHeader()
        if vh.defaultSectionSize() == px:
            return
        # Değişimden önce ekranın en üstündeki satırı hatırla
        anchor = t.rowAt(0)
        if anchor < 0:
            anchor = t.currentRow()

        vh.setDefaultSectionSize(px)
        # Tek tek ayarlanmış (örn. resizeRowsToContents kalıntısı) satırları da hizala
        t.updateGeometries()          # ← kritik: kaydırma aralığını yeniden hesapla

        sb = t.verticalScrollBar()
        if anchor is not None and anchor >= 0:
            t.scrollToItem(t.item(anchor, COL_NAME) or t.item(anchor, COL_CHECK),
                           QAbstractItemView.ScrollHint.PositionAtTop)
        # Yine de aralık dışında kaldıysa güvenli sınıra çek
        if sb.value() > sb.maximum():
            sb.setValue(sb.maximum())
        t.viewport().update()

    def toggle_thumbs(self):
        """Poster önizlemesini aç/kapat."""
        self._thumbs_on = not getattr(self, "_thumbs_on", True)
        if self._thumbs_on:
            self.table.setIconSize(QSize(THUMB_W, THUMB_H))
            self._set_row_height(THUMB_H + 6)
            self._load_visible_thumbs()
            self.statusBar().showMessage("🖼️ Poster önizlemesi AÇIK", 4000)
        else:
            self._clear_all_thumbs()
            self._set_row_height(28)
            self.statusBar().showMessage(
                "🖼️ Poster önizlemesi KAPALI  (liste görünmeye devam eder)", 4000)

    def clear_thumb_cache(self):
        """Diskteki poster önbelleğini temizler."""
        n = 0
        try:
            if os.path.isdir(THUMB_DIR):
                for f in os.listdir(THUMB_DIR):
                    try:
                        os.remove(os.path.join(THUMB_DIR, f)); n += 1
                    except Exception:
                        pass
        except Exception:
            pass
        self._thumb_cache.clear()
        self._clear_all_thumbs()
        self.statusBar().showMessage(f"🧹 {n} önbellek görseli silindi.", 5000)
        if getattr(self, "_thumbs_on", False):
            self._load_visible_thumbs()

    # ══════════════════════════════════════════════════════════════════
    #  GERİ ALMA / YİNELEME  (Ctrl+Z / Ctrl+Y)
    #  Yıkıcı işlemden ÖNCE _snapshot("etiket") çağrılır.
    # ══════════════════════════════════════════════════════════════════
    UNDO_LIMIT = 25

    def _init_undo(self):
        self._undo_stack = []
        self._redo_stack = []
        self._undo_busy  = False   # geri alma sırasında yeni snapshot alma

    def _grab_table(self):
        """Tablonun tamamını hafif bir listeye kopyalar."""
        rows = []
        for r in range(self.table.rowCount()):
            chk = self.table.item(r, COL_CHECK)
            checked = bool(chk and chk.checkState() == Qt.CheckState.Checked)
            cells = [self._get_cell(r, c) for c in
                     (COL_LOGO, COL_GROUP, COL_NAME, COL_YEAR, COL_TMDBID, COL_URL, COL_UA, COL_REF, COL_STATUS)]
            # TMDB kalıcı işareti de saklanmalı; yoksa geri alma sonrası
            # "Bulamayanlar" filtresi yanlış sonuç verir.
            tit = self.table.item(r, COL_TMDBID)
            mark = tit.data(Qt.ItemDataRole.UserRole) if tit is not None else None
            rows.append((checked, cells, mark))
        return rows

    def _restore_table(self, rows):
        """_grab_table çıktısını tabloya geri yazar."""
        self._undo_busy = True
        try:
            was_sorting = self.table.isSortingEnabled()
            self.table.setSortingEnabled(False)
            self.table.setRowCount(0)
            for rec in rows:
                checked, cells = rec[0], rec[1]
                mark = rec[2] if len(rec) > 2 else None
                r = self.table.rowCount()
                self.table.insertRow(r)
                item = self._make_check_item()
                item.setCheckState(Qt.CheckState.Checked if checked
                                   else Qt.CheckState.Unchecked)
                self.table.setItem(r, COL_CHECK, item)
                for col, val in zip(
                        (COL_LOGO, COL_GROUP, COL_NAME, COL_YEAR,
                         COL_TMDBID, COL_URL, COL_UA, COL_REF, COL_STATUS), cells):
                    self.table.setItem(r, col, self._make_item(val))
                if mark in ("ok", "fail"):
                    self.table.item(r, COL_TMDBID).setData(
                        Qt.ItemDataRole.UserRole, mark)
            self.table.setSortingEnabled(was_sorting)
        finally:
            self._undo_busy = False
        self._schedule_stats()

    def _snapshot(self, label):
        """Yıkıcı işlemden önce çağrılır — mevcut durumu yığına iter."""
        if getattr(self, "_undo_busy", False):
            return
        if not hasattr(self, "_undo_stack"):
            self._init_undo()
        self._undo_stack.append((label, self._grab_table()))
        if len(self._undo_stack) > self.UNDO_LIMIT:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._mark_dirty()

    def undo_action(self):
        if not getattr(self, "_undo_stack", None):
            self.statusBar().showMessage("↶ Geri alınacak işlem yok.", 3000)
            return
        label, rows = self._undo_stack.pop()
        self._redo_stack.append((label, self._grab_table()))
        self._restore_table(rows)
        self._mark_dirty()
        self.statusBar().showMessage(f"↶ Geri alındı: {label}", 4000)

    def redo_action(self):
        if not getattr(self, "_redo_stack", None):
            self.statusBar().showMessage("↷ Yinelenecek işlem yok.", 3000)
            return
        label, rows = self._redo_stack.pop()
        self._undo_stack.append((label, self._grab_table()))
        self._restore_table(rows)
        self._mark_dirty()
        self.statusBar().showMessage(f"↷ Yinelendi: {label}", 4000)

    # ══════════════════════════════════════════════════════════════════
    #  OTOMATİK KAYDETME + KURTARMA
    #  60 sn'de bir geçici dosyaya yazar; çökme/kaza sonrası geri yükler.
    # ══════════════════════════════════════════════════════════════════
    AUTOSAVE_INTERVAL_MS = 60_000

    def _autosave_path(self):
        return os.path.join(tempfile.gettempdir(), "m3u_editor_autosave.json")

    def _init_autosave(self):
        self._dirty = False
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(self.AUTOSAVE_INTERVAL_MS)
        self._autosave_timer.timeout.connect(self._do_autosave)
        self._autosave_timer.start()
        # tablo her değiştiğinde "kaydedilmemiş" işaretle
        self.table.itemChanged.connect(self._mark_dirty)
        self.table.model().rowsInserted.connect(self._mark_dirty)
        self.table.model().rowsRemoved.connect(self._mark_dirty)

    def _mark_dirty(self, *_a):
        self._dirty = True

    def _do_autosave(self):
        if not getattr(self, "_dirty", False):
            return
        if self.table.rowCount() == 0:
            return
        try:
            data = {
                "saved_at": time.time(),
                "count": self.table.rowCount(),
                "rows": [
                    {"checked": rec[0], "cells": rec[1],
                     "mark": (rec[2] if len(rec) > 2 else None)}
                    for rec in self._grab_table()
                ],
            }
            tmp = self._autosave_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self._autosave_path())   # atomik yazma
            self._dirty = False
            self.statusBar().showMessage(
                f"💾 Otomatik kaydedildi ({data['count']} satır)", 2500)
        except Exception as e:
            self.statusBar().showMessage(f"⚠️ Otomatik kayıt başarısız: {e}", 4000)

    def _offer_recovery(self):
        """Açılışta kurtarılmamış çalışma varsa sorar."""
        path = self._autosave_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            rows = data.get("rows") or []
            if not rows:
                return
            when = time.strftime("%d.%m.%Y %H:%M",
                                 time.localtime(data.get("saved_at", 0)))
            reply = QMessageBox.question(
                self, "Kurtarma",
                f"Kaydedilmemiş çalışma bulundu:\n\n"
                f"  {len(rows)} satır  —  {when}\n\n"
                f"Geri yüklensin mi?\n"
                f"(Hayır derseniz bu yedek silinir.)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._restore_table([(r.get("checked", False), r.get("cells", []),
                                      r.get("mark"))
                                     for r in rows])
                self.statusBar().showMessage(
                    f"✅ {len(rows)} satır geri yüklendi.", 6000)
            else:
                self._clear_autosave()
        except Exception as e:
            self.statusBar().showMessage(f"⚠️ Kurtarma okunamadı: {e}", 5000)

    def _clear_autosave(self):
        try:
            if os.path.exists(self._autosave_path()):
                os.remove(self._autosave_path())
        except Exception:
            pass

    def closeEvent(self, event):
        """Kaydedilmemiş değişiklik varsa uyar."""
        if getattr(self, "_dirty", False) and self.table.rowCount() > 0:
            reply = QMessageBox.question(
                self, "Çıkış",
                "Kaydedilmemiş değişiklikler var.\n\n"
                "Yine de çıkmak istiyor musunuz?\n"
                "(Otomatik yedek saklanır, açılışta geri yükleyebilirsiniz.)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._do_autosave()   # çıkmadan önce son hali yaz
        else:
            self._clear_autosave()
        event.accept()

    # ══════════════════════════════════════════════════════════════════
    #  SÜRÜKLE-BIRAK ile dosya açma
    # ══════════════════════════════════════════════════════════════════
    def dragEnterEvent(self, event):
        md = event.mimeData()
        if md.hasUrls() and any(
                u.toLocalFile().lower().endswith((".m3u", ".m3u8", ".txt"))
                for u in md.urls()):
            event.acceptProposedAction()
            self.statusBar().showMessage(
                "📥 Bırakın — dosya yüklenecek (Shift basılıysa listeye eklenir)", 4000)
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls()
                 if u.toLocalFile().lower().endswith((".m3u", ".m3u8", ".txt"))]
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        # Shift basılıysa mevcut listeye EKLE, değilse DEĞİŞTİR
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if not shift and self.table.rowCount() > 0:
            reply = QMessageBox.question(
                self, "Dosya Bırakıldı",
                f"{len(paths)} dosya bırakıldı.\n\n"
                f"EVET → mevcut listeyi değiştir\n"
                f"HAYIR → listenin sonuna ekle",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            append_mode = (reply == QMessageBox.StandardButton.No)
        else:
            append_mode = shift

        self._snapshot("Dosya bırak")
        loaded = 0
        for i, fp in enumerate(paths):
            try:
                with open(fp, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except Exception as e:
                QMessageBox.warning(self, "Hata", f"{os.path.basename(fp)} okunamadı:\n{e}")
                continue
            # ilk dosya modu belirler, sonrakiler her zaman ekleme
            self.process_m3u_content(content, append=(append_mode or i > 0))
            loaded += 1
        if loaded:
            self.statusBar().showMessage(
                f"📥 {loaded} dosya yüklendi ({'eklendi' if append_mode else 'değiştirildi'})", 6000)

    def keyPressEvent(self, event):
        """Alt+↑/↓ = bir adım taşı  |  Alt+Home/End = en üste/alta taşı"""
        mod = event.modifiers()
        key = event.key()
        if mod == Qt.KeyboardModifier.AltModifier:
            if key == Qt.Key.Key_Up:
                self.move_rows_up();     return
            if key == Qt.Key.Key_Down:
                self.move_rows_down();   return
            if key == Qt.Key.Key_Home:
                self.move_rows_top();    return
            if key == Qt.Key.Key_End:
                self.move_rows_bottom(); return
        super().keyPressEvent(event)

    # ── Tema ──────────────────────────────────────────────────────────
    def _apply_theme(self):
        if self.is_dark_mode:
            self.setStyleSheet("""
                QMainWindow{background:#2b2b2b}
                QTableWidget{background:#3c3f41;color:white;gridline-color:#555;font-size:13px}
                QHeaderView::section{background:#4e5254;color:white;border:1px solid #333;font-size:12px;padding:3px}
                QLineEdit{background:#3c3f41;color:white;border:1px solid #555;padding:3px 5px;border-radius:3px;font-size:11px}
                QStatusBar{background:#323232;color:#ffcc00}
                /* Ribbon panel — kompakt */
                QWidget#ribbonPanel{background:#2b2b2b;border-bottom:1px solid #444}
                QGroupBox#ribbonGroup{
                    color:#aaa;font-size:9px;font-weight:normal;
                    border:1px solid #3a3a3a;border-radius:3px;
                    margin-top:6px;padding:0px 2px 1px 2px
                }
                QGroupBox#ribbonGroup::title{
                    subcontrol-origin:margin;left:4px;top:0px;padding:0 2px;
                    color:#888;font-size:9px
                }
                QFrame#ribbonSep{color:#444}
                QPushButton{
                    background:#3a3a3a;color:#ddd;
                    border:1px solid #555;border-radius:2px;
                    padding:1px 5px;font-size:11px
                }
                QPushButton:hover{background:#505050;color:white;border-color:#777}
                QPushButton:pressed{background:#222;border-color:#888}
                QPushButton#ribbonToggle{
                    background:#2a4a6a;color:#aed6f1;border:1px solid #3a6a8a;
                    padding:1px 6px;font-size:11px;border-radius:2px
                }
                QPushButton#ribbonToggle:hover{background:#3a5a7a}
                QTableWidget::item:selected{background:#0561a8}
            """)
        else:
            self.setStyleSheet("""
                QMainWindow{background:#f0f0f0}
                QTableWidget{background:white;color:black;gridline-color:#ddd;font-size:13px}
                QHeaderView::section{background:#e1e1e1;color:black;border:1px solid #ccc;font-size:12px;padding:3px}
                QLineEdit{background:white;color:black;border:1px solid #ccc;padding:3px 5px;border-radius:3px;font-size:11px}
                QStatusBar{color:#c00}
                /* Ribbon panel — kompakt */
                QWidget#ribbonPanel{background:#f5f5f5;border-bottom:1px solid #ccc}
                QGroupBox#ribbonGroup{
                    color:#666;font-size:9px;font-weight:normal;
                    border:1px solid #ddd;border-radius:3px;
                    margin-top:6px;padding:0px 2px 1px 2px
                }
                QGroupBox#ribbonGroup::title{
                    subcontrol-origin:margin;left:4px;top:0px;padding:0 2px;
                    color:#888;font-size:9px
                }
                QFrame#ribbonSep{color:#ccc}
                QPushButton{
                    background:#fff;color:#333;
                    border:1px solid #ccc;border-radius:2px;
                    padding:1px 5px;font-size:11px
                }
                QPushButton:hover{background:#e8f0fe;color:#1a73e8;border-color:#aac4f7}
                QPushButton:pressed{background:#d2e3fc;border-color:#1a73e8}
                QPushButton#ribbonToggle{
                    background:#e8f0fe;color:#1a73e8;border:1px solid #aac4f7;
                    padding:1px 6px;font-size:11px;border-radius:2px
                }
                QPushButton#ribbonToggle:hover{background:#d2e3fc}
                QTableWidget::item:selected{background:#a8d1ff;color:black}
            """)

    # ── Araç paneli (kompakt + gizlenebilir ribbon) ───────────────────
    def _build_toolbar(self):
        """
        Kompakt ribbon: küçük butonlar, sıkı boşluklar.
        Üst satırlar (buton grupları) gizlenebilir; arama çubuğu her zaman kalır.
        """
        panel = QWidget()
        panel.setObjectName("ribbonPanel")
        panel_vl = QVBoxLayout(panel)
        panel_vl.setContentsMargins(2, 2, 2, 1)
        panel_vl.setSpacing(1)

        def make_btn(icon, label, func, tooltip=""):
            btn = QPushButton(f"{icon} {label}")
            btn.setFixedHeight(24)
            btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(func)
            if tooltip:
                btn.setToolTip(tooltip)
            return btn

        def make_group(title, buttons):
            gb = QGroupBox(title)
            gb.setObjectName("ribbonGroup")
            hl = QHBoxLayout(gb)
            hl.setContentsMargins(2, 1, 2, 1)
            hl.setSpacing(2)
            for args in buttons:
                hl.addWidget(make_btn(*args))
            return gb

        def _scrollable_row(hbox_layout):
            ic = QWidget()
            ic.setLayout(hbox_layout)
            # Buton 24px + grup başlığı ~10px; ekstra pay minimal
            gerekli_yukseklik = max(38, ic.sizeHint().height()) + 4
            sc = QScrollArea()
            sc.setWidget(ic)
            sc.setWidgetResizable(True)
            sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            sc.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            sc.setFrameShape(QFrame.Shape.NoFrame)
            sc.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            sc.setFixedHeight(gerekli_yukseklik)
            return sc

        # ── Satır 1: Dosya & Düzenleme ────────────────────────────────
        row1 = QHBoxLayout()
        row1.setSpacing(2)
        row1.addWidget(make_group("📂 Dosya", [
            ("📂", "Aç",           self.open_m3u,         "M3U dosyası aç"),
            ("📂+","Ekle",         self.append_m3u,       "Mevcut listeye ekle"),
            ("🌐", "URL Aç",       self.import_from_url,  "URL'den yükle"),
            ("🌐+","URL Ekle",     self.append_from_url,  "URL'den listeye ekle"),
            ("💾", "Kaydet",       self.save_m3u,         "Kaydet (Ctrl+S)"),
        ]))
        row1.addWidget(make_group("📤 Dışa Aktar", [
            ("📤", "Seçili Aktar", self.export_selected,  "Seçili satırları dışa aktar"),
            ("📁", "Klasöre Aktar", self.export_by_group, "Gruba göre klasörlere aktar"),
        ]))
        row1.addWidget(make_group("↩️ Geri", [
            ("↶", "Geri Al", self.undo_action, "Geri al (Ctrl+Z)"),
            ("↷", "Yinele",  self.redo_action, "Yinele (Ctrl+Y)"),
        ]))
        row1.addWidget(make_group("✏️ Düzenle", [
            ("➕", "Yeni Satır",      self.add_new_row_top,      "Üste yeni satır"),
            ("🗑️","Sil",              self.delete_row,            "Seçili satırı sil"),
            ("✏️","Toplu Düzenle",    self.bulk_edit,             "Toplu düzenle"),
            ("🔎","Bul / Değiştir",   self.find_replace,          "Regex bul & değiştir"),
            ("🔁","Aynı Adı Sil",     self.delete_duplicates,     "Aynı isimdeki tekrarları sil"),
            ("🔗","Aynı URL Sil",     self.delete_duplicate_urls, "Aynı URL'leri sil"),
            ("🔢","S01E01 Düzelt",    self.normalize_episodes,    "Sezon/bölüm biçimini düzelt"),
            ("🕵️","User-Agent",       self.set_user_agent_bulk,   "Tikli/seçili satırlara User-Agent ata"),
            ("🔗🕵️","Referer",         self.set_referer_bulk,      "Tikli/seçili satırlara Referer ata"),
            ("👁️","UA/Referer Göster", self.toggle_ua_ref_columns, "User-Agent ve Referer sütunlarını göster/gizle"),
        ]))
        row1.addWidget(make_group("⬆️ Taşı", [
            ("⬆️", "Yukarı",   self.move_rows_up,     "Alt+↑"),
            ("⬇️", "Aşağı",    self.move_rows_down,   "Alt+↓"),
            ("⏫", "En Üste",  self.move_rows_top,    "Alt+Home"),
            ("⏬", "En Alta",  self.move_rows_bottom, "Alt+End"),
        ]))
        row1.addWidget(make_group("🖼️ Poster", [
            ("🖼️", "Aç / Kapa",  self.toggle_thumbs,     "Poster önizlemesi"),
            ("🧹", "Önbellek Sil", self.clear_thumb_cache, "Önbelleği temizle"),
        ]))
        row1.addWidget(make_group("⚙️", [
            ("🌓", "Tema", self._toggle_theme, "Açık/Koyu tema"),
        ]))
        row1.addStretch()

        # ── Satır 2: Link Kontrol + TMDB ──────────────────────────────
        row2 = QHBoxLayout()
        row2.setSpacing(2)
        row2.addWidget(make_group("🔍 Link", [
            ("🔍", "Kontrol Et",   self.check_links,        "Tüm görünür linkleri kontrol et"),
            ("▶️", "Devam Et",     self.resume_check_links, "Kaldığı yerden devam"),
            ("⏹️", "Durdur",       self.stop_check,         "Kontrolü durdur"),
            ("⚙️", "Paralel Ayar", self.set_link_workers,   "Paralel bağlantı sayısı"),
            ("🚫", "Ölüleri Sil",  self.delete_dead_links,  "Ölü linkleri sil"),
            ("👁️", "Sadece Ölüler", self.show_only_dead,   "Sadece ölüleri göster"),
            ("📋", "Tümünü Göster", self.show_all_rows,     "Filtre kaldır"),
        ]))
        row2.addWidget(make_group("🎬 TMDB", [
            ("🎬",  "Seçili Ara",     self.tmdb_search_selected, "Seçili satır için TMDB ara"),
            ("🎬📋","Toplu Ara",      self.tmdb_search_bulk,     "Tikli satırlar için toplu ara"),
            ("🚀",  "Tümünü Doldur",  self.tmdb_auto_all,        "Tümünü otomatik doldur"),
            ("🔴",  "Bulamayanlar",   self.show_only_not_found,  "TMDB bulamadıklarını göster"),
            ("📋",  "Tümünü Göster",  self.show_all_rows,        "Filtre kaldır"),
        ]))
        row2.addWidget(make_group("🔑 TMDB Ayar", [
            ("⚙️", "Seçenekler", self.tmdb_options_dialog, "Hangi alanlar güncellensin"),
            ("🔑", "API Key",    self.set_api_key,         "API anahtarı"),
            ("🧪", "Test Et",    self.test_api_key,        "API bağlantısını test et"),
        ]))
        row2.addStretch()

        # Buton satırlarını bir container'da tut (gizlenebilir)
        self._ribbon_buttons = QWidget()
        rb_vl = QVBoxLayout(self._ribbon_buttons)
        rb_vl.setContentsMargins(0, 0, 0, 0)
        rb_vl.setSpacing(0)   # iki satır birbirine yakın
        rb_vl.addWidget(_scrollable_row(row1))
        rb_vl.addWidget(_scrollable_row(row2))
        self._ribbon_buttons.setVisible(True)
        self._ribbon_collapsed = False

        # ── Satır 3: Arama + Gizle/Göster ─────────────────────────────
        row3 = QHBoxLayout()
        row3.setSpacing(3)
        row3.setContentsMargins(0, 0, 0, 0)

        self._ribbon_toggle_btn = QPushButton("▲ Menü")
        self._ribbon_toggle_btn.setObjectName("ribbonToggle")
        self._ribbon_toggle_btn.setFixedHeight(24)
        self._ribbon_toggle_btn.setToolTip("Menü şeridini gizle / göster")
        self._ribbon_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ribbon_toggle_btn.clicked.connect(self._toggle_ribbon)
        row3.addWidget(self._ribbon_toggle_btn)

        search_lbl = QLabel("🔍")
        search_lbl.setFixedWidth(18)
        self.search_bar = QLineEdit()
        self.search_bar.setFixedHeight(24)
        self.search_bar.setPlaceholderText("Ara… (ad, tür, yıl, TMDB ID)")
        self.search_bar.textChanged.connect(self.filter_table)
        clear_btn = QPushButton("✕")
        clear_btn.setFixedSize(24, 24)
        clear_btn.setToolTip("Aramayı temizle")
        clear_btn.clicked.connect(self.search_bar.clear)
        row3.addWidget(search_lbl)
        row3.addWidget(self.search_bar, 1)
        row3.addWidget(clear_btn)

        panel_vl.addWidget(self._ribbon_buttons)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setObjectName("ribbonSep")
        sep.setFixedHeight(1)
        panel_vl.addWidget(sep)

        panel_vl.addLayout(row3)

        self._ribbon_panel = panel
        self.centralWidget().layout().insertWidget(0, panel)

    def _toggle_ribbon(self):
        """Buton satırlarını gizle / göster; arama çubuğu her zaman kalır."""
        self._ribbon_collapsed = not getattr(self, "_ribbon_collapsed", False)
        if hasattr(self, "_ribbon_buttons"):
            self._ribbon_buttons.setVisible(not self._ribbon_collapsed)
        if hasattr(self, "_ribbon_toggle_btn"):
            if self._ribbon_collapsed:
                self._ribbon_toggle_btn.setText("▼ Menü")
                self._ribbon_toggle_btn.setToolTip("Menü şeridini göster")
                self.statusBar().showMessage("Menü gizlendi — tablo alanı genişledi.", 2500)
            else:
                self._ribbon_toggle_btn.setText("▲ Menü")
                self._ribbon_toggle_btn.setToolTip("Menü şeridini gizle")
                self.statusBar().showMessage("Menü gösteriliyor.", 2000)

    # ── Yardımcılar ───────────────────────────────────────────────────
    def _make_item(self, text=""):
        return QTableWidgetItem(str(text))

    def _make_check_item(self):
        """Checkbox görevi gören, ortada hizalanmış boş item."""
        it = QTableWidgetItem()
        it.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        it.setCheckState(Qt.CheckState.Unchecked)
        it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        return it

    def _header_check_toggle(self, col):
        """✔ sütun başlığına tıklanınca tüm görünür satırları seç/kaldır."""
        if col != COL_CHECK:
            return
        self._all_checked = not self._all_checked
        state = Qt.CheckState.Checked if self._all_checked else Qt.CheckState.Unchecked
        for row in range(self.table.rowCount()):
            if not self.table.isRowHidden(row):
                it = self.table.item(row, COL_CHECK)
                if it:
                    it.setCheckState(state)
        checked = sum(
            1 for r in range(self.table.rowCount())
            if not self.table.isRowHidden(r)
            and self.table.item(r, COL_CHECK)
            and self.table.item(r, COL_CHECK).checkState() == Qt.CheckState.Checked
        )
        self.statusBar().showMessage(
            f"{'✅' if self._all_checked else '☐'} {checked} satır {'seçildi' if self._all_checked else 'seçim kaldırıldı'}.", 3000
        )

    def _checked_rows(self) -> list[int]:
        """Tik işaretli satırların index listesini döner."""
        return [
            r for r in range(self.table.rowCount())
            if self.table.item(r, COL_CHECK)
            and self.table.item(r, COL_CHECK).checkState() == Qt.CheckState.Checked
        ]

    def _get_cell(self, row, col, default=""):
        if col == COL_CHECK:
            it = self.table.item(row, col)
            return "1" if (it and it.checkState() == Qt.CheckState.Checked) else "0"
        it = self.table.item(row, col)
        return it.text() if it else default

    # ══════════════════════════════════════════════════════════════════
    #  TMDB DURUMU — tek kaynak
    #  Durum sütununu link kontrolü de kullanıyor ("AKTİF ✅" vb.) ve
    #  TMDB'nin yazdığı "TMDB ✅" işaretini EZİYOR. Bu yüzden durumu
    #  yalnızca metinden okumak güvenilir değil; kalıcı bir işaret +
    #  gerçek veriye (tvg-id dolu mu) bakılır.
    # ══════════════════════════════════════════════════════════════════
    def _mark_tmdb(self, row, state):
        """Satıra kalıcı TMDB işareti koyar ('ok' / 'fail')."""
        it = self.table.item(row, COL_TMDBID)
        if it is None:
            it = self._make_item("")
            self.table.setItem(row, COL_TMDBID, it)
        it.setData(Qt.ItemDataRole.UserRole, state)

    def _tmdb_state(self, row):
        """'ok' | 'fail' | 'none'  —  satırın TMDB eşleşme durumu."""
        # 1) Kalıcı işaret (link kontrolü bunu ezemez)
        it = self.table.item(row, COL_TMDBID)
        if it is not None:
            mark = it.data(Qt.ItemDataRole.UserRole)
            if mark in ("ok", "fail"):
                return mark
        # 2) tvg-id dolu ise eşleşme vardır
        if (self._get_cell(row, COL_TMDBID) or "").strip():
            return "ok"
        # 3) Son çare: durum metni (henüz ezilmemişse)
        st = self._get_cell(row, COL_STATUS) or ""
        if "TMDB ✅" in st:
            return "ok"
        if "TMDB ❌" in st:
            return "fail"
        return "none"

    def _set_cell(self, row, col, text):
        it = self.table.item(row, col)
        if it:
            it.setText(str(text))
        else:
            self.table.setItem(row, col, self._make_item(str(text)))

    # ── Sütun boyutlandırma ──────────────────────────────────────────
    def _fix_last_col(self, col, old_size, new_size):
        """Sütun sürüklenince Durum sütununu kalan alana sığdır."""
        if self._fixing or col == COL_STATUS or col == COL_CHECK:
            return
        self._fixing = True
        vw = self.table.viewport().width()
        MIN_STATUS = 80
        other_sum = sum(self.table.columnWidth(c) for c in range(1, 9)
                        if c != col and not self.table.isColumnHidden(c))
        other_sum += self.table.columnWidth(COL_CHECK)
        max_w = vw - other_sum - MIN_STATUS
        if new_size > max_w:
            self.table.setColumnWidth(col, max(40, max_w))
            self.table.setColumnWidth(COL_STATUS, MIN_STATUS)
        else:
            self.table.setColumnWidth(COL_STATUS, max(MIN_STATUS, vw - other_sum - new_size))
        self._fixing = False

    def _fit_columns(self):
        """Açılışta / pencere boyutu değişince tüm sütunları orantılı dağıt.
        Gizli sütunlar (örn. User-Agent/Referer kapalıyken) hesaba katılmaz;
        böylece onların payı diğer görünür sütunlara dağıtılır ve sağda
        boş alan kalmaz."""
        vw = self.table.viewport().width()
        if vw < 100:
            return
        self._fixing = True
        visible_ratio_sum = sum(
            r for c, r in enumerate(self._col_ratios)
            if r is not None and not self.table.isColumnHidden(c)
        ) or 1.0
        for col, ratio in enumerate(self._col_ratios):
            if ratio is None:   # fixed (checkbox)
                self.table.setColumnWidth(col, 30)
            elif self.table.isColumnHidden(col):
                continue        # gizliyken genişlik ayarlamanın bir anlamı yok
            else:
                norm_ratio = ratio / visible_ratio_sum
                self.table.setColumnWidth(col, max(40, int(vw * norm_ratio)))
        self._fixing = False

    def showEvent(self, event):
        super().showEvent(event)
        self._fit_columns()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_columns()

    # ── Tema ──────────────────────────────────────────────────────────
    def _toggle_theme(self):
        self.is_dark_mode = not self.is_dark_mode
        self._apply_theme()

    def toggle_ua_ref_columns(self):
        """User-Agent / Referer sütunlarını göster/gizle (varsayılan: gizli)."""
        self._ua_ref_visible = not getattr(self, "_ua_ref_visible", False)
        self.table.setColumnHidden(COL_UA, not self._ua_ref_visible)
        self.table.setColumnHidden(COL_REF, not self._ua_ref_visible)
        self._fit_columns()
        self.statusBar().showMessage(
            "🕵️ User-Agent / Referer sütunları " +
            ("gösteriliyor" if self._ua_ref_visible else "gizlendi"), 4000
        )

    # ── Tablo işlemleri ───────────────────────────────────────────────
    def add_new_row_top(self):
        """
        Seçili satırın ÜSTÜNE yeni satır ekler. Hiçbir satır seçili
        değilse (tablo boşsa ya da seçim yoksa) en üste (0. satır) ekler
        — eski davranış yalnızca bu durumda korunur.
        """
        self._snapshot("Yeni satır ekle")
        secili = self.table.currentRow()
        hedef = secili if secili >= 0 else 0
        self.table.setSortingEnabled(False)
        self.table.insertRow(hedef)
        self.table.setItem(hedef, COL_CHECK, self._make_check_item())
        for col in range(1, self.table.columnCount()):
            self.table.setItem(hedef, col, self._make_item("Yeni" if col == COL_STATUS else ""))
        self.table.setSortingEnabled(True)
        self.table.scrollToItem(self.table.item(hedef, COL_CHECK))
        self.table.setCurrentCell(hedef, 0)

    def delete_row(self):
        indices = self.table.selectionModel().selectedRows()
        if indices or self.table.currentRow() >= 0:
            self._snapshot("Satır sil")
        if not indices:
            r = self.table.currentRow()
            if r >= 0: self.table.removeRow(r)
        else:
            for idx in sorted(indices, reverse=True):
                self.table.removeRow(idx.row())

    # ── Satır Taşıma ─────────────────────────────────────────────────
    def _read_row(self, row):
        """Bir satırın tüm hücre verilerini dict olarak döner."""
        data = {}
        for col in range(self.table.columnCount()):
            it = self.table.item(row, col)
            if it is None:
                data[col] = None
                continue
            if col == COL_CHECK:
                data[col] = it.checkState()
            else:
                data[col] = {
                    "text":       it.text(),
                    "foreground": it.foreground(),
                    "background": it.background(),
                    "flags":      it.flags(),
                }
        return data

    def _write_row(self, row, data):
        """_read_row ile alınan veriyi belirtilen satıra yazar."""
        for col, val in data.items():
            if val is None:
                self.table.setItem(row, col, self._make_item(""))
                continue
            if col == COL_CHECK:
                it = self._make_check_item()
                it.setCheckState(val)
                self.table.setItem(row, col, it)
            else:
                it = QTableWidgetItem(val["text"])
                it.setForeground(val["foreground"])
                it.setBackground(val["background"])
                it.setFlags(val["flags"])
                self.table.setItem(row, col, it)

    def _move_rows(self, rows: list[int], direction: int):
        """
        rows      : taşınacak satır indekslerinin sıralı listesi
        direction : -1 = yukarı, +1 = aşağı
        Bitişik veya dağınık satırları gruplar halinde taşır.
        Sınırda (en üst / en alt) ise sessizce durur.
        """
        if not rows:
            return
        total = self.table.rowCount()
        rows  = sorted(rows, reverse=(direction == 1))

        # Sınır kontrolü — hareket edemeyecek satır varsa hiç taşıma
        if direction == -1 and rows[0] == 0:
            return
        if direction == 1  and rows[-1] == total - 1:
            return

        # Sıralamayı kalıcı olarak kapat (taşıma boyunca açma)
        self.table.setSortingEnabled(False)
        self.table.setUpdatesEnabled(False)

        try:
            for row in rows:
                target = row + direction
                src_data = self._read_row(row)
                tgt_data = self._read_row(target)
                self._write_row(row,    tgt_data)
                self._write_row(target, src_data)
        finally:
            self.table.setUpdatesEnabled(True)
            # NOT: setSortingEnabled(True) kasıtlı olarak buraya alınmadı
            # — açılırsa Qt satırları yeniden sıralar ve taşıma bozulur

        # Seçimi yeni pozisyonlara taşı (çoklu seçim korunacak şekilde)
        sm = self.table.selectionModel()
        sm.clearSelection()
        new_rows = [r + direction for r in rows]
        for r in new_rows:
            sm.select(
                self.table.model().index(r, 0),
                sm.SelectionFlag.Select | sm.SelectionFlag.Rows
            )
        scroll_to = new_rows[-1] if direction == 1 else new_rows[0]
        self.table.scrollTo(self.table.model().index(scroll_to, 0))
        self._schedule_stats()

    def _get_move_targets(self):
        """
        Taşınacak satırları belirler:
        1) Tikli (✔) satırlar varsa onlar,
        2) Yoksa Ctrl/Shift ile seçili satırlar,
        3) Yoksa sadece geçerli satır.
        """
        rows = self._checked_rows()
        if not rows:
            sel = self.table.selectionModel().selectedRows()
            rows = sorted({idx.row() for idx in sel})
        if not rows:
            r = self.table.currentRow()
            if r >= 0:
                rows = [r]
        return rows

    def move_rows_up(self):
        self._move_rows(self._get_move_targets(), -1)

    def move_rows_down(self):
        self._move_rows(self._get_move_targets(), +1)

    def move_rows_top(self):
        """Seçili/tikli satırları tablonun en üstüne taşır."""
        rows = self._get_move_targets()
        if not rows or rows[0] == 0:
            return
        self.table.setSortingEnabled(False)
        self.table.setUpdatesEnabled(False)
        try:
            # Taşınacak satırların verilerini oku
            moving = [self._read_row(r) for r in rows]
            # Geri kalan satırları sırayla oku (taşınanlar hariç)
            remaining_indices = [r for r in range(self.table.rowCount()) if r not in set(rows)]
            remaining = [self._read_row(r) for r in remaining_indices]
            # Yeni sıra: önce taşınanlar, sonra kalanlar
            new_order = moving + remaining
            for i, data in enumerate(new_order):
                self._write_row(i, data)
        finally:
            self.table.setUpdatesEnabled(True)
        # Seçimi güncelle
        sm = self.table.selectionModel()
        sm.clearSelection()
        for r in range(len(rows)):
            sm.select(
                self.table.model().index(r, 0),
                sm.SelectionFlag.Select | sm.SelectionFlag.Rows
            )
        self.table.scrollToTop()
        self._schedule_stats()

    def move_rows_bottom(self):
        """Seçili/tikli satırları tablonun en altına taşır."""
        rows = self._get_move_targets()
        total = self.table.rowCount()
        if not rows or rows[-1] == total - 1:
            return
        self.table.setSortingEnabled(False)
        self.table.setUpdatesEnabled(False)
        try:
            moving = [self._read_row(r) for r in rows]
            remaining_indices = [r for r in range(total) if r not in set(rows)]
            remaining = [self._read_row(r) for r in remaining_indices]
            new_order = remaining + moving
            for i, data in enumerate(new_order):
                self._write_row(i, data)
        finally:
            self.table.setUpdatesEnabled(True)
        # Seçimi güncelle
        sm = self.table.selectionModel()
        sm.clearSelection()
        start = total - len(rows)
        for r in range(start, total):
            sm.select(
                self.table.model().index(r, 0),
                sm.SelectionFlag.Select | sm.SelectionFlag.Rows
            )
        self.table.scrollToBottom()
        self._schedule_stats()

    def filter_table(self):
        text = self.search_bar.text().lower()
        for row in range(self.table.rowCount()):
            if not text:
                self.table.setRowHidden(row, False)
            else:
                match = any(
                    (self.table.item(row, col) and
                     text in self.table.item(row, col).text().lower())
                    for col in [COL_LOGO, COL_GROUP, COL_NAME, COL_YEAR, COL_TMDBID, COL_URL, COL_UA, COL_REF]
                )
                self.table.setRowHidden(row, not match)
        self._schedule_stats()

    # ── M3U Yükleme ───────────────────────────────────────────────────
    def process_m3u_content(self, content, append=False):
        if self.table.rowCount() > 0:
            self._snapshot("Dosya ekle" if append else "Dosya aç")
        lines = content.splitlines()
        self.table.setSortingEnabled(False)
        if not append:
            self.table.setRowCount(0)
        added = 0
        # Append modunda: yeni içerikleri en üste eklemek için
        # önce tüm yeni satırları toplayıp sonra 0. pozisyondan itibaren ekle
        new_entries = []
        for i, line in enumerate(lines):
            line = line.strip()
            if not line.startswith("#EXTINF"):
                continue
            def _attr(tag):
                m = re.search(rf'{tag}="([^"]+)"', line)
                return m.group(1) if m else ""
            logo      = _attr("tvg-logo")
            group_raw = _attr("group-title")
            group     = " ".join(group_raw.split())
            tvg_id    = _attr("tvg-id")
            tmdb_id_v = _attr("tmdb-id")
            tmdb_id   = tvg_id or tmdb_id_v
            year      = _attr("tvg-year")
            # DÜZELTME: İçerik adı, TIRNAK DIŞINDAKİ ilk virgülden sonra başlar.
            # Eskiden split(",", 1) kullanılıyordu; group-title="Love, Death & Robots"
            # gibi virgül içeren bir öznitelik varsa ad yanlış yerden bölünüyordu.
            name      = _extinf_name(line)
            # #EXTVLCOPT gibi meta satırları atlayarak gerçek URL'yi bul
            # (varsa) http-user-agent ve http-referrer değerlerini de bu meta satırlarından yakala
            url = ""
            user_agent = ""
            referer = ""
            for j in range(i+1, min(i+6, len(lines))):
                candidate = lines[j].strip()
                if candidate.startswith("http") or candidate.startswith("rtmp") or candidate.startswith("rtsp"):
                    url = candidate
                    break
                m_ua = re.match(r'#EXTVLCOPT:http-user-agent=(.+)$', candidate, re.IGNORECASE)
                if m_ua:
                    user_agent = m_ua.group(1).strip()
                    continue
                m_ref = re.match(r'#EXTVLCOPT:http-referr?er=(.+)$', candidate, re.IGNORECASE)
                if m_ref:
                    referer = m_ref.group(1).strip()
            new_entries.append((logo, group, name, year, tmdb_id, url, user_agent, referer))

        if append:
            # Ters sıradan ekle ki sonuçta orijinal sıra korunsun (en üste)
            for entry in reversed(new_entries):
                logo, group, name, year, tmdb_id, url, user_agent, referer = entry
                self.table.insertRow(0)
                self.table.setItem(0, COL_CHECK,  self._make_check_item())
                self.table.setItem(0, COL_LOGO,   self._make_item(logo))
                self.table.setItem(0, COL_GROUP,  self._make_item(group))
                self.table.setItem(0, COL_NAME,   self._make_item(name))
                self.table.setItem(0, COL_YEAR,   self._make_item(year))
                self.table.setItem(0, COL_TMDBID, self._make_item(tmdb_id))
                self.table.setItem(0, COL_URL,    self._make_item(url))
                self.table.setItem(0, COL_UA,     self._make_item(user_agent))
                self.table.setItem(0, COL_REF,    self._make_item(referer))
                self.table.setItem(0, COL_STATUS, self._make_item("Bekliyor"))
                added += 1
        else:
            for entry in new_entries:
                logo, group, name, year, tmdb_id, url, user_agent, referer = entry
                r = self.table.rowCount()
                self.table.insertRow(r)
                self.table.setItem(r, COL_CHECK,  self._make_check_item())
                self.table.setItem(r, COL_LOGO,   self._make_item(logo))
                self.table.setItem(r, COL_GROUP,  self._make_item(group))
                self.table.setItem(r, COL_NAME,   self._make_item(name))
                self.table.setItem(r, COL_YEAR,   self._make_item(year))
                self.table.setItem(r, COL_TMDBID, self._make_item(tmdb_id))
                self.table.setItem(r, COL_URL,    self._make_item(url))
                self.table.setItem(r, COL_UA,     self._make_item(user_agent))
                self.table.setItem(r, COL_REF,    self._make_item(referer))
                self.table.setItem(r, COL_STATUS, self._make_item("Bekliyor"))
                added += 1

        self.table.setSortingEnabled(True)
        if append:
            self.table.scrollToTop()
            self.statusBar().showMessage(
                f"✅ {added} içerik en üste eklendi. Toplam: {self.table.rowCount()}", 5000)
        else:
            QMessageBox.information(self, "Yüklendi", f"{added} içerik yüklendi.")
        self._schedule_stats()

    def open_m3u(self):
        fp, _ = QFileDialog.getOpenFileName(
            self, "M3U Aç (mevcut liste silinir)", "", "M3U Files (*.m3u *.m3u8)")
        if fp:
            with open(fp, 'r', encoding='utf-8', errors='ignore') as f:
                self.process_m3u_content(f.read(), append=False)

    def append_m3u(self):
        fp, _ = QFileDialog.getOpenFileName(
            self, "M3U Ekle (mevcut listeye eklenir)", "", "M3U Files (*.m3u *.m3u8)")
        if fp:
            with open(fp, 'r', encoding='utf-8', errors='ignore') as f:
                self.process_m3u_content(f.read(), append=True)

    def import_from_url(self):
        url, ok = QInputDialog.getText(self, "M3U URL Aç", "Playlist URL (mevcut liste silinir):")
        if ok and url:
            try:
                r = requests.get(url, timeout=10)
                if r.status_code == 200:
                    self.process_m3u_content(r.text, append=False)
            except Exception as e:
                QMessageBox.critical(self, "Hata", str(e))

    def append_from_url(self):
        url, ok = QInputDialog.getText(self, "M3U URL Ekle", "Playlist URL (mevcut listeye eklenir):")
        if ok and url:
            try:
                r = requests.get(url, timeout=10)
                if r.status_code == 200:
                    self.process_m3u_content(r.text, append=True)
            except Exception as e:
                QMessageBox.critical(self, "Hata", str(e))

    # ── M3U Kaydet ────────────────────────────────────────────────────
    def save_m3u(self):
        if self.table.rowCount() == 0:
            QMessageBox.warning(self, "Uyarı", "Kaydedilecek veri yok!")
            return
        fp, _ = QFileDialog.getSaveFileName(self, "M3U Kaydet", "", "M3U Files (*.m3u)")
        if not fp:
            return
        try:
            with open(fp, 'w', encoding='utf-8') as f:
                f.write("#EXTM3U\n")
                for row in range(self.table.rowCount()):
                    logo    = self._get_cell(row, COL_LOGO).strip()
                    group   = " ".join(self._get_cell(row, COL_GROUP).split())  # newline/whitespace temizle
                    name    = self._get_cell(row, COL_NAME).strip() or "İçerik"
                    year    = self._get_cell(row, COL_YEAR)
                    tmdb_id = self._get_cell(row, COL_TMDBID)
                    url     = self._get_cell(row, COL_URL)
                    user_agent = self._get_cell(row, COL_UA).strip()
                    referer    = self._get_cell(row, COL_REF).strip()
                    if not url:
                        continue
                    # Öznitelik değerlerindeki çift tırnak kaçırılır; aksi halde
                    # satır bozulur ve tekrar okunduğunda ad/grup karışır.
                    extinf = "#EXTINF:-1"
                    if logo:    extinf += f' tvg-logo="{_m3u_attr(logo)}"'
                    if group:   extinf += f' group-title="{_m3u_attr(group)}"'
                    if year:    extinf += f' tvg-year="{_m3u_attr(year)}"'
                    if tmdb_id: extinf += f' tvg-id="{_m3u_attr(tmdb_id)}"'
                    f.write(f"{extinf},{name}\n")
                    if user_agent:
                        f.write(f"#EXTVLCOPT:http-user-agent={user_agent}\n")
                    if referer:
                        f.write(f"#EXTVLCOPT:http-referrer={referer}\n")
                    f.write(f"{url}\n")
            QMessageBox.information(self, "Başarılı", f"✅ Kaydedildi:\n{fp}")
        except Exception as e:
            QMessageBox.critical(self, "Hata", str(e))

    # ── Seçilileri Dışa Aktar ─────────────────────────────────────────
    def export_selected(self):
        """Tik işaretli satırları (yoksa seçili satırları) tek bir .m3u dosyasına aktarır."""
        # Önce checkbox'lara bak
        checked = self._checked_rows()
        if checked:
            target_rows = checked
            label = f"tikli {len(target_rows)}"
        else:
            target_rows = sorted(set(
                idx.row() for idx in self.table.selectionModel().selectedRows()
            ))
            label = f"seçili {len(target_rows)}"

        if not target_rows:
            QMessageBox.warning(self, "Uyarı",
                "Dışa aktarmak için satır seçin.\n\n"
                "• Tik kutusu ile seçebilirsiniz\n"
                "• veya Ctrl+tıklama / Shift+tıklama ile satır seçebilirsiniz")
            return

        fp, _ = QFileDialog.getSaveFileName(
            self, f"{label} İçeriği Kaydet", "", "M3U Files (*.m3u)")
        if not fp:
            return

        try:
            saved = self._write_rows_to_file(fp, target_rows)
            QMessageBox.information(self, "Başarılı",
                f"✅ {saved} içerik dışa aktarıldı:\n{fp}")
        except Exception as e:
            QMessageBox.critical(self, "Hata", str(e))

    # ── Klasöre Göre Dışa Aktar ───────────────────────────────────────
    def export_by_group(self):
        """Her Tür/Grup için ayrı bir .m3u dosyası oluşturur, seçilen klasöre kaydeder.
        Filtre aktifse sadece görünür satırları kullanır."""
        if self.table.rowCount() == 0:
            QMessageBox.warning(self, "Uyarı", "Kaydedilecek veri yok!")
            return

        visible_rows = [r for r in range(self.table.rowCount())
                        if not self.table.isRowHidden(r)]
        if not visible_rows:
            QMessageBox.information(self, "Bilgi", "Görünür satır bulunamadı.")
            return

        # Grupları topla
        groups: dict[str, list[int]] = {}
        for row in visible_rows:
            grp = self._get_cell(row, COL_GROUP).strip() or "Diğer"
            groups.setdefault(grp, []).append(row)

        # Kullanıcıya önizleme göster
        preview = "\n".join(
            f"  📄 {grp}.m3u  ({len(rows)} içerik)"
            for grp, rows in sorted(groups.items())
        )
        reply = QMessageBox.question(
            self, "Klasöre Göre Dışa Aktar",
            f"{len(groups)} farklı tür/grup bulundu. Her biri için ayrı .m3u dosyası oluşturulacak:\n\n"
            f"{preview}\n\nKayıt klasörünü seçmek istiyor musunuz?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        folder = QFileDialog.getExistingDirectory(self, "Kayıt Klasörü Seç")
        if not folder:
            return

        import os, re as _re

        def safe_filename(name: str) -> str:
            """Dosya adı için geçersiz karakterleri temizler."""
            name = _re.sub(r'[\\/:*?"<>|]', '_', name)
            return name.strip('. ') or "Grup"

        total_files = 0
        total_items = 0
        errors = []
        for grp, rows in sorted(groups.items()):
            fname = safe_filename(grp) + ".m3u"
            fpath = os.path.join(folder, fname)
            try:
                saved = self._write_rows_to_file(fpath, rows)
                total_files += 1
                total_items += saved
            except Exception as e:
                errors.append(f"{fname}: {e}")

        msg = f"✅ {total_files} dosya, {total_items} içerik aktarıldı.\nKlasor: {folder}"
        if errors:
            msg += f"\n\n⚠️ Hatalar:\n" + "\n".join(errors)
        QMessageBox.information(self, "Tamamlandı", msg)

    # ── Ortak yazma yardımcısı ────────────────────────────────────────
    def _write_rows_to_file(self, filepath: str, rows: list) -> int:
        """Verilen satır listesini filepath'e .m3u formatında yazar. Yazılan satır sayısını döner."""
        saved = 0
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write("#EXTM3U\n")
            for row in rows:
                logo    = self._get_cell(row, COL_LOGO).strip()
                group   = " ".join(self._get_cell(row, COL_GROUP).split())
                name    = self._get_cell(row, COL_NAME).strip() or "İçerik"
                year    = self._get_cell(row, COL_YEAR)
                tmdb_id = self._get_cell(row, COL_TMDBID)
                url     = self._get_cell(row, COL_URL)
                user_agent = self._get_cell(row, COL_UA).strip()
                referer    = self._get_cell(row, COL_REF).strip()
                if not url:
                    continue
                extinf = "#EXTINF:-1"
                if logo:    extinf += f' tvg-logo="{_m3u_attr(logo)}"'
                if group:   extinf += f' group-title="{_m3u_attr(group)}"'
                if year:    extinf += f' tvg-year="{_m3u_attr(year)}"'
                if tmdb_id: extinf += f' tvg-id="{_m3u_attr(tmdb_id)}"'
                f.write(f"{extinf},{name}\n")
                if user_agent:
                    f.write(f"#EXTVLCOPT:http-user-agent={user_agent}\n")
                if referer:
                    f.write(f"#EXTVLCOPT:http-referrer={referer}\n")
                f.write(f"{url}\n")
                saved += 1
        return saved

    # ── Link Kontrol (Paralel) ────────────────────────────────────────
    def check_links(self):
        """
        Paralel ThreadPoolExecutor ile tüm görünür satırları eş zamanlı kontrol eder.
        Worker sayısı _link_workers özelliğiyle ayarlanabilir (varsayılan: 30).
        Bu, HER ZAMAN görünür satırların TAMAMINI yeniden kontrol eder
        (baştan başlar). Durdurulan bir kontrolü kaldığı yerden devam
        ettirmek için 'Devam Et' düğmesini kullanın.
        """
        total = self.table.rowCount()
        if total == 0:
            return

        # Eğer önceki worker hâlâ çalışıyorsa uyar
        if self.link_check_worker and self.link_check_worker.isRunning():
            QMessageBox.information(self, "Bilgi", "Link kontrol zaten çalışıyor.\n'⏹️ Durdur' ile durdurun.")
            return

        visible_rows = [r for r in range(total) if not self.table.isRowHidden(r)]
        if not visible_rows:
            QMessageBox.information(self, "Bilgi", "Görünür satır bulunamadı.")
            return

        rows_urls = []
        for row in visible_rows:
            url = self._get_cell(row, COL_URL)
            if url:
                rows_urls.append((row, url))

        if not rows_urls:
            QMessageBox.information(self, "Bilgi", "Kontrol edilecek URL bulunamadı.")
            return

        self._kontrolu_baslat(rows_urls, total)

    def resume_check_links(self):
        """
        'Durdur'dan sonra kaldığı yerden devam eder: yalnızca HENÜZ
        DURUM SÜTUNU BOŞ olan (yani bir önceki taramada kontrol
        edilememiş) satırları kontrol eder. Zaten "AKTİF ✅ / HATA .. /
        KAPALI 🛑" durumu yazılmış satırlar tekrar kontrol edilmez.

        NEDEN GEREKLİ: Eskiden 'Kontrol Et'e her basışta TÜM satırlar
        (durdurulmadan önce kontrol edilmiş olanlar dahil) baştan
        taranıyordu — "durdurup tekrar kontrol et'e basınca yine
        baştan başlıyor" şikayeti buradan kaynaklanıyordu.
        """
        total = self.table.rowCount()
        if total == 0:
            return
        if self.link_check_worker and self.link_check_worker.isRunning():
            QMessageBox.information(self, "Bilgi", "Link kontrol zaten çalışıyor.\n'⏹️ Durdur' ile durdurun.")
            return

        visible_rows = [r for r in range(total) if not self.table.isRowHidden(r)]
        rows_urls = []
        for row in visible_rows:
            durum = (self._get_cell(row, COL_STATUS) or "").strip()
            # DİKKAT: Bu sütun link kontrolü DIŞINDA TMDB sonuçları için de
            # kullanılıyor ("TMDB ✅"/"TMDB ❌") ve yeni/boş satırlarda
            # "Yeni"/"Bekliyor" gibi yer tutucular içerebiliyor. Yalnızca
            # GERÇEK bir link kontrolü sonucu varsa (AKTİF/KAPALI/HATA)
            # bu satır "zaten kontrol edilmiş" sayılır — aksi hâlde
            # yeni eklenen ya da TMDB ile işlenmiş satırlar yanlışlıkla
            # atlanır.
            zaten_kontrol_edilmis = any(a in durum for a in ("AKTİF", "KAPALI", "HATA "))
            if zaten_kontrol_edilmis:
                continue
            url = self._get_cell(row, COL_URL)
            if url:
                rows_urls.append((row, url))

        if not rows_urls:
            QMessageBox.information(
                self, "Bilgi",
                "Devam edilecek satır yok — görünür satırların tamamı zaten "
                "kontrol edilmiş.\nBaştan taramak için '🔍 Kontrol Et' kullanın.")
            return

        self._kontrolu_baslat(rows_urls, total, devam=True)

    def _kontrolu_baslat(self, rows_urls, total, devam: bool = False):
        """check_links() ve resume_check_links() için ORTAK başlatma kodu."""
        count   = len(rows_urls)
        workers = getattr(self, "_link_workers", 30)
        timeout = getattr(self, "_link_timeout", 8)

        filtered = count < total
        etiket = "devam ediliyor" if devam else "başladı"
        label = f"(filtreli: {count} link)" if filtered else f"({count} link)"
        self.statusBar().showMessage(
            f"🔍 Paralel link kontrol {etiket} {label} — {workers} eş zamanlı bağlantı"
        )

        baslik = "Linkler kontrol ediliyor (devam)…" if devam else "Linkler kontrol ediliyor…"
        self._link_prog = QProgressDialog(baslik, "Durdur", 0, count, self)
        self._link_prog.setWindowTitle("Link Kontrol")
        self._link_prog.setWindowModality(Qt.WindowModality.WindowModal)
        self._link_prog.show()

        self.link_check_worker = LinkCheckWorker(rows_urls, workers=workers, timeout=timeout)
        self.link_check_worker.result_ready.connect(self._on_link_result)
        self.link_check_worker.progress.connect(self._on_link_progress)
        self.link_check_worker.finished.connect(self._on_link_done)
        self._link_prog.canceled.connect(self.link_check_worker.stop)
        self.link_check_worker.start()

    def _on_link_result(self, row, status, color):
        self._set_cell(row, COL_STATUS, status)
        it = self.table.item(row, COL_STATUS)
        if it:
            it.setBackground(color)
            it.setForeground(QColor("white"))

    def _on_link_progress(self, done, total, url):
        self._link_prog.setValue(done)
        self._link_prog.setLabelText(
            f"{done}/{total} kontrol edildi\n{url[:80]}"
        )

    def _on_link_done(self, active, dead, error):
        self._link_prog.close()
        total = active + dead + error
        self.statusBar().showMessage(
            f"✅ Link kontrol tamamlandı — "
            f"Aktif: {active}  |  Kapalı: {dead}  |  Hata: {error}  |  Toplam: {total}",
            8000
        )
        QMessageBox.information(
            self, "Link Kontrol Tamamlandı",
            f"✅ Aktif  : {active}\n"
            f"🛑 Kapalı : {dead}\n"
            f"⚠️  Hata   : {error}\n"
            f"─────────────\n"
            f"📊 Toplam  : {total}"
        )

    def set_link_workers(self):
        """Eş zamanlı bağlantı (worker) sayısını kullanıcıdan al."""
        current = getattr(self, "_link_workers", 30)
        val, ok = QInputDialog.getInt(
            self, "Paralel Bağlantı Sayısı",
            "Eş zamanlı kontrol edilecek link sayısı:\n"
            "(Az: daha güvenli  |  Çok: daha hızlı)\n"
            "Önerilen: 20-50",
            current, 1, 100, 5
        )
        if ok:
            self._link_workers = val
            self.statusBar().showMessage(f"⚙️ Paralel bağlantı sayısı: {val}", 4000)

    def set_link_timeout(self):
        """Bağlantı zaman aşımı süresini kullanıcıdan al."""
        current = getattr(self, "_link_timeout", 8)
        val, ok = QInputDialog.getInt(
            self, "Zaman Aşımı (saniye)",
            "Her link için maksimum bekleme süresi (saniye):",
            current, 1, 60, 1
        )
        if ok:
            self._link_timeout = val
            self.statusBar().showMessage(f"⚙️ Zaman aşımı: {val} sn", 4000)

    def stop_check(self):
        if self.link_check_worker and self.link_check_worker.isRunning():
            self.link_check_worker.stop()
        self._check_running = False

    def delete_dead_links(self):
        dead = []
        for row in range(self.table.rowCount()):
            status = self._get_cell(row, COL_STATUS)
            if "KAPALI" in status or "HATA" in status:
                dead.append(row)
        if not dead:
            QMessageBox.information(self, "Bilgi", "Silinecek ölü link bulunamadı.\nÖnce 'Link Kontrol' çalıştırın.")
            return
        reply = QMessageBox.question(
            self, "Ölü Linkleri Sil",
            f"{len(dead)} adet çalışmayan link bulundu.\nHepsini silmek istiyor musunuz?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._snapshot(f"{len(dead)} ölü link sil")
            for row in sorted(dead, reverse=True):
                self.table.removeRow(row)
            self.statusBar().showMessage(f"🗑️ {len(dead)} ölü link silindi.", 5000)

    def show_only_dead(self):
        found = 0
        for row in range(self.table.rowCount()):
            status = self._get_cell(row, COL_STATUS)
            is_dead = "KAPALI" in status or "HATA" in status
            self.table.setRowHidden(row, not is_dead)
            if is_dead:
                found += 1
        if found == 0:
            QMessageBox.information(self, "Bilgi", "Çalışmayan link bulunamadı.\nÖnce 'Link Kontrol' çalıştırın.")
            self.show_all_rows()
        else:
            self.statusBar().showMessage(f"👁️ {found} çalışmayan link gösteriliyor. Tümünü görmek için '📋 Tümünü Göster'e basın.", 8000)
            self._schedule_stats()

    def show_only_not_found(self):
        """TMDB tarafından bulunamayan (TMDB ✅ işareti olmayan, dolu) satırları gösterir.
        TMDB otomatik doldur çalıştırıldıktan sonra kullanışlıdır."""
        total = self.table.rowCount()
        if total == 0:
            QMessageBox.information(self, "Bilgi", "Önce bir liste yükleyin.")
            return

        found = 0
        not_started = 0  # Henüz TMDB sorgusu yapılmamış satırlar
        for row in range(total):
            # DÜZELTME: Durum sütununu link kontrolü de kullanıyor ("AKTİF ✅",
            # "HATA 404" ...) ve TMDB'nin yazdığı işareti EZİYOR. Metne bakmak
            # yerine kalıcı işaret + tvg-id'ye bakan _tmdb_state kullanılır.
            st = self._tmdb_state(row)
            is_not_found = (st != "ok")
            self.table.setRowHidden(row, not is_not_found)
            if is_not_found:
                found += 1
                if st == "none":
                    not_started += 1

        if found == 0:
            QMessageBox.information(
                self, "Tebrikler! 🎉",
                "Tüm içerikler TMDB'de bulundu! Hiç eksik kalmadı."
            )
            self.show_all_rows()
        else:
            extra = f"\n({not_started} tanesi henüz sorgulanmadı)" if not_started else ""
            self.statusBar().showMessage(
                f"🔴 {found} içerik TMDB'de bulunamadı.{extra}  "
                f"Tümünü görmek için '📋 Tümünü Göster (TMDB)'ye basın.",
                10000
            )
            if not_started == found:
                QMessageBox.information(
                    self, "Bilgi",
                    f"{found} satır görüntüleniyor, ancak henüz TMDB sorgusu yapılmamış.\n\n"
                    f"Önce '🚀 TMDB: Tümünü Doldur' butonunu çalıştırın,\n"
                    f"ardından bu butona tekrar basın."
                )
            self._schedule_stats()

    def show_all_rows(self):
        self.search_bar.clear()
        for row in range(self.table.rowCount()):
            self.table.setRowHidden(row, False)
        self.statusBar().showMessage("📋 Tüm satırlar gösteriliyor.", 3000)
        self._schedule_stats()

    def normalize_episodes(self):
        """Sezon/bölüm ifadelerini S01E01 biçimine çevirir.

        "Meghan 1. Sezon 1. Bölüm (2022)" -> "Meghan S01E01 (2022)"
        Zaten S01E01 biçiminde olanlara ve bölüm bilgisi olmayanlara dokunmaz.
        Tikli satır varsa yalnızca onlarda, yoksa görünür satırlarda çalışır.
        """
        checked = self._checked_rows()
        if checked:
            target_rows, scope = checked, "tikli %d satır" % len(checked)
        else:
            target_rows = [r for r in range(self.table.rowCount())
                           if not self.table.isRowHidden(r)]
            scope = "görünür %d satır" % len(target_rows)

        if not target_rows:
            QMessageBox.information(self, "Bilgi", "İşlenecek satır bulunamadı.")
            return

        # Önce kuru çalıştır: neyin değişeceğini hesapla
        changes = []
        for row in target_rows:
            old = self._get_cell(row, COL_NAME)
            new, changed = normalize_season_episode(old)
            if changed:
                changes.append((row, old, new))

        if not changes:
            QMessageBox.information(
                self, "Bilgi",
                "Dönüştürülecek sezon/bölüm ifadesi bulunamadı.\n\n"
                "(%s tarandı. Zaten S01E01 biçiminde olanlar atlanır.)" % scope
            )
            return

        preview = []
        for _r, old, new in changes[:12]:
            o = old if len(old) <= 52 else old[:49] + "..."
            nn = new if len(new) <= 52 else new[:49] + "..."
            preview.append("  %s\n     ->  %s" % (o, nn))
        if len(changes) > 12:
            preview.append("  ... ve %d tane daha" % (len(changes) - 12))

        reply = QMessageBox.question(
            self, "Sezon/Bölüm Biçimini Düzelt",
            "%s içinde %d satır dönüştürülecek:\n\n%s\n\nUygulansın mı?"
            % (scope, len(changes), "\n".join(preview)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._snapshot("Sezon/Bölüm biçimi (%d satır)" % len(changes))
        for row, _old, new in changes:
            self._set_cell(row, COL_NAME, new)
        self.statusBar().showMessage(
            "✅ %d satırın sezon/bölüm biçimi S01E01 olarak düzeltildi." % len(changes), 7000)

    def delete_duplicate_urls(self):
        """Aynı URL'ye sahip satırlardan ilkini bırakır, kalanlarını siler.

        'Tekrarları Sil'den FARKI: bu fonksiyon içerik ADINA bakmaz, yalnızca
        URL'yi karşılaştırır. Böylece aynı filmin farklı kaynaklardaki
        alternatif linkleri KORUNUR; sadece birebir aynı adres tekrarlanmışsa
        temizlenir.
        """
        visible_rows = [r for r in range(self.table.rowCount())
                        if not self.table.isRowHidden(r)]
        filtered = len(visible_rows) < self.table.rowCount()

        if not visible_rows:
            QMessageBox.information(self, "Bilgi", "Görünür satır bulunamadı.")
            return

        seen   = {}   # normalize_url -> ilk görülen satır
        to_del = []
        blanks = 0
        for row in visible_rows:
            raw = self._get_cell(row, COL_URL)
            key = _norm_url(raw)
            if not key:
                blanks += 1          # URL'si boş satırlar dokunulmadan bırakılır
                continue
            if key in seen:
                to_del.append(row)
            else:
                seen[key] = row

        if not to_del:
            QMessageBox.information(
                self, "Bilgi",
                "Aynı URL'ye sahip tekrar bulunamadı."
                + (f"\n\n({blanks} satırın URL'si boş, dikkate alınmadı.)" if blanks else "")
            )
            return

        preview_lines = []
        for row in to_del[:10]:
            nm = self._get_cell(row, COL_NAME) or "(adsız)"
            ur = self._get_cell(row, COL_URL)
            if len(ur) > 60:
                ur = ur[:57] + "..."
            preview_lines.append(f"  • {nm}\n      {ur}")
        if len(to_del) > 10:
            preview_lines.append(f"  ... ve {len(to_del)-10} tane daha")

        reply = QMessageBox.question(
            self, "Aynı URL'leri Sil",
            f"{'Filtreli listede ' if filtered else ''}"
            f"{len(to_del)} adet birebir aynı URL bulundu.\n"
            f"Her adresten 1 tanesi korunacak, kalanlar silinecek.\n"
            f"(Farklı URL'ye sahip alternatifler KORUNUR.)\n\n"
            f"Silineceklerden ilk 10'u:\n" + "\n".join(preview_lines) +
            "\n\nDevam etmek istiyor musunuz?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._snapshot(f"{len(to_del)} yinelenen URL sil")
            for row in sorted(to_del, reverse=True):
                self.table.removeRow(row)
            msg = (f"🗑️ {len(to_del)} yinelenen URL silindi. "
                   f"{len(seen)} benzersiz adres kaldı.")
            if blanks:
                msg += f" ({blanks} boş URL atlandı.)"
            self.statusBar().showMessage(msg, 6000)

    def delete_duplicates(self):
        """Aynı İSİMDEN ilk karşılaşılanı bırakır, kalanları siler.

        DİKKAT: URL'ye bakmaz. Aynı filmin farklı kaynaklardaki alternatif
        linkleri de silinir. Alternatifleri korumak için 'Aynı URL'yi Sil'
        (delete_duplicate_urls) kullanın.
        Filtre aktifse sadece görünen satırlarda çalışır."""

        visible_rows = [r for r in range(self.table.rowCount())
                        if not self.table.isRowHidden(r)]
        filtered = len(visible_rows) < self.table.rowCount()

        if not visible_rows:
            QMessageBox.information(self, "Bilgi", "Görünür satır bulunamadı.")
            return

        # Önce kaç tane olduğunu hesapla (önizleme için)
        seen   = {}   # normalize_ad → ilk görülen satır
        to_del = []
        for row in visible_rows:
            name = self._get_cell(row, COL_NAME)
            # Bölüm bilgisini (S01E01 vb.) çıkar, sadece dizi/film adına bak
            base = clean_name(name).lower().strip()
            if not base:
                continue
            if base in seen:
                to_del.append(row)
            else:
                seen[base] = row

        if not to_del:
            QMessageBox.information(self, "Bilgi", "Tekrar eden içerik bulunamadı.")
            return

        # Kullanıcıya önizleme göster
        preview_lines = []
        for row in to_del[:10]:
            preview_lines.append(f"  • {self._get_cell(row, COL_NAME)}")
        if len(to_del) > 10:
            preview_lines.append(f"  ... ve {len(to_del)-10} tane daha")

        reply = QMessageBox.question(
            self, "Tekrarları Sil",
            f"{'Filtreli listede ' if filtered else ''}"
            f"{len(to_del)} adet AYNI İSİMDE içerik bulundu.\n"
            f"Her birinden 1 tanesi korunacak, kalanlar silinecek.\n"
            f"⚠️ Farklı URL'ye sahip alternatifler de silinir.\n"
            f"   Onları korumak için '🔗 Aynı URL'yi Sil' kullanın.\n\n"
            f"Silineceklerden ilk 10'u:\n" + "\n".join(preview_lines) +
            "\n\nDevam etmek istiyor musunuz?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._snapshot(f"{len(to_del)} tekrar sil")
            for row in sorted(to_del, reverse=True):
                self.table.removeRow(row)
            self.statusBar().showMessage(
                f"🗑️ {len(to_del)} tekrar eden içerik silindi. {len(seen)} benzersiz içerik kaldı.", 6000
            )

    # ── Toplu Bul & Değiştir ──────────────────────────────────────────
    def find_replace(self):
        """Regex destekli toplu bul & değiştir penceresini açar."""
        if self.table.rowCount() == 0:
            QMessageBox.information(self, "Bilgi", "Önce bir liste yükleyin.")
            return
        checked_count = len(self._checked_rows())
        visible_count = sum(1 for r in range(self.table.rowCount())
                            if not self.table.isRowHidden(r))
        total_count   = self.table.rowCount()
        self._snapshot("Bul & Değiştir")
        dlg = FindReplaceDialog(self,
                                checked_count=checked_count,
                                visible_count=visible_count,
                                total_count=total_count)
        dlg.exec()   # modeless gibi davranır ama modal olarak açılır; Kapat ile çıkılır

    # ── User-Agent Ayarla (Toplu) ───────────────────────────────────────
    def set_user_agent_bulk(self):
        """Tikli satırlara (yoksa seçili satırlara) User-Agent atar/temizler.
        Hazır listeden seçilebilir ya da serbest metin girilebilir (editable combo)."""
        if self.table.rowCount() == 0:
            QMessageBox.information(self, "Bilgi", "Önce bir liste yükleyin.")
            return
        checked = self._checked_rows()
        if checked:
            target_rows = checked
            label = f"tikli {len(target_rows)} satır"
        else:
            target_rows = sorted(set(
                idx.row() for idx in self.table.selectionModel().selectedRows()
            ))
            label = f"seçili {len(target_rows)} satır"

        if not target_rows:
            QMessageBox.warning(self, "Uyarı",
                "User-Agent atamak için satır seçin.\n\n"
                "• Tik kutusu ile seçebilirsiniz\n"
                "• veya Ctrl+tıklama / Shift+tıklama ile satır seçebilirsiniz")
            return

        CLEAR_LABEL = "(Temizle / Kaldır)"
        items = [f"{lbl} — {val}" for lbl, val in UA_PRESETS] + [CLEAR_LABEL]
        # Tek satır hedeflendiyse mevcut değeri düzenleme alanına ön doldur
        prefill = self._get_cell(target_rows[0], COL_UA).strip() if len(target_rows) == 1 else ""
        if prefill and prefill not in items:
            items.insert(0, prefill)

        text, ok = QInputDialog.getItem(
            self, "User-Agent Ayarla",
            f"{label} için User-Agent seçin ya da kendi metninizi yazın:\n"
            f"(Oynatıcı bazı linkleri User-Agent olmadan açamıyorsa burada belirleyin)",
            items, 0, True
        )
        if not ok:
            return

        value = text.strip()
        for lbl, val in UA_PRESETS:
            if value == f"{lbl} — {val}":
                value = val
                break
        if value == CLEAR_LABEL:
            value = ""

        self._snapshot(f"User-Agent ayarla ({label})")
        for row in target_rows:
            self._set_cell(row, COL_UA, value)
        self.statusBar().showMessage(
            f"🕵️ {len(target_rows)} satıra User-Agent "
            f"{'temizlendi' if not value else 'uygulandı: ' + value}.", 6000
        )

    # ── Referer Ayarla (Toplu) ───────────────────────────────────────────
    def set_referer_bulk(self):
        """Tikli satırlara (yoksa seçili satırlara) Referer atar/temizler.
        Bazı yayın sunucuları (hotlink-koruması) URL'yi ancak doğru
        Referer header'ı ile açar — bu bilgi #EXTVLCOPT:http-referrer= olarak
        kaydedilir/okunur."""
        if self.table.rowCount() == 0:
            QMessageBox.information(self, "Bilgi", "Önce bir liste yükleyin.")
            return
        checked = self._checked_rows()
        if checked:
            target_rows = checked
            label = f"tikli {len(target_rows)} satır"
        else:
            target_rows = sorted(set(
                idx.row() for idx in self.table.selectionModel().selectedRows()
            ))
            label = f"seçili {len(target_rows)} satır"

        if not target_rows:
            QMessageBox.warning(self, "Uyarı",
                "Referer atamak için satır seçin.\n\n"
                "• Tik kutusu ile seçebilirsiniz\n"
                "• veya Ctrl+tıklama / Shift+tıklama ile satır seçebilirsiniz")
            return

        prefill = self._get_cell(target_rows[0], COL_REF).strip() if len(target_rows) == 1 else ""

        text, ok = QInputDialog.getText(
            self, "Referer Ayarla",
            f"{label} için Referer (kaynak site) adresini girin — örn: https://vidload.top/\n"
            f"Boş bırakıp Tamam'a basarsanız Referer temizlenir:",
            text=prefill
        )
        if not ok:
            return

        value = text.strip()
        self._snapshot(f"Referer ayarla ({label})")
        for row in target_rows:
            self._set_cell(row, COL_REF, value)
        self.statusBar().showMessage(
            f"🔗 {len(target_rows)} satıra Referer "
            f"{'temizlendi' if not value else 'uygulandı: ' + value}.", 6000
        )

    # ── Toplu Düzenle ─────────────────────────────────────────────────
    def bulk_edit(self):
        """Tikli / seçili veya tüm görünür satırlarda Tür, Logo, İçerik Adı'nı toplu değiştirir."""
        if self.table.rowCount() == 0:
            QMessageBox.information(self, "Bilgi", "Önce bir liste yükleyin.")
            return

        checked_rows = self._checked_rows()
        selected_rows = checked_rows if checked_rows else sorted(set(
            idx.row() for idx in self.table.selectionModel().selectedRows()
        ))
        visible_rows = [r for r in range(self.table.rowCount())
                        if not self.table.isRowHidden(r)]

        dlg = BulkEditDialog(self,
                             selected_count=len(selected_rows),
                             visible_count=len(visible_rows))
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.result:
            return
        self._snapshot("Toplu düzenle")

        fields = dlg.result
        scope  = dlg.scope()
        target_rows = selected_rows if scope == "selected" else visible_rows

        if not target_rows:
            QMessageBox.information(self, "Bilgi", "Uygulanacak satır bulunamadı.")
            return

        changed = 0
        for row in target_rows:
            for field in fields:
                col   = field["col"]
                value = field["value"]
                if col == COL_NAME and field.get("keep_ep"):
                    # Mevcut bölüm bilgisini (S01 E01 vb.) koru
                    original  = self._get_cell(row, col)
                    ep_info   = self._extract_episode_info(original)
                    new_val   = f"{value} {ep_info}".strip() if ep_info else value
                    self._set_cell(row, col, new_val)
                else:
                    self._set_cell(row, col, value)
            changed += 1

        self.statusBar().showMessage(
            f"✅ Toplu düzenleme tamamlandı — {changed} satır güncellendi.", 5000
        )

    # ── TMDB: API Test ────────────────────────────────────────────────
    def test_api_key(self):
        """Seçili satır varsa onun adıyla, yoksa 'The 100 S01 E01' ile test yapar.
        TMDB'den dönen tüm alanları gösterir."""
        row = self.table.currentRow()
        if row >= 0:
            test_name = self._get_cell(row, COL_NAME) or "The 100 S01 E01"
        else:
            test_name = "The 100 S01 E01"

        cleaned = clean_name(test_name)
        data    = tmdb_fetch(test_name)

        if data:
            QMessageBox.information(
                self, "✅ TMDB Test Sonucu",
                f"Test adı    : {test_name}\n"
                f"Temizlendi  : {cleaned}\n"
                f"─────────────────────────────\n"
                f"İçerik Adı  : {data.get('tr_name')}\n"
                f"Yıl         : {data.get('year')}\n"
                f"Grup        : {data.get('group')}\n"
                f"TMDB ID     : {data.get('tmdb_id')}\n"
                f"Logo URL    : {data.get('logo', '')[:60]}{'…' if len(data.get('logo',''))>60 else ''}\n"
                f"─────────────────────────────\n"
                f"Mevcut seçenekler: {self._get_opts()}"
            )
        else:
            QMessageBox.warning(
                self, "⚠️ Sonuç Bulunamadı",
                f"Test adı   : {test_name}\n"
                f"Temizlendi : {cleaned}\n\n"
                f"TMDB'den sonuç gelmedi.\n"
                f"API key doğru mu? İnternet bağlantısı var mı?"
            )

    # ── TMDB: API Key ─────────────────────────────────────────────────
    def set_api_key(self):
        global TMDB_API_KEY
        key, ok = QInputDialog.getText(self, "TMDB API Key", "API Key:", text=TMDB_API_KEY)
        if ok and key.strip():
            TMDB_API_KEY = key.strip()
            self.statusBar().showMessage("✅ API Key güncellendi!", 4000)

    # ── TMDB: Seçenekler ──────────────────────────────────────────────
    def tmdb_options_dialog(self):
        """Hangi alanların güncelleneceğini seçme penceresi"""
        dlg = QDialog(self)
        dlg.setWindowTitle("TMDB Güncelleme Seçenekleri")
        dlg.setMinimumWidth(320)
        if self.is_dark_mode:
            dlg.setStyleSheet("QDialog,QWidget{background:#2b2b2b;color:white}"
                              "QCheckBox{color:white} QPushButton{background:#444;color:white;"
                              "border:1px solid #666;padding:6px 16px}")
        vl = QVBoxLayout(dlg)
        vl.addWidget(QLabel("Otomatik doldurmada hangi alanlar güncellensin?"))

        opts = [
            ("logo",    "🖼️  Poster / Logo"),
            ("group",   "🏷️  Tür (group-title)"),
            ("tr_name", "📝  Türkçe İçerik Adı"),
            ("year",    "📅  Yıl"),
            ("tmdb_id", "🔢  TMDB ID"),
        ]
        self._opt_checks = {}
        saved = getattr(self, "_tmdb_opts",
                        {"logo": True, "group": True, "tr_name": True,
                         "year": True, "tmdb_id": True})
        for key, label in opts:
            cb = QCheckBox(label)
            cb.setChecked(saved.get(key, True))
            vl.addWidget(cb)
            self._opt_checks[key] = cb

        btn_row = QHBoxLayout()
        ok  = QPushButton("Kaydet"); ok.clicked.connect(dlg.accept)
        can = QPushButton("İptal");  can.clicked.connect(dlg.reject)
        btn_row.addStretch(); btn_row.addWidget(ok); btn_row.addWidget(can)
        vl.addLayout(btn_row)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._tmdb_opts = {k: cb.isChecked() for k, cb in self._opt_checks.items()}
            self.statusBar().showMessage("✅ Seçenekler kaydedildi.", 3000)

    def _get_opts(self):
        return getattr(self, "_tmdb_opts",
                       {"logo": True, "group": True, "tr_name": True,
                        "year": True, "tmdb_id": True})

    # ── TMDB: Manuel Arama (tek satır) ───────────────────────────────
    def tmdb_search_selected(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Bilgi", "Önce bir satır seçin.")
            return
        dlg = TMDBSearchDialog(self, self._get_cell(row, COL_NAME))
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_data:
            self._apply_tmdb_data(row, dlg.selected_data, self._get_opts())
            self.statusBar().showMessage("✅ TMDB bilgileri uygulandı.", 4000)

    # ── TMDB: Toplu Manuel Arama (işaretli/seçili satırlar) ──────────
    def tmdb_search_bulk(self):
        """
        Tikli (✔) satırlar varsa onları, yoksa tabloda ctrl/shift ile
        seçili satırları hedef alır.  Kullanıcı TEK BİR TMDB sonucu seçer
        ve bu sonuç tüm hedef satırlara uygulanır; her satırın bölüm
        bilgisi (S01 E01 gibi) ayrı ayrı korunur.
        """
        # Önce tikli satırlara bak
        target_rows = self._checked_rows()

        # Tikli yoksa, tabloda seçili (highlight) satırlara bak
        if not target_rows:
            selected_indexes = self.table.selectionModel().selectedRows()
            target_rows = sorted({idx.row() for idx in selected_indexes})

        # Hâlâ boşsa, sadece current row'u al
        if not target_rows:
            row = self.table.currentRow()
            if row < 0:
                QMessageBox.information(self, "Bilgi",
                    "Önce satırları tikleyin (✔ sütunu) veya Ctrl/Shift ile seçin.")
                return
            target_rows = [row]

        if len(target_rows) == 1:
            # Tek satır → normal tek satır araması gibi davran
            row = target_rows[0]
            dlg = TMDBSearchDialog(self, self._get_cell(row, COL_NAME))
            if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_data:
                self._apply_tmdb_data(row, dlg.selected_data, self._get_opts())
                self.statusBar().showMessage("✅ TMDB bilgileri uygulandı.", 4000)
            return

        # Çok satır → ilk satırın adını arama kutusuna doldur, başlıkta kaç satır olduğunu göster
        first_name = self._get_cell(target_rows[0], COL_NAME)
        dlg = TMDBSearchDialog(self, first_name, bulk_count=len(target_rows))
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.selected_data:
            return

        opts = self._get_opts()
        for row in target_rows:
            self._apply_tmdb_data(row, dlg.selected_data, opts)

        self.statusBar().showMessage(
            f"✅ TMDB bilgileri {len(target_rows)} satıra uygulandı.", 5000
        )

    # ── TMDB: Tümünü Doldur ───────────────────────────────────────────
    def tmdb_auto_all(self):
        if self.table.rowCount() == 0:
            QMessageBox.information(self, "Bilgi", "Önce bir liste yükleyin.")
            return

        # Sadece görünür satırları al (filtre aktifse sadece onlar)
        visible_rows = [r for r in range(self.table.rowCount())
                        if not self.table.isRowHidden(r)]
        total        = self.table.rowCount()
        filtered     = len(visible_rows) < total
        filtre_label = f"filtreli {len(visible_rows)}" if filtered else f"{total}"

        reply = QMessageBox.question(
            self, "TMDB Otomatik Doldur",
            f"{filtre_label} içerik işlenecek"
            f"{' (filtre aktif)' if filtered else ''}.\n\n"
            "Sadece eksik bilgisi olanları mı işleyelim?\n"
            "(Hayır = Hepsini yeniden çek)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No |
            QMessageBox.StandardButton.Cancel
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return

        opts = self._get_opts()
        rows_data = []
        for row in visible_rows:
            name = self._get_cell(row, COL_NAME)
            if not name:
                continue
            if reply == QMessageBox.StandardButton.Yes:
                # TMDB ❌ olan satırları her zaman tekrar dene
                is_failed = (self._tmdb_state(row) == "fail")
                missing = (
                    (opts.get("logo")    and not self._get_cell(row, COL_LOGO))   or
                    (opts.get("group")   and not self._get_cell(row, COL_GROUP))  or
                    (opts.get("year")    and not self._get_cell(row, COL_YEAR))   or
                    (opts.get("tmdb_id") and not self._get_cell(row, COL_TMDBID))
                )
                if not missing and not is_failed:
                    continue
            rows_data.append((row, name))

        if not rows_data:
            QMessageBox.information(self, "Bilgi", "Güncellenecek içerik bulunamadı.")
            return

        self._prog = QProgressDialog("TMDB'den bilgi çekiliyor…", "Durdur", 0, len(rows_data), self)
        self._prog.setWindowTitle("TMDB Otomatik Doldur")
        self._prog.setWindowModality(Qt.WindowModality.WindowModal)
        self._prog.show()

        self.tmdb_worker = TMDBWorker(rows_data, opts)
        self.tmdb_worker.result_ready.connect(lambda r, d: self._apply_tmdb_data(r, d, opts))
        self.tmdb_worker.not_found.connect(self._on_tmdb_not_found)
        self.tmdb_worker.progress.connect(self._on_prog)
        self.tmdb_worker.finished.connect(self._on_done)
        self.tmdb_worker.finished.connect(self.tmdb_worker.deleteLater)
        self._prog.canceled.connect(self.tmdb_worker.stop)
        self._prog_done = 0
        self.tmdb_worker.start()

    def _extract_episode_info(self, name: str) -> str:
        """Orijinal addan sadece bölüm/sezon bilgisini çıkarır ve normalize eder.
        'Lucifer S06-E10'  -> 'S06 E10'
        'The 100 S01 E01'  -> 'S01 E01'
        'Breaking Bad S03E07' -> 'S03 E07'
        'Dizi 3.Bölüm'    -> '3.Bölüm'
        """
        # S01E01 / S01 E01 / S01-E01 formatlarını yakala ve normalize et
        m = re.search(r'S(\d{1,2})[\s\-]*E(\d{1,2})', name, flags=re.IGNORECASE)
        if m:
            s = m.group(1).zfill(2)
            e = m.group(2).zfill(2)
            return f"S{s} E{e}"

        patterns = [
            r'\d{1,2}x\d{1,2}',
            r'\d+\.\s*(?:Bölüm|Sezon)',
            r'(?:Bölüm|Sezon|Episode|Season)\s*\d+',
        ]
        for pat in patterns:
            m = re.search(pat, name, flags=re.IGNORECASE)
            if m:
                return m.group(0).strip()
        return ""

    def _apply_tmdb_data(self, row, data, opts):
        # Orijinal adı al, bölüm bilgisini kaydet
        original_name = self._get_cell(row, COL_NAME)
        episode_info  = self._extract_episode_info(original_name)

        if opts.get("logo") and data.get("logo"):
            self._set_cell(row, COL_LOGO, data["logo"])
        if opts.get("group") and data.get("group"):
            self._set_cell(row, COL_GROUP, data["group"])

        # Yılı önce yaz (ad oluştururken lazım)
        year = data.get("year", "")
        if opts.get("year") and year:
            self._set_cell(row, COL_YEAR, year)

        # Ad: TMDB Türkçe adı + yıl (film) veya bölüm bilgisi (dizi)
        if opts.get("tr_name") and data.get("tr_name"):
            tmdb_title = data["tr_name"]
            # tmdb_title içindeki tüm (YYYY) parantezli yılları temizle (çift yıl önleme)
            tmdb_title_clean = re.sub(r'\s*\(\d{4}\)', "", tmdb_title).strip()
            if episode_info:
                # Dizi: "The 100 S01 E01" — yıl ada eklenmez, zaten yıl sütununda
                new_name = f"{tmdb_title_clean} {episode_info}"
            elif year:
                # Film: "Inception (2010)"
                new_name = f"{tmdb_title_clean} ({year})"
            else:
                new_name = tmdb_title_clean
            self._set_cell(row, COL_NAME, new_name)

        if opts.get("tmdb_id") and data.get("tmdb_id"):
            self._set_cell(row, COL_TMDBID, data["tmdb_id"])

        self._set_cell(row, COL_STATUS, "TMDB ✅")
        self._mark_tmdb(row, "ok")      # kalıcı işaret — link kontrolü ezemez
        it = self.table.item(row, COL_STATUS)
        if it:
            it.setBackground(QColor(0, 100, 60))
            it.setForeground(QColor("white"))

    def _on_tmdb_not_found(self, row):
        """TMDB'de bulunamayan satırı kırmızı olarak işaretle."""
        self._set_cell(row, COL_STATUS, "TMDB ❌")
        self._mark_tmdb(row, "fail")    # kalıcı işaret — link kontrolü ezemez
        it = self.table.item(row, COL_STATUS)
        if it:
            it.setBackground(QColor(160, 30, 30))
            it.setForeground(QColor("white"))

    def _on_prog(self, done, name):
        self._prog_done = done
        self._prog.setValue(done)
        self._prog.setLabelText(f"İşleniyor: {name}")

    def _on_done(self, success, fail):
        self._prog.close()
        QMessageBox.information(
            self, "TMDB Tamamlandı",
            f"✅ Bilgi bulundu : {success}\n❌ Bulunamadı  : {fail}"
        )

    # ── Bağlam menüsü ────────────────────────────────────────────────
    def _context_menu(self, pos):
        # Tikli / seçili satır sayısını hesapla
        checked = self._checked_rows()
        selected_indexes = self.table.selectionModel().selectedRows()
        sel_rows = sorted({idx.row() for idx in selected_indexes})
        bulk_count = len(checked) if checked else len(sel_rows)

        menu = QMenu()
        a1  = menu.addAction("➕ Üste Yeni Satır")
        a2  = menu.addAction("🗑️ Sil")
        menu.addSeparator()
        a_up   = menu.addAction("⬆️  Yukarı Taşı")
        a_dn   = menu.addAction("⬇️  Aşağı Taşı")
        a_top  = menu.addAction("⏫ En Üste Taşı")
        a_bot  = menu.addAction("⏬ En Alta Taşı")
        menu.addSeparator()
        a3  = menu.addAction("🎬 TMDB'de Ara (Bu Satır)")
        if bulk_count > 1:
            a3b = menu.addAction(f"🎬 TMDB'de Ara — Toplu ({bulk_count} satır)")
        else:
            a3b = None
        menu.addSeparator()
        a5  = menu.addAction("✏️ Toplu Düzenle (Seçili Satırlar)")
        a5b = menu.addAction("🔎 Bul & Değiştir")
        a5c = menu.addAction(f"🕵️ User-Agent Ayarla ({bulk_count if bulk_count else 'seçili'} satır)")
        a5d = menu.addAction(f"🔗 Referer Ayarla ({bulk_count if bulk_count else 'seçili'} satır)")
        a5e = menu.addAction(
            "👁️ UA/Referer Sütunlarını Gizle" if getattr(self, "_ua_ref_visible", False)
            else "👁️ UA/Referer Sütunlarını Göster")
        menu.addSeparator()
        a6  = menu.addAction("📤 Seçilileri Dışa Aktar")
        a7  = menu.addAction("📁 Klasöre Göre Dışa Aktar")
        menu.addSeparator()
        a4  = menu.addAction("🔁 Aynı Adı Sil (tekrarlar)")
        a4b = menu.addAction("🔗 Aynı URL'yi Sil (alternatifler korunur)")
        a4c = menu.addAction("🔢 Sezon/Bölüm Biçimini Düzelt (S01E01)")

        act = menu.exec(self.table.viewport().mapToGlobal(pos))
        if   act == a1:              self.add_new_row_top()
        elif act == a2:              self.delete_row()
        elif act == a_up:            self.move_rows_up()
        elif act == a_dn:            self.move_rows_down()
        elif act == a_top:           self.move_rows_top()
        elif act == a_bot:           self.move_rows_bottom()
        elif act == a3:              self.tmdb_search_selected()
        elif a3b and act == a3b:     self.tmdb_search_bulk()
        elif act == a4:              self.delete_duplicates()
        elif act == a4b:             self.delete_duplicate_urls()
        elif act == a4c:             self.normalize_episodes()
        elif act == a5:              self.bulk_edit()
        elif act == a5b:             self.find_replace()
        elif act == a5c:             self.set_user_agent_bulk()
        elif act == a5d:             self.set_referer_bulk()
        elif act == a5e:             self.toggle_ua_ref_columns()
        elif act == a6:              self.export_selected()
        elif act == a7:              self.export_by_group()


# ── Giriş noktası ─────────────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = IPTVEditor()
    w.show()
    sys.exit(app.exec())

# MediaBox Qt 2.4 — Kurulum

## 1) Arşivi açın

```bash
tar -xzf mediabox-qt-2.4.tar.gz
cd mediabox-qt
```

## 2) Gerekenleri kurun (CachyOS / Arch)

```bash
sudo pacman -S --needed python python-pyqt6 python-requests mpv ffmpeg yt-dlp
```

İsteğe bağlı (telefon kumandasının QR kodu için):

```bash
sudo pacman -S --needed python-qrcode python-pillow
```

> **Not:** `yt-dlp`'yi güncel tutun — YouTube bağlantıları için gerekiyor.
> `sudo pacman -Syu yt-dlp` ya da `pip install -U yt-dlp`

## 3) Çalıştırın

```bash
python3 mediabox_qt.py
```

Ortam eksikse program size ne kurulacağını söyler:

```bash
python3 mediabox_qt.py --check
```

---

## Menüye eklemek (isteğe bağlı)

```bash
mkdir -p ~/.local/share/applications
sed "s|/home/user|$PWD|" MediaBox.desktop > ~/.local/share/applications/MediaBox.desktop
update-desktop-database ~/.local/share/applications 2>/dev/null
```

Artık uygulama menüsünde **MediaBox** görünür.

---

## Verileriniz nerede

```
~/.local/share/mediabox-qt/
├── mediabox.json      ← listeleriniz, favoriler, izleme geçmişi
├── gorseller/         ← indirilen afişler
├── tmdb_onbellek/     ← TMDB yanıtları
├── shaderlar/
├── mpv.log
└── cokme.log          ← bir sorun olursa BURAYA bakın
```

Bu klasör silinmez; programı güncellediğinizde listeleriniz korunur.

---

## Bu sürümde ne değişti

### 2.4 — “Bunları da İzle” rafı
Ana sayfaya, kütüphanenizde **olmayan** yüksek puanlı film ve dizileri
gösteren yeni bir raf eklendi. Kartta puan rozeti, `⚡ OTO` (otomatik URL) ve
`×` (bu öneriyi gizle) düğmeleri var.
Ayarlar: **⚙ → ★ Bunları da İzle**

### 2.3 — “Geri tuşu çalışmıyor” düzeltmesi
Film izlerken sol üstteki **‹ Geri** düğmesinin çalışmamasına yol açan
6 ayrı neden bulundu ve düzeltildi. En önemlisi: ayrı pencere modunda
mpv'nin penceresi ekranda kalıyordu.

Ayrıntılar: `DEGISIKLIKLER_v2.3.md` ve `DEGISIKLIKLER_v2.4.md`

---

## Sorun çıkarsa

| Belirti | Deneyin |
|---|---|
| Video siyah / açılmıyor | `⚙ → Video çıkışı → x11` |
| Video ayrı pencerede açılıyor | `⚙ → Video modu → Gömülü` (X11 oturumu gerekir) |
| YouTube açılmıyor | `python3 mediabox_qt.py --yt-tani` |
| Genel tanı | `python3 mediabox_qt.py --tani` |
| Program kapanıyor | `~/.local/share/mediabox-qt/cokme.log` dosyasına bakın |

Wayland kullanıyorsanız gömülü video için X11 oturumu açmanız gerekebilir;
program bunu algılayıp otomatik olarak ayrı pencereye düşer.

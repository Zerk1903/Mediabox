#!/usr/bin/env bash
# M3U Playlist Editor başlatıcı — tek tıkla açılır
set -e
cd "$HOME"

VENV="$HOME/m3uenv"
SCRIPT="/home/yucel/Masaüstü/xxx/M3U_Playlist_Editor_25.py"

msg() {
    if command -v kdialog >/dev/null 2>&1; then
        kdialog --error "$1"
    elif command -v zenity >/dev/null 2>&1; then
        zenity --error --text="$1"
    else
        echo "$1"; sleep 5
    fi
}

if [ ! -f "$SCRIPT" ]; then
    msg "M3U_Playlist_Editor_25.py bulunamadı!\nBeklenen konum:\n$SCRIPT"
    exit 1
fi

# Sanal ortam yoksa oluştur
if [ ! -f "$VENV/bin/activate" ]; then
    python3 -m venv "$VENV"
fi

source "$VENV/bin/activate"

# Bağımlılıklar eksikse kur
if ! python3 -c "import PyQt6, requests" >/dev/null 2>&1; then
    python3 -m pip install --quiet PyQt6 requests
fi

exec python3 "$SCRIPT"

#!/usr/bin/env bash
# Macro Pro başlatıcı — tek tıkla açılır
set -e
cd "$HOME"

VENV="$HOME/macroenv"
SCRIPT="/home/yucel/Masaüstü/xxx/macro_pro.py"

# Hata gösterme (KDE -> kdialog, yoksa zenity, yoksa terminal)
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
    msg "macro_pro.py bulunamadı!\nBeklenen konum:\n$SCRIPT"
    exit 1
fi

# Sanal ortam yoksa oluştur
if [ ! -f "$VENV/bin/activate" ]; then
    python3 -m venv "$VENV"
fi

source "$VENV/bin/activate"

# Bağımlılıklar eksikse kur
if ! python3 -c "import pynput, pyautogui" >/dev/null 2>&1; then
    python3 -m pip install --quiet pynput pyautogui
fi

# Wayland uyarısı (KDE/CachyOS)
if [ "$XDG_SESSION_TYPE" = "wayland" ] && command -v kdialog >/dev/null 2>&1; then
    kdialog --sorry "Wayland oturumundasınız.\nMacro Pro fare/tuş yakalama için X11 ister.\nGiriş ekranında 'Plasma (X11)' seçin." &
fi

exec python3 "$SCRIPT"

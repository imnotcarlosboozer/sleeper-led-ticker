#!/bin/bash
# One-time setup on the Raspberry Pi. Run from this folder:  sudo ./install.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo ./install.sh"; exit 1
fi

APP_DIR="$(cd "$(dirname "$0")" && pwd)"

# Reuse an existing library checkout (e.g. one sitting next to this folder) if there is one.
if [[ -z "${LIB_DIR:-}" ]]; then
  LIB_DIR=/opt/rpi-rgb-led-matrix
  for d in "$APP_DIR/../rpi-rgb-led-matrix" /opt/rpi-rgb-led-matrix; do
    if [[ -d "$d/fonts" ]]; then LIB_DIR="$(cd "$d" && pwd)"; break; fi
  done
fi
echo "==> Using rpi-rgb-led-matrix at $LIB_DIR"

if python3 -c "import rgbmatrix" 2>/dev/null; then
  echo "==> rgbmatrix Python bindings already installed, skipping build"
else
  echo "==> Installing build tools"
  apt-get update
  apt-get install -y git python3-dev python3-pip cython3 cmake build-essential

  echo "==> Building rpi-rgb-led-matrix Python bindings (takes several minutes on a Pi 3)"
  if [[ ! -d "$LIB_DIR" ]]; then
    git clone --depth 1 https://github.com/hzeller/rpi-rgb-led-matrix.git "$LIB_DIR"
  fi
  python3 -m pip install --break-system-packages "$LIB_DIR"
fi

echo "==> Copying fonts"
mkdir -p "$APP_DIR/fonts"
cp "$LIB_DIR"/fonts/*.bdf "$APP_DIR/fonts/"

# Boot files live in /boot/firmware on Bookworm and later, /boot on older images.
BOOT=/boot/firmware
[[ -f $BOOT/config.txt ]] || BOOT=/boot

echo "==> Disabling onboard audio (it uses the same hardware timer as the LED driver)"
cat > /etc/modprobe.d/blacklist-rgb-matrix.conf <<'EOF'
blacklist snd_bcm2835
EOF
sed -i 's/^dtparam=audio=on/dtparam=audio=off/' "$BOOT/config.txt"

echo "==> Reserving CPU core 3 for the LED refresh thread (reduces flicker)"
if ! grep -q 'isolcpus=3' "$BOOT/cmdline.txt"; then
  sed -i '1 s/$/ isolcpus=3/' "$BOOT/cmdline.txt"
fi

echo "==> Installing systemd service"
sed "s#__APP_DIR__#$APP_DIR#g" "$APP_DIR/sleeper-ticker.service" > /etc/systemd/system/sleeper-ticker.service
systemctl daemon-reload
systemctl enable sleeper-ticker.service

echo
echo "Done. Reboot to apply the audio/CPU changes:  sudo reboot"
echo "The ticker starts automatically on boot. Logs:  journalctl -u sleeper-ticker -f"

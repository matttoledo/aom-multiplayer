#!/bin/bash
# Sobe o server em modo compartilhado (Bomba Patch em :1) e DEPOIS o PCSX2:
# os pads uinput precisam existir antes do PCSX2, senao ele nao recebe input.
set -e
cd /home/matt/aomstream
set -a; . ./.env; set +a
export AOM_MODE=shared AOM_SHARED_DISPLAY=:1 MONITOR_DISPLAY=:1 WIDTH=640 HEIGHT=480 DISPLAY=:1
# janela de render do PCSX2 abre em +1+25 no :1 (xwininfo)
export CAPTURE_OFFSET=1,25
for p in $(pgrep -x python3); do grep -q webrtc_server /proc/$p/cmdline && kill $p; done
pkill -x pcsx2-qt || true
for i in $(seq 1 20); do pgrep -x pcsx2-qt >/dev/null || break; sleep 0.5; done
sleep 1
setsid bash -c "nohup .venv/bin/python3 webrtc_server.py >> /tmp/webrtc.log 2>&1 < /dev/null &"
for i in $(seq 1 30); do [ "$(grep -c 'Name="aom-pad' /proc/bus/input/devices)" = 8 ] && break; sleep 0.5; done
sleep 2
cd /home/matt
HOME=/mnt/windows/home DISPLAY=:1 setsid bash -c "nohup /mnt/windows/pcsx2/squashfs-root/usr/bin/pcsx2-qt -nogui -fastboot /home/matt/roms/bomba-patch-2026.iso > /mnt/windows/uinput_test/pcsx2gui.log 2>&1 < /dev/null &"
echo "pads: $(grep -c 'Name="aom-pad' /proc/bus/input/devices)"

#!/bin/bash
# Sobe o Xorg com GPU de uma instancia: display :N no VT3, compartilhado.
# Uso (root): display.sh <N>     ex.: display.sh 3
#
# vt3 + -sharevts: todas as telas ficam no VT ativo. Um Xorg num VT inativo para de
# renderizar (medido 2026-10-14, STATUS.md). -novtswitch: nenhuma tela troca o VT.
set -e
N="$1"
VT="${VT:-3}"
CONF="${CONF:-/home/matt/aomstream/xorg-nvidia.conf}"
[ -n "$N" ] || { echo "uso: display.sh <N>"; exit 1; }
if [ -S "/tmp/.X11-unix/X$N" ] && pgrep -f "^/usr/lib/xorg/Xorg -config .* :$N " >/dev/null; then
  echo ":$N ja esta no ar"; exit 0
fi
setsid nohup /usr/lib/xorg/Xorg -config "$CONF" ":$N" "vt$VT" -sharevts -novtswitch \
  -nolisten tcp -logfile "/var/log/Xorg.aom$N.log" < /dev/null > /dev/null 2>&1 &
for _ in $(seq 20); do
  [ -S "/tmp/.X11-unix/X$N" ] && break
  sleep 0.5
done
[ -S "/tmp/.X11-unix/X$N" ] || { echo ":$N nao subiu (ver /var/log/Xorg.aom$N.log)"; exit 1; }
echo ":$N no ar, VT ativo $(cat /sys/class/tty/tty0/active)"

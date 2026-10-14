#!/bin/bash
# Teste: um 2o Xorg nvidia compartilhando o VT do :2 (-sharevts) renderiza nos dois?
PW=/home/matt/.sudo_asus
OUT=/tmp/sharevts
mkdir -p $OUT; rm -f $OUT/*

grab() {  # grab <display> <arquivo>
  ffmpeg -y -hide_banner -loglevel error -f x11grab -video_size 1280x720 -i "$1.0" -frames:v 1 "$2"
}
live() {  # live <display> <tag>: dois quadros com 0.5 s de intervalo
  grab "$1" $OUT/$2a.png; sleep 0.5; grab "$1" $OUT/$2b.png
  a=$(md5sum < $OUT/$2a.png); b=$(md5sum < $OUT/$2b.png)
  echo "$1 [$2]: $(identify -format '%k cores, media %[fx:mean]' $OUT/$2b.png) | quadros $([ "$a" = "$b" ] && echo IGUAIS || echo diferentes)"
}

echo "VT ativo antes: $(cat /sys/class/tty/tty0/active)"
DISPLAY=:2 glxgears >/dev/null 2>&1 & G2=$!
sleep 2
live :2 antes

sudo -S -p '' -- setsid nohup /usr/lib/xorg/Xorg -config /tmp/xorg-nvidia.conf :3 vt3 -sharevts -novtswitch -nolisten tcp -logfile /tmp/x3s.log < "$PW" > /dev/null 2>&1 &
sleep 5
X3=$(pgrep -f '^/usr/lib/xorg/Xorg -config /tmp/xorg-nvidia.conf :3 ')
echo "Xorg :3 pid: ${X3:-NAO SUBIU} | VT ativo: $(cat /sys/class/tty/tty0/active)"
grep -iE "using VT|sharevts|\(EE\)" /tmp/x3s.log 2>/dev/null | head -5

if [ -n "$X3" ]; then
  echo "glxinfo :3 -> $(DISPLAY=:3 glxinfo -B 2>&1 | grep -E 'OpenGL renderer|direct rendering' | tr '\n' ' ')"
  DISPLAY=:3 glxgears >/dev/null 2>&1 & G3=$!
  sleep 3
  live :2 junto
  live :3 junto
  kill $G3 2>/dev/null
  sudo -S -p '' -- kill "$X3" < "$PW"
  sleep 3
fi

live :2 depois
kill $G2 2>/dev/null
echo "VT ativo depois: $(cat /sys/class/tty/tty0/active) | :3 ainda vivo: $(pgrep -f '^/usr/lib/xorg/Xorg -config /tmp/xorg-nvidia.conf :3 ' || echo nao)"

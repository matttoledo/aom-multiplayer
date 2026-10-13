#!/bin/bash
NS="$1"; PREF="$2"; OUT="$3"
ip netns exec "$NS" su matt -c '
  cd "/home/matt/aom/Age of Mythology Extended Edition"
  DISPLAY=:2 WINEPREFIX="'"$PREF"'" WINEDEBUG=-all strace -q -e trace=write,read,send,recv -s 4000 -o '"$OUT"' wine AoMX.exe
' &
echo "pid $!"

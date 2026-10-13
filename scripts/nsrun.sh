#!/bin/bash
NS="$1"; PREF="$2"; OUT="$3"; DBG="$4"
ip netns exec "$NS" su matt -c '
  cd "/home/matt/aom/Age of Mythology Extended Edition"
  DISPLAY=:2 WINEPREFIX="'"$PREF"'" WINEDEBUG='"$DBG"' wine AoMX.exe
' > "$OUT" 2>&1 &
echo "pid $!"

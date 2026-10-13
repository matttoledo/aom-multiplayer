#!/bin/bash
NS="$1"; PREF="$2"; LOG="$3"
ip netns exec "$NS" su matt -c "cd '/home/matt/aom/Age of Mythology Extended Edition'; DISPLAY=:2 WINEPREFIX='$PREF' WINEDEBUG=+winsock setsid nohup wine AoMX.exe > $LOG 2>&1 < /dev/null &"

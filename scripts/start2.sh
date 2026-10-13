#!/bin/bash
PREF="$1"; LOG="$2"
su matt -c "cd '/home/matt/aom/Age of Mythology Extended Edition'; DISPLAY=:2 WINEPREFIX='$PREF' WINEDEBUG=-all setsid nohup wine AoMX.exe > $LOG 2>&1 < /dev/null &"

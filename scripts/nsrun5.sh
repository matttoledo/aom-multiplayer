#!/bin/bash
ip netns exec "$1" su matt -c '
  cd "/home/matt/aom/Age of Mythology Extended Edition"
  WINEPREFIX="/home/matt/.wine-aom-2" WINEDEBUG=-all wine netstat 2>&1
'

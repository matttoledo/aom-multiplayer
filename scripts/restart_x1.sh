#!/bin/bash
kill 54172 2>/dev/null
sleep 2
nohup /usr/lib/xorg/Xorg -config /home/matt/xorg-dummy.conf :1 -logfile /tmp/x1b.log -nolisten tcp > /dev/null 2>&1 &
sleep 6

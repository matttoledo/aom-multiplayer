pkill -f "AoMX.exe"
pkill -f "xorg-nvidia.conf"
sleep 4
Xorg -config /tmp/xorg-nvidia.conf :2 -novtswitch -logfile /root/x2.log &
sleep 12
DISPLAY=:2 xdotool getdisplaygeometry

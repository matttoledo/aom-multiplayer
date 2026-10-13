#!/bin/bash
PW="$HOME/.sudo_asus"
echo "=== trafego da instancia em ns1 (25s) ==="
sudo -S -p '' -- ip netns exec ns1 timeout 25 tcpdump -i veth1 -n -c 20 < "$PW" 2>&1 | head -20
echo "=== janelas no display :2 ==="
for w in $(DISPLAY=:2 xdotool search --name "Age of Mythology"); do
  echo "win $w: $(DISPLAY=:2 xdotool getwindowname $w) | $(DISPLAY=:2 xdotool getwindowgeometry $w | tr '\n' ' ')"
done

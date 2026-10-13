echo "=== glxinfo dentro de ns1, DISPLAY=:1 ==="
ip netns exec ns1 DISPLAY=:1 timeout 10 glxinfo 2>&1 | head -6
echo "=== glxinfo dentro de ns1, DISPLAY=:2 ==="
ip netns exec ns1 DISPLAY=:2 timeout 10 glxinfo 2>&1 | head -6

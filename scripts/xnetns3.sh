echo "=== ns1 -> :1 ==="
ip netns exec ns1 env DISPLAY=:1 timeout 10 glxinfo 2>&1 | head -5
echo "=== ns1 -> :2 ==="
ip netns exec ns1 env DISPLAY=:2 timeout 10 glxinfo 2>&1 | head -5

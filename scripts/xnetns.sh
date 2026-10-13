echo "=== socket filesystem do Xorg :1 ==="
ls -l /tmp/.X11-unix/ 2>/dev/null
echo "=== conectar a :1 de dentro de ns1 (socket de arquivo) ==="
ip netns exec ns1 DISPLAY=:1 timeout 10 glxinfo 2>&1 | grep -i -E "renderer|Cannot open|direct rendering" | head -4
echo "=== conectar a :2 de dentro de ns1 ==="
ip netns exec ns1 DISPLAY=:2 timeout 10 glxinfo 2>&1 | grep -i -E "renderer|Cannot open|direct rendering" | head -4

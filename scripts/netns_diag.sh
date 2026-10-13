echo "=== links existentes ==="
ip link show 2>/dev/null | grep -E "veth|br0" | head -10
echo "=== netns existentes ==="
ip netns list 2>/dev/null | head -5
echo "=== tentativa de criar veth1 ==="
ip link add testveth type veth peer name testveth-in 2>&1 | head -3
ip link show testveth 2>&1 | head -3

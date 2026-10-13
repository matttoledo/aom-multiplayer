set -e
for i in 1 2; do
  ip netns add ns$i 2>/dev/null || true
  if ! ip link show veth$i >/dev/null 2>&1; then
    ip link add veth$i type veth peer name veth$i-in
  fi
  ip link set veth$i-in master br0 2>/dev/null || true
  ip link set veth$i netns ns$i 2>/dev/null || true
  ip link set veth$i-in up
  ip netns exec ns$i ip link set veth$i up
  ip netns exec ns$i ip addr add 10.77.0.$i/24 dev veth$i 2>/dev/null || true
  ip netns exec ns$i ip link set lo up
done
echo "=== interfaces vistas dentro de ns1 ==="
ip netns exec ns1 ip -4 addr show 2>/dev/null | grep -E "inet " | head -5
echo "=== interfaces vistas dentro de ns2 ==="
ip netns exec ns2 ip -4 addr show 2>/dev/null | grep -E "inet " | head -5
echo "=== conectividade ns1 <-> ns2 ==="
ip netns exec ns1 ping -c 1 -W 2 10.77.0.2 2>&1 | tail -2
echo "=== broadcast entre namespaces ==="
ip netns exec ns2 python3 -c "
import socket
tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
tx.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
tx.sendto(b\"PROBE-ns2\", (\"10.77.0.255\", 2299))
print(\"ns2 enviou para 10.77.0.255\")
"
ip netns exec ns1 python3 -c "
import socket
rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
rx.bind((\"0.0.0.0\", 2299))
rx.settimeout(4)
try:
    print(\"ns1 RECEBEU:\", rx.recvfrom(64))
except socket.timeout:
    print(\"ns1 NAO recebeu\")
"

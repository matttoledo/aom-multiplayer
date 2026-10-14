#!/bin/bash
# Rede isolada do jogador N: netns nsN com vethN = 10.77.0.N/24 na br0 (a LAN de
# jogo; descoberta por broadcast na 2299, partida na 2300). Idempotente.
# Uso (root): netns.sh <N>
set -e
N="$1"
case "$N" in ''|*[!0-9]*) echo "uso: netns.sh <N>"; exit 1 ;; esac
ip netns add "ns$N" 2>/dev/null || true
if ! ip link show "veth$N-in" >/dev/null 2>&1; then
  ip link add "veth$N" type veth peer name "veth$N-in"
fi
ip link set "veth$N-in" master br0
ip link set "veth$N" netns "ns$N" 2>/dev/null || true
ip link set "veth$N-in" up
ip netns exec "ns$N" ip link set "veth$N" up
ip netns exec "ns$N" ip addr add "10.77.0.$N/24" dev "veth$N" 2>/dev/null || true
ip netns exec "ns$N" ip link set lo up
ip netns exec "ns$N" ip -4 route | grep -q '^default' || \
  ip netns exec "ns$N" ip route add default dev "veth$N"
echo "ns$N: 10.77.0.$N na br0"

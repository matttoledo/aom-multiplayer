#!/bin/bash
# Prepara e lanca a instancia do jogador N. Uso (root): instance.sh <N>
#
#   prefixo   /home/matt/.wine-aom-pN, clonado do .wine-aom-2 (winepulse; o -3 caiu em winealsa)
#   jogo      overlayfs: o diretorio do jogo e a camada de baixo, so leitura; o que a
#             instancia grava (Logs, savegame, Users, steam_emu.ini) vai para upper/
#   identidade  UserName=PLAYERN no steam_emu.ini da instancia (o emulador e por LAN)
#   rede      netns nsN (10.77.0.N na br0), tela :N+1
set -e
N="$1"
[ -n "$N" ] || { echo "uso: instance.sh <N>"; exit 1; }
U=matt
H=/home/$U
GAME="$H/aom/Age of Mythology Extended Edition"
BASE_PREFIX="$H/.wine-aom-2"
PREFIX="$H/.wine-aom-p$N"
INST="$H/aominst/p$N"
DISP="${DISP:-:$((N + 1))}"
NS="ns$N"

if [ ! -d "$PREFIX" ]; then
  echo "clonando prefixo -> $PREFIX"
  cp -a "$BASE_PREFIX" "$PREFIX"
fi

mkdir -p "$INST/upper" "$INST/work" "$INST/game"
chown -R $U:$U "$INST"
if ! mountpoint -q "$INST/game"; then
  mount -t overlay overlay -o "lowerdir=$GAME,upperdir=$INST/upper,workdir=$INST/work" "$INST/game"
fi
# identidade da instancia: escrever no merged faz copy-up so deste arquivo
sed -i "s/^UserName=.*/UserName=PLAYER$N/" "$INST/game/steam_emu.ini"
chown $U:$U "$INST/upper/steam_emu.ini"

ip netns exec "$NS" ip -4 route | grep -q '^default' || ip netns exec "$NS" ip route add default dev "veth$N"

LOG="/tmp/aom-p$N.log"
ip netns exec "$NS" su $U -c "cd '$INST/game'; DISPLAY=$DISP WINEPREFIX='$PREFIX' WINEDEBUG=-all setsid nohup wine AoMX.exe > $LOG 2>&1 < /dev/null &"
echo "jogador $N: $NS, tela $DISP, prefixo $PREFIX, jogo $INST/game, log $LOG"

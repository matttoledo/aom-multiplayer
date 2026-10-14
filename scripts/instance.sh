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
# Wine: o 11 do WineHQ (/opt/wine-stable) usa o ntsync do kernel quando /dev/ntsync
# existe; o 9.0 do Ubuntu nao tem sincronizacao rapida nenhuma (wineserver a ~25%
# e 300-430 mil trocas de contexto/s com dois jogos, medido 2026-10-14)
WINE="${WINE:-/opt/wine-stable/bin/wine}"
[ -x "$WINE" ] || WINE=wine
# Na primeira execucao num prefixo do 9.0 o Wine 11 atualiza o prefixo e abre o
# instalador do Wine Mono, que trava a subida ate alguem clicar. O jogo nao usa .NET
# nem o motor HTML: mscoree/mshtml desligados pulam o Mono e o Gecko.

if [ ! -d "$PREFIX" ]; then
  echo "clonando prefixo -> $PREFIX"
  cp -a "$BASE_PREFIX" "$PREFIX"
fi

mkdir -p "$INST/upper" "$INST/work" "$INST/game"
if ! mountpoint -q "$INST/game"; then
  # NUNCA chown -R aqui: com o overlay montado ele desce no jogo e o copy-up duplica
  # os 2.8 GB no upper (aconteceu em 2026-10-14: 1.2 GB antes de parar)
  chown $U:$U "$INST" "$INST/upper" "$INST/work" "$INST/game"
  mount -t overlay overlay -o "lowerdir=$GAME,upperdir=$INST/upper,workdir=$INST/work" "$INST/game"
fi
# identidade da instancia: escrever no merged faz copy-up so deste arquivo
sed -i "s/^UserName=.*/UserName=PLAYER$N/" "$INST/game/steam_emu.ini"
chown $U:$U "$INST/upper/steam_emu.ini"

ip netns exec "$NS" ip -4 route | grep -q '^default' || ip netns exec "$NS" ip route add default dev "veth$N"

LOG="/tmp/aom-p$N.log"
# su nao passa XDG_RUNTIME_DIR: sem ele o libpulse nao acha o socket, o Wine cai no
# winealsa e o som nunca chega ao aom_slotN (medido 2026-10-14). PULSE_SINK aponta o
# stream do jogo direto para o slot do jogador.
RUN="/run/user/$(id -u $U)"
ip netns exec "$NS" su $U -c "cd '$INST/game'; XDG_RUNTIME_DIR=$RUN PULSE_SERVER=unix:$RUN/pulse/native PULSE_SINK=aom_slot$N DISPLAY=$DISP WINEPREFIX='$PREFIX' WINEDEBUG=-all WINEDLLOVERRIDES=mscoree,mshtml= setsid nohup $WINE AoMX.exe > $LOG 2>&1 < /dev/null &"
echo "jogador $N: $NS, tela $DISP, prefixo $PREFIX, jogo $INST/game, wine $($WINE --version), log $LOG"

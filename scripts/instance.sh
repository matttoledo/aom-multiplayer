#!/bin/bash
# Prepara e lanca a instancia do jogador N. Uso (root): instance.sh <N> [jogo]
#
#   prefixo   /home/matt/.wine-<jogo>-pN, clonado do .wine-<jogo>-2 (winepulse; o -3 caiu em winealsa)
#   jogo      overlayfs: o diretorio do jogo e a camada de baixo, so leitura; o que a
#             instancia grava (Logs, savegame, Users, steam_emu.ini) vai para upper/
#   identidade  UserName=PLAYERN no steam_emu.ini da instancia (o emulador e por LAN)
#   rede      netns nsN (10.77.0.N na br0), tela :N+1
#
# O registro de cada jogo (diretorio, exe, prefixo base, DLLs do DXVK) esta em games.sh.
set -e
N="$1"
GAME="${2:-aom}"
[ -n "$N" ] || { echo "uso: instance.sh <N> [jogo]"; exit 1; }
U=matt
H=/home/$U
D="$(dirname "$(readlink -f "$0")")"
source "$D/games.sh"
game_cfg "$GAME" || { echo "jogo desconhecido: $GAME"; exit 1; }
PREFIX="$H/.wine-$GAME_SHORT-p$N"
INST="$H/aominst/$GAME_SHORT-p$N"
DISP="${DISP:-:$((N + 1))}"
NS="ns$N"
# Wine: o 11 do WineHQ (/opt/wine-stable) usa o ntsync do kernel quando /dev/ntsync
# existe; o 9.0 do Ubuntu nao tem sincronizacao rapida nenhuma (wineserver a ~25%
# e 300-430 mil trocas de contexto/s com dois jogos, medido 2026-10-14)
WINE="${WINE:-/opt/wine-stable/bin/wine}"
[ -x "$WINE" ] || WINE=wine
# CPU do jogo (medido 2026-10-14, instancia 3, menu a 30 fps):
#   wined3d (D3D11 -> OpenGL)          129% | thread principal 99%
#   DXVK (D3D11 -> Vulkan)             108% | 99%
#   DXVK + yieldsleep 50 us             22% | 13%, ainda 30 quadros/s na tela
# O laco de espera e do proprio jogo (sched_yield + getrusage, ~110 mil/s cada);
# yieldsleep (scripts/yieldsleep.c) faz o yield dormir. So yieldsleep, sem DXVK,
# derrubava o fps (a thread principal esperava a wined3d_cs).
# AOM_YIELD_US=0 desliga o yieldsleep; AOM_DXVK=0 volta ao wined3d.
AOM_YIELD_US="${AOM_YIELD_US:-50}"
PRELOAD=""
if [ "$AOM_YIELD_US" != 0 ] && [ -f "$D/yieldsleep32.so" ]; then
  PRELOAD="AOM_YIELD_US=$AOM_YIELD_US LD_PRELOAD=$D/yieldsleep32.so"
fi
OVERRIDES="mscoree,mshtml="
DXVK=""
if [ "${AOM_DXVK:-1}" != 0 ]; then
  for f in $GAME_DXVK; do
    [ -f "$D/dxvk/x32/$f.dll" ] && { DXVK=1; break; }
  done
  if [ -n "$DXVK" ]; then
    for f in $GAME_DXVK; do OVERRIDES="$OVERRIDES;$f=n,b"; done
  fi
fi
# Na primeira execucao num prefixo do 9.0 o Wine 11 atualiza o prefixo e abre o
# instalador do Wine Mono, que trava a subida ate alguem clicar. O jogo nao usa .NET
# nem o motor HTML: mscoree/mshtml desligados pulam o Mono e o Gecko.

if [ ! -d "$PREFIX" ]; then
  echo "clonando prefixo -> $PREFIX"
  cp -a "$GAME_PREFIX_BASE" "$PREFIX"
fi

if [ ! -d "$GAME_DIR" ]; then
  echo "jogo $GAME_SHORT: $GAME_DIR nao existe (falta extrair?)"
  exit 1
fi

mkdir -p "$INST/upper" "$INST/work" "$INST/game"
if ! mountpoint -q "$INST/game"; then
  # NUNCA chown -R aqui: com o overlay montado ele desce no jogo e o copy-up duplica
  # os 2.8 GB no upper (aconteceu em 2026-10-14: 1.2 GB antes de parar)
  chown $U:$U "$INST" "$INST/upper" "$INST/work" "$INST/game"
  mount -t overlay overlay -o "lowerdir=$GAME_DIR,upperdir=$INST/upper,workdir=$INST/work" "$INST/game"
fi
# identidade da instancia: escrever no merged faz copy-up so deste arquivo.
# So o AoM tem steam_emu.ini; o NFSU nao tem emulador de Steam.
if [ -f "$INST/game/steam_emu.ini" ]; then
  sed -i "s/^UserName=.*/UserName=PLAYER$N/" "$INST/game/steam_emu.ini"
  chown $U:$U "$INST/upper/steam_emu.ini"
fi

ip netns exec "$NS" ip -4 route | grep -q '^default' || ip netns exec "$NS" ip route add default dev "veth$N"

if [ -n "$DXVK" ]; then
  # DLLs de 32 bits do DXVK no diretorio de sistema de 32 bits do prefixo
  # (syswow64 num prefixo de 64 bits, system32 num de 32, como os p1-p3)
  SYS="$PREFIX/drive_c/windows/syswow64"
  [ -d "$SYS" ] || SYS="$PREFIX/drive_c/windows/system32"
  for f in $GAME_DXVK; do
    [ -f "$D/dxvk/x32/$f.dll" ] || continue
    cmp -s "$D/dxvk/x32/$f.dll" "$SYS/$f.dll" || install -o $U -g $U -m 644 "$D/dxvk/x32/$f.dll" "$SYS/$f.dll"
  done
fi

LOG="/tmp/aom-p$N.log"
# su nao passa XDG_RUNTIME_DIR: sem ele o libpulse nao acha o socket, o Wine cai no
# winealsa e o som nunca chega ao aom_slotN (medido 2026-10-14). PULSE_SINK aponta o
# stream do jogo direto para o slot do jogador.
RUN="/run/user/$(id -u $U)"
ip netns exec "$NS" su $U -c "cd '$INST/game'; XDG_RUNTIME_DIR=$RUN PULSE_SERVER=unix:$RUN/pulse/native PULSE_SINK=aom_slot$N DISPLAY=$DISP WINEPREFIX='$PREFIX' WINEDEBUG=-all WINEDLLOVERRIDES='$OVERRIDES' $PRELOAD setsid nohup $WINE '$GAME_EXE' > $LOG 2>&1 < /dev/null &"
echo "jogador $N ($GAME_SHORT): $NS, tela $DISP, prefixo $PREFIX, jogo $INST/game, wine $($WINE --version)${PRELOAD:+, yield ${AOM_YIELD_US}us}, dlls $OVERRIDES, log $LOG"

#!/bin/bash
# Sobe tudo o que o jogador N precisa: rede (nsN), tela (:N+1) e o jogo. Idempotente:
# o que ja esta no ar fica como esta. Chamado pelo servidor quando um convite aponta
# para uma vaga sem jogo, via sudo sem senha restrito a este arquivo.
#
# Instalado root-owned em /usr/local/lib/aom/ (com netns.sh, display.sh, instance.sh,
# games.sh): a regra do sudoers nao pode apontar para um arquivo que o usuario edita.
# Uso (root): spawn.sh <N> [jogo]    N de 2 a 8
set -e
N="$1"
GAME="${2:-aom}"
case "$N" in [2-8]) ;; *) echo "uso: spawn.sh <N> [jogo] (2-8)"; exit 1 ;; esac
D="$(dirname "$(readlink -f "$0")")"
source "$D/games.sh"
game_cfg "$GAME" || { echo "jogo desconhecido: $GAME"; exit 1; }
bash "$D/netns.sh" "$N"
bash "$D/display.sh" "$((N + 1))"
# o jogo do jogador N e o exe do jogo cujo ambiente tem o prefixo dele. O [X] no comeco
# do padrao e para o pgrep nao casar com a propria linha de comando.
game_running() {
  local p first
  first="$(printf '%s' "$GAME_EXE" | cut -c1)"
  for p in $(pgrep -f "[${first}]${GAME_EXE}"); do
    tr '\0' '\n' < "/proc/$p/environ" 2>/dev/null | grep -qx "WINEPREFIX=/home/matt/.wine-$GAME_SHORT-p$N" && return 0
  done
  return 1
}
if game_running; then
  echo "jogo do jogador $N ja esta no ar"
  exit 0
fi
bash "$D/instance.sh" "$N" "$GAME"

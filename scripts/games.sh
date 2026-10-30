#!/bin/bash
# Registro de jogos, metade shell: onde o jogo esta, qual exe rodar, qual prefixo
# base clonar e quais DLLs do DXVK instalar. A metade Python esta em games.py.
#
# Uso: source games.sh; game_cfg aom
#      -> GAME_DIR GAME_EXE GAME_PREFIX_BASE GAME_DXVK GAME_SHORT

game_cfg() {
  case "$1" in
    aom)
      GAME_SHORT=aom
      GAME_DIR="/home/matt/aom/Age of Mythology Extended Edition"
      GAME_EXE="AoMX.exe"
      GAME_PREFIX_BASE="/home/matt/.wine-aom-2"
      # AoMX desenha por D3D11
      GAME_DXVK="d3d11 dxgi d3d10core"
      ;;
    nfsu)
      GAME_SHORT=nfsu
      # O jogo esta na particao do Windows (/dev/nvme0n1p4, montada rw como ntfs3):
      # a particao raiz de ext4 esta cheia (91 GB, 17 MB livres em 2026-10-15) e o
      # NFSU sao 1,7 GB. Overlay sobre ntfs3 funciona, medido: leitura do lowerdir e
      # copy-up no upper de ext4. O mount precisa estar rw e com uid/gid do matt -
      # o mount antigo era fuseblk ro com o daemon morto.
      GAME_DIR="/mnt/windows/games/Need for Speed Underground 2"
      GAME_EXE="SPEED2.EXE"
      GAME_PREFIX_BASE="/home/matt/.wine-nfsu-2"
      # RenderWare desenha por D3D8/D3D9. Sem d3d9.dll o DXVK nao e chamado e o
      # jogo cai no wined3d (OpenGL), medido em 2026-10-14: 129% de CPU no menu
      # contra 108% com DXVK.
      GAME_DXVK="d3d8 d3d9 dxgi"
      ;;
    *)
      return 1
      ;;
  esac
}

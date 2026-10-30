"""Registro de jogos, metade Python.

O servidor nao conhece o jogo: ele captura uma tela X11 e injeta input na janela. O
que muda por jogo e (a) qual instancia o spawn.sh sobe, (b) qual janela procurar,
(c) se varios jogadores jogam juntos ou apenas assistem.

Os campos que o shell precisa (diretorio, exe, prefixo, DLLs do DXVK) estao em
games.sh, que instance.sh e spawn.sh sourceiam. Os dois arquivos sao o mesmo
registro: mudar um sem o outro deixa a pagina mostrando um jogo e o spawn subindo
outro.
"""

GAMES = {
    "aom": {
        "label": "Age of Mythology: Extended Edition",
        "short": "AoM",
        "window": "age of mythology",
        # O multiplayer e do proprio jogo, entre instancias: identidade por IP
        # (BSocksProtocol::getLocalIP), descoberta por broadcast na 2299, partida
        # na 2300. Cada jogador precisa da sua instancia, tela e slot.
        "multiplayer": True,
    },
    "nfsu": {
        "label": "Need for Speed Underground 2",
        "short": "NFSU2",
        "window": "need for speed underground 2",
        # Sem multiplayer LAN (o online da EA e morto). Um motorista na instancia,
        # os outros assistem a mesma tela e o mesmo slot; so o input do motorista
        # e injetado, senao dois espectadores brigariam pelo mesmo volante.
        "multiplayer": False,
    },
}

DEFAULT_GAME = "aom"

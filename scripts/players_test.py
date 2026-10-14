"""Prova do mapa token -> (tela, slot) no servidor unico. Roda no asus.

Casos: jogador 1 entra na tela dele; jogador 2 sem tela recebe "fora do ar";
token errado e negado; o mesmo jogador reconectando derruba a sessao antiga.
Os tokens vem do .env e nunca sao impressos.
"""
import asyncio
import json
import sys

import websockets

URL = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8082"
env = {}
for line in open("/home/matt/aomstream/.env"):
    if "=" in line:
        k, v = line.strip().split("=", 1)
        env[k] = v
T1 = env.get("AOM_TOKEN_1") or env.get("AOM_SESSION_TOKEN", "")
T2 = env.get("AOM_TOKEN_2", "")


async def hello(tok):
    ws = await websockets.connect(URL + "/?t=" + tok)
    await ws.send(json.dumps({"t": "hello", "decoder": "webrtc"}))
    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
    return ws, m


async def main():
    ws1, m = await hello(T1)
    print("jogador 1:", m.get("t"))
    _, m = await hello(T2)
    print("jogador 2 (sem tela):", m.get("t"), "|", m.get("msg"))
    _, m = await hello("errado")
    print("token errado:", m.get("t"), "|", m.get("msg"))
    ws1b, m = await hello(T1)
    print("jogador 1 de novo:", m.get("t"))
    try:
        await asyncio.wait_for(ws1.recv(), timeout=5)
        print("sessao antiga: continuou aberta (FALHA)")
    except websockets.exceptions.ConnectionClosed as e:
        print("sessao antiga: fechada com", e.rcvd.code if e.rcvd else None)
    await ws1b.close()


asyncio.run(main())

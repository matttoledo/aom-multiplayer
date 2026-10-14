"""Queda do WebSocket no meio da sessao: o que o servidor registra e se o jogo segue.

Conecta como cliente WebRTC (com DataChannel "input", como a pagina), espera o
video chegar e entao derruba o WebSocket de um dos jeitos:

  close  close normal do cliente          -> log: iniciado_por=cliente
  abort  TCP some sem close (tunel caiu)  -> log: iniciado_por=nenhum, code=1006

Depois mede se o video continua chegando e se o DataChannel ainda responde (ping),
que e o que mantem a partida viva sem o tunel. Nao injeta input no jogo.
Roda no asus. Uso: python3 session_test.py close|abort [ws://127.0.0.1:8082]
O token vem do .env; o valor nunca e impresso.
"""
import asyncio
import json
import sys
import time

import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.rtcconfiguration import RTCConfiguration

ENV = "/home/matt/aomstream/.env"
HOW = sys.argv[1] if len(sys.argv) > 1 else "abort"
URL = sys.argv[2] if len(sys.argv) > 2 else "ws://127.0.0.1:8082"

tok = ""
for line in open(ENV):
    if line.startswith("AOM_SESSION_TOKEN="):
        tok = line.split("=", 1)[1].strip()


async def count_frames(track, seconds):
    n, t0 = 0, time.time()
    while time.time() - t0 < seconds:
        try:
            await asyncio.wait_for(track.recv(), timeout=2)
            n += 1
        except Exception:
            break
    return n


async def main():
    ws = await websockets.connect(URL + "/?t=" + tok, max_size=None)
    await ws.send(json.dumps({"t": "hello", "decoder": "webrtc"}))
    m = json.loads(await ws.recv())
    if m.get("t") != "hello-ok":
        print("hello negado:", m)
        return
    pc = RTCPeerConnection(RTCConfiguration())
    pc.addTransceiver("video", direction="recvonly")
    pc.addTransceiver("audio", direction="recvonly")
    dc = pc.createDataChannel("input")
    pongs = []
    dc.on("message", lambda msg: pongs.append(json.loads(msg)))
    loop = asyncio.get_running_loop()
    got = loop.create_future()
    opened = loop.create_future()
    dc.on("open", lambda: opened.done() or opened.set_result(True))

    @pc.on("track")
    def on_track(track):
        if track.kind == "video" and not got.done():
            got.set_result(track)

    await pc.setLocalDescription(await pc.createOffer())
    await ws.send(json.dumps({"t": "offer", "sdp": pc.localDescription.sdp}))
    while True:
        d = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
        if d.get("t") == "answer":
            break
    print("answer com m=application (DataChannel):", "m=application" in d["sdp"])
    await pc.setRemoteDescription(RTCSessionDescription(d["sdp"], "answer"))
    track = await asyncio.wait_for(got, timeout=20)
    print("quadros de video em 3 s, com WebSocket:", await count_frames(track, 3))
    try:
        await asyncio.wait_for(opened, timeout=5)
        dc_ok = True
    except asyncio.TimeoutError:
        dc_ok = False
    print("DataChannel aberto:", dc_ok)

    if HOW == "close":
        await ws.close()
    else:
        ws.transport.abort()
    print(f"WebSocket derrubado ({HOW}) as {time.strftime('%H:%M:%S')}")
    await asyncio.sleep(1)
    frames = await count_frames(track, 5)
    if dc_ok and dc.readyState == "open":
        dc.send(json.dumps({"t": "ping", "ts": 0}))
        await asyncio.sleep(1)
    print(f"sem WebSocket: quadros em 5 s = {frames} | pong pelo DataChannel = "
          f"{any(p.get('t') == 'pong' for p in pongs)} | estado {pc.connectionState}")
    await pc.close()
    await asyncio.sleep(2)


asyncio.run(main())

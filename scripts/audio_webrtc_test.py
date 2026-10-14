"""Prova do audio Opus no caminho aiortc: conecta como cliente, recebe a faixa de
audio e mede quadros e pico. Roda no asus (unica maquina com aiortc).

Uso: python3 audio_webrtc_test.py [ws://127.0.0.1:8082]
O token vem do .env; o valor nunca e impresso.
"""
import array
import asyncio
import json
import sys
import time

import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.rtcconfiguration import RTCConfiguration

ENV = "/home/matt/aomstream/.env"
URL = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8082"

tok = ""
for line in open(ENV):
    if line.startswith("AOM_SESSION_TOKEN="):
        tok = line.split("=", 1)[1].strip()


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
    got = asyncio.get_running_loop().create_future()

    @pc.on("track")
    def on_track(track):
        if track.kind == "audio" and not got.done():
            got.set_result(track)

    await pc.setLocalDescription(await pc.createOffer())
    await ws.send(json.dumps({"t": "offer", "sdp": pc.localDescription.sdp}))
    while True:
        d = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
        if d.get("t") == "answer":
            break
    ans = d["sdp"]
    print("answer com m=audio:", "m=audio" in ans, "| opus:", "opus/48000" in ans.lower())
    msids = {l.split()[0] for l in ans.splitlines() if l.startswith("a=msid:")}
    print("streams distintos no answer (a=msid):", len(msids))
    await pc.setRemoteDescription(RTCSessionDescription(ans, "answer"))

    track = await asyncio.wait_for(got, timeout=20)
    frames, peak, nonzero = 0, 0, 0
    t0 = time.time()
    while time.time() - t0 < 6:
        f = await asyncio.wait_for(track.recv(), timeout=5)
        frames += 1
        s = array.array("h", bytes(f.planes[0]))
        p = max(abs(x) for x in s) if s else 0
        peak = max(peak, p)
        nonzero += p > 300
    print(f"estado: {pc.connectionState} | quadros de audio em 6 s: {frames} "
          f"| taxa {f.sample_rate} Hz | pico {peak} | quadros com som {nonzero}")
    await pc.close()
    await ws.close()


asyncio.run(main())

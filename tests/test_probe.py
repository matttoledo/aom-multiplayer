import asyncio
import json
import time

import av
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription

WS = "ws://127.0.0.1:8081"

# centro do marcador: janela 1200,680 64x32 -> pixel (1232, 696)
MARK_X, MARK_Y = 1232, 696
MAGENTA = (255, 0, 255)

got = {"frames": 0, "drawn": None, "detect": None, "start": None,
       "first": None, "first_pts": None, "last": None}


def is_magenta(p):
    # o H.264 e perdas: o magenta chega como (249,0,254), nao (255,0,255)
    return p[0] > 200 and p[1] < 60 and p[2] > 200


def pixel(frame):
    """Le o pixel do marcador sem numpy: reformat para rgb24 e leitura direta do plano."""
    f = frame.reformat(width=1280, height=720, format="rgb24")
    buf = bytes(f.planes[0])
    off = (MARK_Y * 1280 + MARK_X) * 3
    return tuple(buf[off:off + 3])


async def main():
    pc = RTCPeerConnection()
    pc.addTransceiver("video", direction="recvonly")
    ws = await websockets.connect(WS)

    async def send(obj):
        await ws.send(json.dumps(obj))

    @pc.on("track")
    def on_track(track):
        async def pump():
            while True:
                try:
                    f = await track.recv()
                except Exception as e:
                    print("track terminou:", e, flush=True)
                    break
                n = got["frames"]
                got["frames"] += 1
                now = time.time()
                if n == 0:
                    got["first"] = now
                    got["first_pts"] = f.pts
                got["last"] = now
                if got["drawn"] and is_magenta(pixel(f)) and got["detect"] is None:
                    got["detect"] = now
                    print("marcador detectado no frame", n, flush=True)
                    break
                if got["drawn"] and n % 30 == 0:
                    print("frame", n, "pixel no marcador:", pixel(f), flush=True)
        asyncio.create_task(pump())

    @pc.on("connectionstatechange")
    def on_state():
        print("estado:", pc.connectionState, flush=True)
        if pc.connectionState == "connected":
            asyncio.create_task(send({"t": "probe"}))

    await send({"t": "hello", "decoder": "webrtc"})
    print("hello-ok:", json.loads(await asyncio.wait_for(ws.recv(), timeout=5)))

    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    await send({"t": "offer", "sdp": pc.localDescription.sdp})
    t0 = time.time()
    while time.time() - t0 < 40 and got["detect"] is None:
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        except (asyncio.TimeoutError, json.JSONDecodeError):
            continue
        if m.get("t") == "answer":
            await pc.setRemoteDescription(RTCSessionDescription(m["sdp"], "answer"))
            if m.get("serverStart"):
                got["start"] = m["serverStart"]
        elif m.get("t") == "start":
            got["start"] = m["serverStart"]
        elif m.get("t") == "probe-at":
            got["drawn"] = m["drawn"]
            print("servidor desenhou o marcador em", got["drawn"], flush=True)

    # espera mais um pouco pela deteccao
    t1 = time.time()
    while time.time() - t1 < 8 and got["detect"] is None:
        await asyncio.sleep(0.1)

    print("frames recebidos:", got["frames"])
    if got["detect"] and got["drawn"]:
        print("LATENCIA PROBE (desenho->chegada ao decoder):",
              round((got["detect"] - got["drawn"]) * 1000), "ms")
    if got["frames"] and got["start"] and got["first"]:
        print("latencia do 1o frame (chegada - captura do frame 0):",
              round((got["first"] - got["start"] - (got["first_pts"] or 0) / 30) * 1000), "ms")
    await pc.close()
    await ws.close()


asyncio.run(main())

import asyncio
import json
import time

import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription

WS = "ws://127.0.0.1:8081"


async def main():
    pc = RTCPeerConnection()
    pc.addTransceiver("video", direction="recvonly")
    ws = await websockets.connect(WS)

    got = {"frames": 0, "first": None, "last": None, "start": None,
          "ice": 0, "sent": False}

    async def send(obj):
        await ws.send(json.dumps(obj))

    @pc.on("icecandidate")
    def on_ice(c):
        # aiortc nao faz trickle: candidatos aparecem no SDP apos gathering
        if c and c.candidate:
            got["ice"] += 1
            asyncio.create_task(send({"t": "ice", "candidate": {"candidate": c.candidate}}))

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
                if n == 0:
                    got["first"] = time.time()
                    got["first_media"] = f.pts
                got["last"] = time.time()
        asyncio.create_task(pump())

    @pc.on("connectionstatechange")
    def on_state():
        print("estado:", pc.connectionState, flush=True)

    await send({"t": "hello", "decoder": "webrtc"})
    print("hello-ok:", json.loads(await asyncio.wait_for(ws.recv(), timeout=5)))

    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    # aiortc gather em setLocalDescription -> manda localDescription, nao o offer cru
    print("candidatos na oferta:", pc.localDescription.sdp.count("a=candidate"), flush=True)
    await send({"t": "offer", "sdp": pc.localDescription.sdp})

    t0 = time.time()
    while time.time() - t0 < 30:
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=8))
        except (asyncio.TimeoutError, json.JSONDecodeError):
            continue
        if m.get("t") == "answer":
            await pc.setRemoteDescription(RTCSessionDescription(m["sdp"], "answer"))
            if m.get("serverStart"):
                got["start"] = m["serverStart"]
            n_cand = m["sdp"].count("a=candidate")
            print("answer: candidatos no SDP =", n_cand, flush=True)
        elif m.get("t") == "start":
            got["start"] = m["serverStart"]

    print("frames recebidos:", got["frames"])
    if got["frames"] and got["start"]:
        dur = got["last"] - got["first"]
        print("fps no cliente:", round(got["frames"] / dur, 1))
        print("latencia do 1o frame:", round((got["first"] - got["last"]) * 1000), "ms")
        print("latencia media (chegada - captura):",
              round((got["last"] - got["start"] - got["frames"] / 30) * 1000), "ms")
    await pc.close()
    await ws.close()


asyncio.run(main())

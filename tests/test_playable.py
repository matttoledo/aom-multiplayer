import asyncio
import json
import os
import statistics
import time

import av
import aiortc.codecs
import aiortc.rtcrtpsender
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription

WS = "ws://127.0.0.1:8081"
MARK_X, MARK_Y = 1232, 696

# imitar o navegador forcando H264 (aiortc nao tem setCodecPreferences)
_orig_caps = aiortc.codecs.get_capabilities


def caps(kind):
    c = _orig_caps(kind)
    c.codecs = ([x for x in c.codecs if x.mimeType.lower() == "video/h264"] +
                [x for x in c.codecs if x.mimeType.lower() != "video/h264"])
    return c


aiortc.codecs.get_capabilities = caps
aiortc.rtcrtptransceiver.get_capabilities = caps
aiortc.rtcrtpsender.get_capabilities = caps

# createOffer usa a lista estatica CODECS[kind]; reordenar e o que define a ordem
# do offer, igual faz o navegador com setCodecPreferences.
# Ojo: aiortc escreve "video/H264" com H maiusculo -> comparar case-insensitive.
_video = aiortc.codecs.CODECS["video"]
aiortc.codecs.CODECS["video"] = ([c for c in _video if c.mimeType.lower() == "video/h264"] +
                                  [c for c in _video if c.mimeType.lower() != "video/h264"])

# capacidade do jitter buffer: 128 pacotes e pequena para keyframes de jogo
# (medido: 178-196 pacotes com qmin=16). Sem isso o access unit e truncado e o
# decoder nunca ve SPS/PPS -> 0 frames decodificados.
from aiortc.jitterbuffer import JitterBuffer

_orig_init = JitterBuffer.__init__


def init(self, capacity=64, prefetch=3, is_video=False):
    _orig_init(self, 512 if is_video else capacity, prefetch, is_video)


JitterBuffer.__init__ = init


def is_magenta(p):
    return p[0] > 200 and p[1] < 60 and p[2] > 200


got = {"frames": 0, "bytes": 0, "intervals": [], "drawn": None, "detect": None,
       "start": None, "first": None, "first_pts": None, "last": None,
       "keyframes": 0, "bitrate_seen": [], "gaps": []}


def pixel(frame):
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
        prev_pts = None

        async def pump():
            prev = None
            while True:
                try:
                    f = await track.recv()
                except Exception as e:
                    print("track terminou:", type(e).__name__, repr(e), flush=True)
                    break
                n = got["frames"]
                got["frames"] += 1
                now = time.time()
                if prev is not None:
                    iv = (now - prev) * 1000
                    got["intervals"].append(iv)
                    if iv > 60:
                        dpts = None if prev_pts is None else (f.pts - prev_pts)
                        got["gaps"].append((n, round(iv), dpts))
                prev = now
                prev_pts = f.pts
                if got["frames"] == 1:
                    got["first"] = now
                    got["first_pts"] = f.pts
                got["last"] = now
                if got["drawn"] and got["detect"] is None and is_magenta(pixel(f)):
                    got["detect"] = now
                    print("marcador detectado no frame", n, flush=True)
        asyncio.create_task(pump())

    @pc.on("connectionstatechange")
    def on_state():
        print("estado:", pc.connectionState, flush=True)

    await send({"t": "hello", "decoder": "webrtc"})
    await asyncio.wait_for(ws.recv(), timeout=5)

    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    await send({"t": "offer", "sdp": pc.localDescription.sdp})

    t0 = time.time()
    while time.time() - t0 < 25:
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=6))
        except (asyncio.TimeoutError, json.JSONDecodeError):
            continue
        if m.get("t") == "answer":
            await pc.setRemoteDescription(RTCSessionDescription(m["sdp"], "answer"))
        elif m.get("t") == "start":
            got["start"] = m["serverStart"]
        elif m.get("t") == "probe-at":
            got["drawn"] = m["drawn"]

    # probe
    await send({"t": "probe"})
    t1 = time.time()
    while time.time() - t1 < 8 and got["detect"] is None:
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            if m.get("t") == "probe-at":
                got["drawn"] = m["drawn"]
        except (asyncio.TimeoutError, json.JSONDecodeError):
            pass
        await asyncio.sleep(0.05)

    iv = got["intervals"]
    print("frames:", got["frames"])
    if iv:
        print("fps:", round(1000 / statistics.mean(iv), 1))
        print("intervalo medio:", round(statistics.mean(iv), 1), "ms",
              "| mediana:", round(statistics.median(iv), 1),
              "| p95:", round(sorted(iv)[int(len(iv) * 0.95)], 1),
              "| max:", round(max(iv), 1))
        print("frames fora de 40ms (bursts):", sum(1 for x in iv if x > 40))
        print("bursts (frame, intervalo_ms, delta_pts):", got["gaps"][:12])
    if got["detect"] and got["drawn"]:
        print("LATENCIA PROBE:", round((got["detect"] - got["drawn"]) * 1000), "ms")
    await pc.close()
    await ws.close()


asyncio.run(main())

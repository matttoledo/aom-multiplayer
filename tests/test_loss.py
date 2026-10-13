import asyncio
import json
import statistics
import sys
import time

import av
import aiortc.codecs
import aiortc.rtcrtpsender
import aiortc.rtcrtpreceiver
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.jitterbuffer import JitterBuffer
from aiortc.codecs.h264 import H264Decoder

WS = "ws://127.0.0.1:8081"
DUR = int(sys.argv[1]) if len(sys.argv) > 1 else 20

# imitar o navegador forcando H264
_orig_caps = aiortc.codecs.get_capabilities


def caps(kind):
    c = _orig_caps(kind)
    c.codecs = ([x for x in c.codecs if x.mimeType.lower() == "video/h264"] +
                [x for x in c.codecs if x.mimeType.lower() != "video/h264"])
    return c


aiortc.codecs.get_capabilities = caps
aiortc.rtcrtptransceiver.get_capabilities = caps
aiortc.rtcrtpsender.get_capabilities = caps
_video = aiortc.codecs.CODECS["video"]
aiortc.codecs.CODECS["video"] = ([c for c in _video if c.mimeType.lower() == "video/h264"] +
                                  [c for c in _video if c.mimeType.lower() != "video/h264"])

# capacidade do jitter buffer: 128 pacotes e pequena para keyframes de jogo
_orig_init = JitterBuffer.__init__


def init(self, capacity=64, prefetch=3, is_video=False):
    _orig_init(self, 512 if is_video else capacity, prefetch, is_video)


JitterBuffer.__init__ = init

c = {"pkts": 0, "rtx": 0, "nack": 0, "nack_seqs": 0, "pli": 0, "frames": 0,
     "decode_fail": 0, "partial": 0, "bytes": 0, "intervals": []}

_orig_decode = H264Decoder.decode


def decode(self, ef):
    if ef.data[:4] != b"\x00\x00\x00\x01":
        c["partial"] += 1
    try:
        fr = _orig_decode(self, ef)
        if fr:
            c["frames"] += 1
        else:
            c["decode_fail"] += 1
        return fr
    except Exception:
        c["decode_fail"] += 1
        return []


H264Decoder.decode = decode


def patch_receiver(r):
    codecs = getattr(r, "_RTCRtpReceiver__codecs", {})
    # NAO calcular rtx_pts aqui: __codecs so e preenchido em receive(), que acontece
    # depois do patch. Calculado dentro do handler, senao conta 0 rtx sempre.
    def is_rtx_pt(pt):
        c = codecs.get(pt)
        return c is not None and c.mimeType.lower() == "video/rtx"

    orig_packet = r._handle_rtp_packet

    async def handle(packet, arrival_time_ms):
        c["pkts"] += 1
        c["bytes"] += len(packet.payload)
        if is_rtx_pt(packet.payload_type):
            c["rtx"] += 1
        return await orig_packet(packet, arrival_time_ms)

    r._handle_rtp_packet = handle

    orig_nack = r._send_rtcp_nack

    async def nack(ssrc, seqs):
        c["nack"] += 1
        c["nack_seqs"] += len(seqs)
        return await orig_nack(ssrc, seqs)

    r._send_rtcp_nack = nack

    orig_pli = r._send_rtcp_pli

    async def pli(ssrc):
        c["pli"] += 1
        return await orig_pli(ssrc)

    r._send_rtcp_pli = pli


async def main():
    pc = RTCPeerConnection()
    tr = pc.addTransceiver("video", direction="recvonly")

    ws = await websockets.connect(WS)

    async def send(obj):
        await ws.send(json.dumps(obj))

    @pc.on("track")
    def on_track(track):
        patch_receiver(tr.receiver)
        prev = None

        async def pump():
            nonlocal prev
            while True:
                try:
                    f = await track.recv()
                except Exception:
                    break
                now = time.time()
                if prev is not None:
                    c["intervals"].append((now - prev) * 1000)
                prev = now
        asyncio.create_task(pump())

    await send({"t": "hello", "decoder": "webrtc"})
    await asyncio.wait_for(ws.recv(), timeout=5)

    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    print("OFFER (linhas de midia):")
    for ln in offer.sdp.splitlines():
        if ln.startswith("m=video") or "rtpmap" in ln or "fmtp" in ln:
            print("  ", ln)
    await send({"t": "offer", "sdp": pc.localDescription.sdp})

    t0 = time.time()
    while time.time() - t0 < DUR:
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=6))
        except (asyncio.TimeoutError, json.JSONDecodeError):
            continue
        if m.get("t") == "answer":
            print("ANSWER (linhas de midia):")
            for ln in m["sdp"].splitlines():
                if ln.startswith("m=video") or "rtpmap" in ln or "fmtp" in ln:
                    print("  ", ln)
            await pc.setRemoteDescription(RTCSessionDescription(m["sdp"], "answer"))

    iv = c["intervals"]
    print(f"=== perda: {DUR}s ===")
    print("pacotes RTP:", c["pkts"], "| bytes:", c["bytes"],
          "| rtx recebidos:", c["rtx"])
    print("NACKs enviados:", c["nack"], "(seqs:", c["nack_seqs"], ") | PLI:", c["pli"])
    print("access units parciais (sem start code):", c["partial"])
    print("frames decodificados:", c["frames"], "| falhas de decode:", c["decode_fail"])
    if iv:
        s = sorted(iv)
        print("fps:", round(1000 / statistics.mean(iv), 1),
              "| mediana:", round(statistics.median(iv), 1),
              "| p95:", round(s[int(len(s) * 0.95)], 1),
              "| p99:", round(s[min(len(s) - 1, int(len(s) * 0.99))], 1),
              "| max:", round(max(iv), 1))
    await pc.close()
    await ws.close()


asyncio.run(main())

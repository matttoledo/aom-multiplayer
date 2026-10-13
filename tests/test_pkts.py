import asyncio
import json
import logging
import time

import aiortc.codecs
import aiortc.rtcrtpreceiver
from aiortc.codecs import depayload as _orig_depayload
from aiortc.codecs.h264 import H264Decoder

logging.basicConfig(level=logging.INFO)

dump = []


def depayload(codec, payload):
    out = _orig_depayload(codec, payload)
    if len(dump) < 2:
        dump.append((codec.mimeType, payload[:12], out[:40]))
    return out


aiortc.codecs.depayload = depayload
aiortc.rtcrtpreceiver.depayload = depayload


_orig_decode = H264Decoder.decode


def decode(self, encoded_frame):
    if len(dump) < 6:
        dump.append(("decoder", len(bytes(encoded_frame.data)), bytes(encoded_frame.data)[:40], None))
    return _orig_decode(self, encoded_frame)


H264Decoder.decode = decode

from aiortc.jitterbuffer import JitterBuffer

_orig_add = JitterBuffer.add
seen = []


def add(self, packet):
    if len(seen) < 40:
        seen.append((packet.sequence_number, packet.timestamp, len(packet.payload)))
    return _orig_add(self, packet)


JitterBuffer.add = add

import websockets
from aiortc import RTCPeerConnection

import aiortc.codecs

_video = aiortc.codecs.CODECS["video"]
aiortc.codecs.CODECS["video"] = ([c for c in _video if c.mimeType.lower() == "video/h264"] +
                                  [c for c in _video if c.mimeType.lower() != "video/h264"])

WS = "ws://127.0.0.1:8081"
cnt = {"pkts": 0, "frames": 0, "codec": None, "pt": None, "rtp_bytes": 0,
       "ssrc": None, "mid": None}


async def main():
    pc = RTCPeerConnection()
    t = pc.addTransceiver("video", direction="recvonly")
    print("codecs do offer:", [c.mimeType for c in t._codecs][:4])

    ws = await websockets.connect(WS)

    @pc.on("track")
    def on_track(track):
        r = None
        # achar o receiver correspondente para contar pacotes
        for tr in pc.getTransceivers():
            if tr.receiver is not None:
                r = tr.receiver
        orig = r._handle_rtp_packet

        async def wrapped(packet, arrival_time_ms=None):
            cnt["pkts"] += 1
            cnt["pt"] = packet.payload_type
            cnt["ssrc"] = packet.ssrc
            cnt["mid"] = packet.extensions.mid
            return await orig(packet, arrival_time_ms)

        r._handle_rtp_packet = wrapped

        tr = r.transport
        orig_data = tr._handle_rtp_data

        async def wrapped_data(data, arrival_time_ms=None):
            cnt["rtp_bytes"] += len(data)
            return await orig_data(data, arrival_time_ms)

        tr._handle_rtp_data = wrapped_data

        async def pump():
            while cnt["frames"] < 200:
                try:
                    f = await track.recv()
                except Exception as e:
                    print("track:", type(e).__name__, repr(e), "packets:", cnt["pkts"])
                    break
                cnt["frames"] += 1
        asyncio.create_task(pump())

    await ws.send(json.dumps({"t": "hello", "decoder": "webrtc"}))
    await asyncio.wait_for(ws.recv(), timeout=5)

    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    await ws.send(json.dumps({"t": "offer", "sdp": pc.localDescription.sdp}))

    t0 = time.time()
    while time.time() - t0 < 12:
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=4))
        except (asyncio.TimeoutError, json.JSONDecodeError):
            continue
        if m.get("t") == "answer":
            await pc.setRemoteDescription(
                __import__("aiortc").RTCSessionDescription(m["sdp"], "answer"))
            break

    await asyncio.sleep(8)
    print("pacotes RTP recebidos:", cnt["pkts"], "| frames decodificados:", cnt["frames"],
          "| payload type:", cnt["pt"], "| ssrc:", cnt["ssrc"], "| mid:", cnt["mid"],
          "| bytes RTP no DTLS:", cnt["rtp_bytes"])
    for item in dump:
        if item[0] == "decoder":
            print("  decoder recebe", item[1], "bytes:", item[2].hex())
        else:
            print("  entrada nal", item[1][0] & 0x1F, item[1].hex())
            print("  saida       ", item[2].hex())
    print("pacotes no jitter buffer (seq, timestamp, payload):")
    for s in seen:
        print("  ", s)
    print("timestamps distintos nos primeiros pacotes:", len(set(s[1] for s in seen)))
    await pc.close()
    await ws.close()


asyncio.run(main())

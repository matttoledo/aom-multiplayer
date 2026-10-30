"""Mede o fps que um peer WebRTC realmente decodifica (N peers ao mesmo tempo).
Uso: fps_probe.py <peers> <segundos>. Tokens do .env, nunca impressos."""
import asyncio, base64, hashlib, hmac, json, statistics, sys, time
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.rtcconfiguration import RTCConfiguration

FIRST = int(__import__("os").environ.get("FIRST", "1"))
ENV = dict(l.strip().split("=", 1) for l in open("/home/matt/aomstream/.env") if "=" in l)

def token_for(n):
    t = ENV.get(f"AOM_TOKEN_{n}") or (ENV.get("AOM_SESSION_TOKEN") if n == 1 else None)
    if t:
        return t
    exp = int(time.time()) + 600
    mac = hmac.new(ENV["AOM_INVITE_KEY"].encode(), f"inv.{n}.{exp}".encode(), hashlib.sha256).digest()
    return f"inv.{n}.{exp}." + base64.urlsafe_b64encode(mac).decode().rstrip("=")

async def peer(n, secs):
    tok = token_for(n)
    ws = await websockets.connect(f"ws://127.0.0.1:8081/?t={tok}", max_size=None)
    await ws.send(json.dumps({"t": "hello", "token": tok, "decoder": "webrtc"}))
    m = json.loads(await ws.recv())
    if m.get("t") != "hello-ok":
        return n, f"hello negado: {m.get('msg')}"
    pc = RTCPeerConnection(RTCConfiguration())
    from aiortc import RTCRtpReceiver
    vt = pc.addTransceiver("video", direction="recvonly"); pc.addTransceiver("audio", direction="recvonly")
    # igual ao index.html: o modo compartilhado manda H264 ja codificado para todos
    caps = RTCRtpReceiver.getCapabilities("video").codecs
    h264 = [c for c in caps if c.mimeType.lower() == "video/h264"]
    vt.setCodecPreferences(h264 + [c for c in caps if c.mimeType.lower() == "video/rtx"])
    pc.createDataChannel("input")
    got = asyncio.get_event_loop().create_future()
    pc.on("track", lambda t: t.kind == "video" and not got.done() and got.set_result(t))
    await pc.setLocalDescription(await pc.createOffer())
    await ws.send(json.dumps({"t": "offer", "sdp": pc.localDescription.sdp}))
    while True:
        d = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
        if d.get("t") == "answer":
            break
    await pc.setRemoteDescription(RTCSessionDescription(d["sdp"], "answer"))
    try:
        track = await asyncio.wait_for(got, 20)
    except asyncio.TimeoutError:
        return n, "sem trilha de video em 20 s"
    try:
        await asyncio.wait_for(track.recv(), 10)      # aquece ate o 1o keyframe
    except asyncio.TimeoutError:
        st = await pc.getStats()
        inb = [f"{v.kind}: {v.packetsReceived} pkts lost={v.packetsLost}" for v in st.values() if v.type == "inbound-rtp"]
        codecs = [c.mimeType for t in pc.getTransceivers() if t.receiver for c in (t._codecs or [])][:4]
        return n, f"0 frames decodificados em 10 s (ice={pc.iceConnectionState}) {inb} codecs={codecs}"

    ts, t0 = [], time.time()
    while time.time() - t0 < secs:
        try:
            f = await asyncio.wait_for(track.recv(), 3)
        except asyncio.TimeoutError:
            return n, f"parou depois de {len(ts)} frames"
        ts.append(time.time())
    gaps = [(b - a) * 1000 for a, b in zip(ts, ts[1:])]
    rf = f.reformat(format="rgb24")   # ultimo frame recebido -> PPM (sem numpy)
    pl, w, h = rf.planes[0], rf.width, rf.height
    raw = bytes(pl)
    with open(f"/tmp/peer{n}.ppm", "wb") as o:
        o.write(f"P6 {w} {h} 255\n".encode())
        for y in range(h):
            o.write(raw[y * pl.line_size:y * pl.line_size + w * 3])
    big = [(round(ts[i] - ts[0], 2), round(g)) for i, g in enumerate(gaps) if g > 80]
    print(f"peer {n} travadas >80ms (t, ms): {big}", flush=True)
    await pc.close(); await ws.close()
    return n, (f"{len(ts)/secs:.1f} fps decodificados, {f.width}x{f.height}, intervalo mediana "
               f"{statistics.median(gaps):.0f} ms, p95 {sorted(gaps)[int(len(gaps)*.95)]:.0f} ms, max {max(gaps):.0f} ms")

async def main():
    peers, secs = int(sys.argv[1]), float(sys.argv[2])
    for n, r in await asyncio.gather(*[peer(i, secs) for i in range(FIRST, FIRST + peers)]):
        print(f"peer {n}: {r}")

asyncio.run(main())

import asyncio, json, time, websockets
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.rtcconfiguration import RTCConfiguration, RTCIceServer
from aioice.ice import CandidatePair

ENV = "/home/matt/aomstream/.env"
tok = ""
for line in open(ENV):
    if line.startswith("AOM_SESSION_TOKEN="):
        tok = line.split("=", 1)[1].strip()

BASE = "wss://game.matheustoledo.dev"
ORIGIN = "https://game.matheustoledo.dev"


def to_servers(arr):
    return [RTCIceServer(urls=s["urls"], username=s.get("username"), credential=s.get("credential"))
            for s in (arr or [])]


async def main():
    t0 = time.time()
    ws = await websockets.connect(BASE + "/?t=" + tok, origin=ORIGIN, max_size=None)
    print("WS upgrade + TLS: %.2f s" % (time.time() - t0))

    await ws.send(json.dumps({"t": "hello", "decoder": "webrtc"}))
    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
    if m.get("t") != "hello-ok":
        print("hello negado:", m)
        return
    print("hello-ok via tunel | iceServers:", [s["urls"] for s in (m.get("ice") or [])])

    pc = RTCPeerConnection(RTCConfiguration(iceServers=to_servers(m.get("ice"))))
    pc.addTransceiver("video", direction="recvonly")

    @pc.on("connectionstatechange")
    def on_state():
        print("estado:", pc.connectionState, flush=True)

    await pc.setLocalDescription(await pc.createOffer())
    offer = pc.localDescription
    cand = [l for l in offer.sdp.split("\n") if l.startswith("a=candidate:")]
    print("candidatos do cliente:", len(cand), "| srflx:", len([l for l in cand if "typ srflx" in l]))
    await ws.send(json.dumps({"t": "offer", "sdp": offer.sdp}))

    answer = None
    while True:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=25)
        except asyncio.TimeoutError:
            break
        if isinstance(raw, bytes):
            continue
        d = json.loads(raw)
        if d.get("t") == "answer":
            answer = d["sdp"]
        elif d.get("t") == "ice":
            await pc.addIceCandidate(RTCSessionDescription(sdp=d["sdp"], type="remote-candidate"))
        elif d.get("t") == "pong":
            print("pong (keepalive pelo tunel) ok")
        elif d.get("t") == "error":
            print("erro:", d.get("msg"))
            return
        if answer:
            break
    if not answer:
        print("sem answer em 25 s")
        return
    await pc.setRemoteDescription(RTCSessionDescription(sdp=answer, type="answer"))

    for _ in range(25):
        await asyncio.sleep(1)
        if pc.connectionState in ("connected", "completed"):
            break
    print("estado final:", pc.connectionState)

    for c in pc._RTCPeerConnection__iceTransports:
        conn = getattr(c, "_connection", None)
        for p in getattr(conn, "_check_list", []):
            if p.state == CandidatePair.State.SUCCEEDED:
                print("par succeeded:", p.local_candidate.type, p.local_addr, "->",
                      p.remote_candidate.type, p.remote_addr, "nominated=", p.nominated)

    frames = 0
    t1 = time.time()
    while time.time() - t1 < 10:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=3)
            if isinstance(raw, bytes) and raw:
                frames += 1
        except asyncio.TimeoutError:
            pass
    print("frames binarios recebidos em 10 s:", frames)
    await pc.close()
    await ws.close()


asyncio.run(main())

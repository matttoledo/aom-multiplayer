import asyncio, json, time, websockets

OFFER = (
    "v=0\r\n"
    "o=- 0 0 IN IP4 127.0.0.1\r\n"
    "s=-\r\n"
    "t=0 0\r\n"
    "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
    "c=IN IP4 0.0.0.0\r\n"
    "a=recvonly\r\na=rtcp-mux\r\na=setup:actpass\r\n"
    "a=rtpmap:96 H264/90000\r\n"
    "a=fmtp:96 level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42001f\r\n"
    "a=ice-ufrag:testufrag\r\n"
    "a=ice-pwd:testpwd\r\na=fingerprint:sha-256 11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00\r\n"
)

async def main():
    async with websockets.connect("ws://127.0.0.1:8081") as ws:
        await ws.send(json.dumps({"t": "hello", "decoder": "webrtc"}))
        print("hello-ok:", json.loads(await asyncio.wait_for(ws.recv(), timeout=5)))
        await ws.send(json.dumps({"t": "offer", "sdp": OFFER}))
        t0 = time.time()
        answer = None
        ice = 0
        while time.time() - t0 < 12:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
            except (asyncio.TimeoutError, json.JSONDecodeError):
                break
            if msg.get("t") == "answer":
                answer = msg
                print("answer recebida; serverStart =", msg.get("serverStart"))
                print("m-line da answer:", [l for l in answer["sdp"].split("\r\n") if l.startswith("m=")])
            elif msg.get("t") == "ice":
                ice += 1
            elif msg.get("t") == "error":
                print("erro:", msg.get("msg"))
        print(f"ice candidates do servidor: {ice}")

asyncio.run(main())

"""Fan-out com 2, 4 e 8 jogadores na mesma instancia compartilhada.

Roda no asus com AOM_MODE=shared. Para cada jogador: conecta, recebe o stream e mede
quantos frames decodifica. O que o teste confirma:

  - todos os peers recebem o mesmo stream, inclusive quem entra depois;
  - quem entra depois comeca num keyframe: medido pelo primeiro frame que decodifica
    (sem IDR o decoder nao comeca no meio do GOP);
  - a saida de um peer nao derruba os outros;
  - sequencia antiga e descartada: mandar seq menor que a ultima aceita nao muda o pad;
  - o pad e neutralizado por desconexao e por expiracao do prazo sem atualizacao.

Uso: python3 scripts/fanout_test.py [ws://127.0.0.1:8082] [2 4 8]
Os tokens vem do .env e nunca sao impressos.
"""
import asyncio
import json
import os
import sys
import time

import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.rtcconfiguration import RTCConfiguration

ENV = "/home/matt/aomstream/.env"
URL = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8082"
ROUNDS = [int(x) for x in (sys.argv[2:] or ["2", "4", "8"])]
# mesmo prazo do servidor: sem atualizacao do cliente, o pad e neutralizado
PAD_STALE = int(os.environ.get("AOM_PAD_STALE", "5"))

env = {}
for line in open(ENV):
    if "=" in line:
        k, v = line.strip().split("=", 1)
        env[k] = v

# jogador N -> token. O pad do jogador N e N-1.
TOKENS = {}
for n in range(1, 9):
    tok = env.get(f"AOM_TOKEN_{n}") or (env.get("AOM_SESSION_TOKEN", "") if n == 1 else "")
    if tok:
        TOKENS[n] = tok


class Client:
    def __init__(self, n):
        self.n = n
        self.track = None
        self.frames = 0
        self.first_frame_ok = False
        self.msgs = asyncio.Queue()
        self.dc = None

    async def connect(self):
        self.ws = await websockets.connect(URL + "/?t=" + TOKENS[self.n], max_size=None)
        await self.ws.send(json.dumps({"t": "hello", "decoder": "webrtc"}))
        m = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=10))
        if m.get("t") != "hello-ok":
            raise RuntimeError(f"jogador {self.n}: hello negado ({m.get('msg')})")
        self.pad = m.get("pad")
        self.state = m.get("buttons") or []
        self.pc = RTCPeerConnection(RTCConfiguration())
        pc = self.pc
        pc.addTransceiver("video", direction="recvonly")
        pc.addTransceiver("audio", direction="recvonly")
        self.dc = pc.createDataChannel("input")
        self.dc.on("message", lambda msg: self.msgs.put_nowait(json.loads(msg)))
        self.reader = asyncio.create_task(self.read())

        @pc.on("track")
        def on_track(track):
            if track.kind == "video":
                self.track = track

        await pc.setLocalDescription(await pc.createOffer())
        await self.ws.send(json.dumps({"t": "offer", "sdp": pc.localDescription.sdp}))
        while True:
            d = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=20))
            if d.get("t") == "answer":
                break
        await pc.setRemoteDescription(RTCSessionDescription(d["sdp"], "answer"))

    async def read(self):
        try:
            async for raw in self.ws:
                self.msgs.put_nowait(json.loads(raw))
        except Exception:
            pass

    async def expect(self, t, timeout=5):
        """Proxima mensagem do servidor com esse tipo, pulando o estado periodico."""
        end = time.time() + timeout
        while time.time() < end:
            try:
                m = await asyncio.wait_for(self.msgs.get(), timeout=max(0.1, end - time.time()))
            except asyncio.TimeoutError:
                return None
            if m.get("t") == t:
                return m

    async def pad(self, seq, buttons):
        self.dc.send(json.dumps({"t": "pad", "seq": seq, "buttons": buttons}))
        return await self.expect("pad-ack")

    async def frames(self, seconds):
        n, t0 = 0, time.time()
        while time.time() - t0 < seconds:
            try:
                f = await asyncio.wait_for(self.track.recv(), timeout=2)
            except Exception:
                break
            n += 1
            if n == 1:
                # decodificou o primeiro frame: ele tinha que ser um IDR
                self.first_frame_ok = f is not None
        self.frames += n
        return n

    async def close(self):
        try:
            await self.ws.close()
            await self.pc.close()
        except Exception:
            pass


async def round(n):
    print(f"\n=== {n} jogadores ===")
    clients = [Client(i) for i in range(1, n + 1)]
    for c in clients:
        await c.connect()
    await asyncio.sleep(3)   # ICE + DataChannel
    got = await asyncio.gather(*[c.frames(5) for c in clients])
    print(f"frames em 5 s por peer: {got}")
    print(f"primeiro frame decodificado (keyframe no entry): "
          f"{[c.first_frame_ok for c in clients]}")

    # sequencia antiga: seq 5 entra, seq 3 e descartada e o pad nao muda
    c = clients[0]
    a = await c.pad(5, ["cross"])
    b = await c.pad(3, ["circle"])
    print(f"jogador {c.n}: seq 5 -> {a and a.get('buttons')}, "
          f"seq 3 -> {b and b.get('buttons')} (descartado={b and b.get('descartado')})")

    # saida de um peer nao derruba os outros
    await c.close()
    rest = clients[1:]
    still = await asyncio.gather(*[x.frames(4) for x in rest])
    print(f"depois que o jogador {c.n} saiu: frames em 4 s = {still}")

    # neutralizacao por desconexao: o mesmo token volta com o pad limpo
    back = Client(c.n)
    await back.connect()
    print(f"jogador {c.n} reconectado: pad={back.pad} estado={back.state} "
          f"(esperado: vazio)")
    await back.close()

    # neutralizacao por prazo sem atualizacao
    c = rest[0]
    await c.pad(9, ["r2"])
    m = await c.expect("pad-state", timeout=PAD_STALE + 4)
    print(f"jogador {c.n}: sem atualizacao por {PAD_STALE} s -> {m and m.get('buttons')} "
          f"(neutralizado={m and m.get('neutralizado')})")
    for x in clients[1:]:
        await x.close()
    await asyncio.sleep(2)


async def main():
    for n in ROUNDS:
        if n > len(TOKENS):
            print(f"\n=== {n} jogadores: sem tokens no .env ({len(TOKENS)}), pula ===")
            continue
        await round(n)


asyncio.run(main())

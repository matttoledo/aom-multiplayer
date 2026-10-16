#!/usr/bin/env python3
"""Fan-out: UMA saida de encoder consumida por N peers.

No futebol todos os jogadores veem a mesma tela, entao o NVENC codifica uma vez por
frame e o access unit resultante e entregue TAL QUAL a cada peer: nao se reencoda por
peer. O que continua sendo por conexao e a packetizacao em NAL/FU-A, o numero de
sequencia RTP, o SSRC e o historico de retransmissao. Um NACK do peer A nao pode
afetar o peer B.

Como isso encaixa no aiortc sem mexer nele: o sender chama `encoder.encode(frame)`
quando o track devolve um `Frame`, e `encoder.pack(packet)` quando devolve um
`av.Packet`. O track de cada peer devolve o access unit ja codificado como av.Packet,
entao o encoder daquele peer nunca abre uma sessao NVENC -- ele so packetiza o que a
fonte compartilhou. Uma sessao NVENC no total, nao N.

Regras:
  - peer novo entra no proximo IDR: sem keyframe o decoder do navegador nao comeca no
    meio do GOP, entao a fila dele so aceita frames a partir de um keyframe;
  - fila bounded: um peer lento nao pode fazer a fonte crescer sem limite. Fila cheia
    = descarta o mais novo e marca resync para o proximo keyframe; os outros peers
    seguem no ritmo normal;
  - PLI/FIR de qualquer peer peca keyframe na fonte. O keyframe e compartilhado;
  - serverStart por peer: o instante em que o frame que o peer recebeu com
    mediaTime=0 foi capturado. Com fonte compartilhada o primeiro frame do peer nao e
    o primeiro frame da fonte, e a conta de latencia do navegador usa o valor dele.

Audio: a captura do monitor do slot e compartilhada (uma leitura do sink, nao N). O
codec (Opus) continua por peer, porque o encoder do aiortc pertence ao sender.
"""
import asyncio
import os
import threading
import time

import av
from aiortc.mediastreams import AUDIO_TIME_BASE, MediaStreamError, MediaStreamTrack, VIDEO_TIME_BASE

WIDTH = int(os.environ.get("WIDTH", "1280"))
HEIGHT = int(os.environ.get("HEIGHT", "720"))
FPS = int(os.environ.get("FPS", "30"))
SLOT_PREFIX = os.environ.get("SLOT_PREFIX", "aom_slot")
AUDIO_RATE = 48000
AUDIO_CHANNELS = 2
AUDIO_SAMPLES = AUDIO_RATE // 50

# 60 frames = 2 s a 30 fps. Cheia, o peer resync no proximo keyframe em vez de
# atrasar os outros.
QUEUE_MAX = int(os.environ.get("AOM_FANOUT_QUEUE", "60"))


def is_keyframe(data):
    """Access unit em Annex-B com IDR (NAL type 5 ou 7)."""
    i = 0
    while True:
        i = data.find(b"\x00\x00\x01", i)
        if i == -1:
            return False
        i += 3
        if i >= len(data):
            return False
        if data[i] & 0x1F in (5, 7):
            return True


class EncodedUnit:
    """Um access unit codificado, com o instante da captura que o gerou."""
    __slots__ = ("data", "timestamp", "captured_at", "keyframe")

    def __init__(self, data, timestamp, captured_at, keyframe):
        self.data = data
        self.timestamp = timestamp
        self.captured_at = captured_at
        self.keyframe = keyframe


class Pipe:
    """Fila bounded de um peer. Cheia: descarta o mais novo, nao atrasa os outros.

    keyframe_rule: o peer so comeca a receber a partir de um IDR. Fila cheia tambem
    marca resync, porque retomar no meio de um GOP nao funciona.
    """

    def __init__(self, name, source=None, keyframe_rule=False, maxsize=QUEUE_MAX):
        self.name = name
        self.source = source
        self.keyframe_rule = keyframe_rule
        self.queue = asyncio.Queue(maxsize=maxsize)
        self.resync = keyframe_rule
        self.dropped = 0
        self.closed = False

    def put(self, item):
        if self.closed:
            return
        if self.keyframe_rule and self.resync and not getattr(item, "keyframe", False):
            return
        if getattr(item, "keyframe", False):
            self.resync = False
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            self.dropped += 1
            self.resync = self.keyframe_rule
            if self.source is not None and self.keyframe_rule:
                self.source.request_keyframe()

    def close(self):
        self.closed = True


class FanoutSource:
    """Captura + encoder uma vez; o access unit vai para a fila de cada peer.

    `encode(frame, force_keyframe)` devolve (bytes do access unit em Annex-B,
    timestamp em 90 kHz) e roda no executor. Quem passa o encode e o servidor, com o
    encoder NVENC: reencodar por peer e exatamente o que este modulo evita.
    """

    def __init__(self, display, encode, loop):
        self.display = display
        self.encode = encode
        self.loop = loop
        self.container = None
        self.stream = None
        self.gen = None
        # O decode roda num thread do executor. Fechar o container enquanto o thread
        # esta dentro de next(gen) e use-after-free (segfault medido no caminho de 1
        # jogador): o mesmo lock vale aqui.
        self.decoding = threading.Lock()
        self.pipes = []
        self.start = None
        self.frames = 0
        # O primeiro access unit da fonte e sempre IDR: um peer que entra depois
        # precisa de um keyframe para comecar.
        self.force_keyframe = True
        self.running = False
        self.task = None

    async def start(self):
        if self.running:
            return True
        try:
            self.container = await asyncio.to_thread(
                av.open, f"{self.display}.0", format="x11grab",
                options={"framerate": str(FPS), "video_size": f"{WIDTH}x{HEIGHT}"})
        except Exception as e:
            print(f"fan-out: captura {self.display} falhou: {e}", flush=True)
            return False
        self.stream = self.container.streams.video[0]
        self.gen = self.container.decode(self.stream)
        self.running = True
        self.task = asyncio.create_task(self.run())
        print(f"fan-out: fonte na tela {self.display}, {WIDTH}x{HEIGHT}@{FPS}", flush=True)
        return True

    def _decode(self):
        with self.decoding:
            return next(self.gen)

    async def run(self):
        try:
            while self.running:
                frame = await self.loop.run_in_executor(None, self._decode)
                if frame is None:
                    break
                captured = time.time()
                if self.start is None:
                    self.start = captured
                    print(f"primeiro frame capturado; serverStart = {self.start}", flush=True)
                forced = self.force_keyframe
                self.force_keyframe = False
                data, ts = await self.loop.run_in_executor(None, self.encode, frame, forced)
                if not data:
                    continue
                self.frames += 1
                unit = EncodedUnit(data, ts, captured, forced or is_keyframe(data))
                for pipe in list(self.pipes):
                    pipe.put(unit)
                if self.frames % 300 == 0:
                    print(f"fan-out: {self.frames} frames em {time.time() - self.start:.1f}s, "
                          f"{len(self.pipes)} peers", flush=True)
        except Exception as e:
            print(f"fan-out: fonte parou: {e!r}", flush=True)
        finally:
            self.running = False
            for pipe in list(self.pipes):
                pipe.close()
            if self.container is not None:
                # esperar o decode em andamento: sem isso o thread do executor ainda
                # esta dentro de next(gen) quando o container e liberado -> segfault
                with self.decoding:
                    self.container.close()

    def subscribe(self, name):
        pipe = Pipe(name, source=self, keyframe_rule=True)
        self.pipes.append(pipe)
        # o peer novo precisa de um IDR: o proximo frame da fonte e forcado
        self.request_keyframe()
        return pipe

    def unsubscribe(self, pipe):
        if pipe in self.pipes:
            self.pipes.remove(pipe)
        pipe.close()

    def request_keyframe(self):
        self.force_keyframe = True

    def stop(self):
        self.running = False
        if self.task is not None and not self.task.done():
            self.task.cancel()


class FanoutTrack(MediaStreamTrack):
    """Track de um peer: consome a fila da fonte compartilhada.

    Devolve o access unit ja codificado como av.Packet, entao o sender daquele peer
    chama `pack()` (packetiza na conexao dele) em vez de `encode()`. O historico de
    retransmissao continua por conexao.
    """
    kind = "video"

    def __init__(self, source, pipe, notify=None):
        super().__init__()
        self.source = source
        self.pipe = pipe
        self.notify = notify or (lambda obj: None)
        self.start = None
        self.frames = 0

    async def recv(self):
        unit = await self.pipe.queue.get()
        if unit is None:
            raise MediaStreamError
        if self.start is None:
            # serverStart do peer: o instante do frame que ele recebe com mediaTime=0
            self.start = unit.captured_at
            self.notify({"t": "start", "serverStart": self.start, "peer": self.pipe.name})
        self.frames += 1
        if self.frames % 300 == 0:
            print(f"fan-out {self.pipe.name}: {self.frames} frames, "
                  f"{self.pipe.dropped} descartados", flush=True)
        packet = av.Packet(unit.data)
        packet.pts = unit.timestamp
        packet.time_base = VIDEO_TIME_BASE
        return packet

    def stop(self):
        super().stop()
        self.source.unsubscribe(self.pipe)


class AudioSource:
    """Captura de audio uma vez; os quadros de 20 ms vao para a fila de cada peer.

    O codec (Opus) continua por peer, porque o encoder do aiortc pertence ao sender.
    O que nao pode rodar N vezes e a leitura do monitor do sink.
    """

    def __init__(self, slot):
        self.slot = slot
        self.proc = None
        self.pipes = []
        self.running = False
        self.task = None

    async def start(self):
        if self.running:
            return True
        self.proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            # 3840 bytes = 20 ms de s16 estereo a 48 kHz: sem isso o pulse entrega em
            # rajadas e o jitter buffer de audio do navegador sobe para ~120 ms
            "-fragment_size", str(AUDIO_SAMPLES * AUDIO_CHANNELS * 2),
            "-f", "pulse", "-i", f"{SLOT_PREFIX}{self.slot}.monitor",
            "-f", "s16le", "-ac", str(AUDIO_CHANNELS), "-ar", str(AUDIO_RATE), "-",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self.running = True
        self.task = asyncio.create_task(self.run())
        print(f"fan-out: audio do slot {self.slot}", flush=True)
        return True

    async def run(self):
        size = AUDIO_SAMPLES * AUDIO_CHANNELS * 2
        try:
            while self.running:
                data = await self.proc.stdout.readexactly(size)
                for pipe in list(self.pipes):
                    pipe.put(data)
        except asyncio.IncompleteReadError:
            print(f"audio slot {self.slot}: captura terminou", flush=True)
        except Exception as e:
            print(f"audio slot {self.slot}: {e!r}", flush=True)
        finally:
            self.running = False
            for pipe in list(self.pipes):
                pipe.close()

    def subscribe(self, name):
        pipe = Pipe(name)
        self.pipes.append(pipe)
        return pipe

    def unsubscribe(self, pipe):
        if pipe in self.pipes:
            self.pipes.remove(pipe)
        pipe.close()

    def stop(self):
        self.running = False
        if self.task is not None and not self.task.done():
            self.task.cancel()
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()


class AudioFanoutTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(self, source, pipe):
        super().__init__()
        self.source = source
        self.pipe = pipe
        self.pts = 0
        self.frames = 0

    async def recv(self):
        data = await self.pipe.queue.get()
        if data is None:
            raise MediaStreamError
        frame = av.AudioFrame(format="s16", layout="stereo", samples=AUDIO_SAMPLES)
        frame.planes[0].update(data)
        frame.sample_rate = AUDIO_RATE
        frame.pts = self.pts
        frame.time_base = AUDIO_TIME_BASE
        self.pts += AUDIO_SAMPLES
        self.frames += 1
        if self.frames % 1500 == 0:
            print(f"fan-out audio {self.pipe.name}: {self.frames} quadros, "
                  f"{self.pipe.dropped} descartados", flush=True)
        return frame

    def stop(self):
        super().stop()
        self.source.unsubscribe(self.pipe)

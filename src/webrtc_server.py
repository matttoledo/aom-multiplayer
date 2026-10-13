#!/usr/bin/env python3
"""Servidor WebRTC (aiortc) para medir o transporte contra o caminho MSE.

Por que aiortc e nao webrtcbin: o GStreamer 1.24 do Ubuntu 24.04 nao tem o plugin
`nice` (elementos nicesrc/nicesink), e webrtcbin precisa deles para ICE --
"libnice elements are not available". O pacote traz a biblioteca
libgstwebrtcnice-1.0.so mas nao o plugin. aiortc tem ICE proprio, em Python.

Mesmo display (:2), mesmo GPU (NVENC), mesmos 30 fps, mesmo bitrate. Muda SO o
transporte: WebSocket+MSE -> RTP sobre UDP.

Signaling pelo mesmo WebSocket:
  cliente -> {"t":"offer","sdp":...}
  servidor -> {"t":"answer","sdp":...} e {"t":"start","serverStart":<epoch s>}
  ambos     -> {"t":"ice","candidate":...}
  cliente   -> {"t":"ping","ts":...} -> {"t":"pong",...}

Latencia medida no navegador com requestVideoFrameCallback:
  render_time - (serverStart + mediaTime*1000 + clock_offset)
serverStart e o instante exato em que o PRIMEIRO frame foi capturado; mediaTime
do navegador comeca em 0 nesse frame e avanca 1/FPS por frame, porque o timestamp
RTP sai do pts do frame capturado.

Input: XTest no display :2.
"""
import asyncio
import fractions
import json
import logging
import os
import time

import av
import aiortc.codecs
import aiortc.rtcrtpsender
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.codecs.h264 import H264Encoder
from aiortc.mediastreams import MediaStreamTrack
from aiortc.rtcicetransport import RTCIceCandidate
from aiortc.sdp import candidate_from_sdp

import websockets
from websockets.datastructures import Headers
from websockets.http11 import Response

from Xlib import X
from Xlib.display import Display
from Xlib.ext import xtest
from Xlib.XK import string_to_keysym

from marker import Marker

# aiortc engole excecao do encoder com __log_warning; sem handler nada aparece no log
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

DISPLAY_NAME = os.environ.get("DISPLAY", ":2")
WIDTH = int(os.environ.get("WIDTH", "1280"))
HEIGHT = int(os.environ.get("HEIGHT", "720"))
FPS = int(os.environ.get("FPS", "30"))
# O alvo de bitrate NAO define a qualidade: medido, com alvo de 2 Mbps o encoder
# entregou 8.4 Mbps porque nao podia subir o QP acima de qmax. O que define a
# qualidade percebida (blockiness) e qmax; o bitrate real e consequencia do
# conteudo. Alvo 14 Mbps com qmax 34 medido no jogo real: PSNR 36.6 dB,
# max access unit 115 pacotes.
BITRATE = int(os.environ.get("BITRATE", "17000000"))
MAX_BITRATE = int(os.environ.get("MAX_BITRATE", "20000000"))
PRESET = os.environ.get("PRESET", "p4")
PORT = int(os.environ.get("PORT", "8081"))
WINDOW_NAME = os.environ.get("WINDOW_NAME", "Age of Mythology").lower()

# ATENCAO: no FFmpeg 7 (PyAV 17) as AVOptions do h264_nvenc NAO incluem
# "g", "max-bitrate", "vbv-bufsize", "qmin", "qmax". Passa-las em codec.options
# e silencioso: avcodec_open2 devolve-as no dict sem aplicar (medido: cqp=51
# nao mudava nada). As que realmente aplicam: preset, tune, profile, level, rc,
# coder, delay, max_slice_size, spatial-aq, temporal-aq, no-scenecut, forced-idr.
# gop/qmin/qmax vao como CAMPOS do contexto (PyAV expoe gop_size, qmin, qmax).
NVENC_OPTIONS = {
    "preset": PRESET,
    "tune": "ll",
    # main permite CABAC: compressa melhor que baseline na mesma cena, e o navegador
    # decode main sem custo extra de latencia
    "profile": "main",
    "delay": "0",
    "rc": os.environ.get("RC", "vbr"),
    "coder": "cabac",
    # AQ: redistribui bits para as areas planas, onde o blockiness e mais visivel.
    # Medido: +0.3 dB de PSNR em PSNR, mas perceptualmente ajuda mais que o numero.
    "spatial-aq": "1",
    "temporal-aq": "1",
    # sem scenecut: keyframes no GOP fixo, sem picos de tamanho por mudanca de cena
    "no-scenecut": "1",
}

# gop e qmin/qmax como campos (AVOptions nao funcionam para eles nesse build).
# qmax e o teto de QP = teto de qualidade. Varredura medida no jogo real, alvo
# 6 Mbps: qmax 38 -> 34.2 dB / 9.5 Mbps; 36 -> 35.5 / 11.3; 34 -> 36.6 / 14.0;
# 32 -> 37.6 / 17.1; 30 -> 38.8 / 20.9. Baixar fps NAO melhora qualidade (mesmo
# PSNR a 24/20/15 fps), so gasta menos bits. Escolhido qmax 32: +3.4 dB sobre o
# teto antigo de 38, e o access unit maximo continua <= 120 pacotes.
# GOP 15: resync 0.5 s em vez de 1 s, com o mesmo PSNR medido e AU maximo menor.
GOP_SIZE = int(os.environ.get("GOP_SIZE", "15"))
QMIN = int(os.environ.get("QMIN", "18"))
QMAX = int(os.environ.get("QMAX", "32"))


class NvencH264Encoder(H264Encoder):
    """Encoder de H264 no GPU, igual ao caminho MSE. Sem B-frame, sem delay de reorder."""

    def __init__(self):
        super().__init__()
        # aiortc clampa target_bitrate em MAX_BITRATE=3 Mbps, que e exatamente o que
        # deixa a tela pixelizada. O override abaixo remove o clamp de 3M.
        self._bitrate = BITRATE

    @property
    def target_bitrate(self):
        return self._bitrate

    @target_bitrate.setter
    def target_bitrate(self, bitrate):
        self._bitrate = max(500000, min(int(bitrate), MAX_BITRATE))

    def _encode_frame(self, frame, force_keyframe):
        if self.codec is None:
            # PyAV 17: CodecContext.create() NAO aceita options=; o dicionario e
            # atribuido antes do primeiro encode (open acontece no primeiro encode)
            self.codec = av.CodecContext.create("h264_nvenc", "w")
            self.codec.options = dict(NVENC_OPTIONS)
            self.codec.width = frame.width
            self.codec.height = frame.height
            self.codec.bit_rate = self.target_bitrate
            self.codec.pix_fmt = "yuv420p"
            self.codec.framerate = fractions.Fraction(FPS, 1)
            self.codec.time_base = fractions.Fraction(1, FPS)
            # campos do AVCodecContext, NAO AVOptions (ver comentario acima)
            self.codec.gop_size = GOP_SIZE
            self.codec.qmin = QMIN
            self.codec.qmax = QMAX
            self.bytes_total = 0
            self.frames_total = 0
            print("encoder ativo:", self.codec.name, "bitrate:", self.codec.bit_rate,
                  "preset:", PRESET, "gop:", self.codec.gop_size,
                  "qmin:", self.codec.qmin, "qmax:", self.codec.qmax,
                  "opcoes:", dict(NVENC_OPTIONS), "frame pix_fmt:", frame.format.name,
                  flush=True)

        if force_keyframe:
            frame.pict_type = av.video.frame.PictureType.I
        else:
            frame.pict_type = av.video.frame.PictureType.NONE

        data = b""
        for packet in self.codec.encode(frame):
            data += bytes(packet)
        if data:
            self.bytes_total += len(data)
            self.frames_total += 1
            if self.frames_total % 150 == 0:
                avg = self.bytes_total / self.frames_total
                print(f"encode: {self.frames_total} frames, "
                      f"{avg:.0f} B/frame, {avg * FPS * 8 / 1000:.0f} kbps", flush=True)
            yield from self._split_bitstream(data)


def _get_encoder(codec):
    if codec.mimeType.lower() == "video/h264":
        enc = NvencH264Encoder()
        enc.target_bitrate = BITRATE
        return enc
    return _orig_get_encoder(codec)


_orig_get_encoder = aiortc.codecs.get_encoder
aiortc.rtcrtpsender.get_encoder = _get_encoder

# RTP_HISTORY_SIZE = 128 no aiortc. Um keyframe de jogo ocupa 100-180 pacotes,
# entao um NACK que chega atrasado ja nao encontra o pacote no historico.
# 1024 pacotes ~ 1.3 MB por conexao: barato e cobre varios access units.
aiortc.rtcrtpsender.RTP_HISTORY_SIZE = int(os.environ.get("RTP_HISTORY", "1024"))

# diagnostico de retransmissao
_retransmit_orig = aiortc.rtcrtpsender.RTCRtpSender._retransmit
rx = {"nack": 0, "hit": 0}


async def _retransmit(self, seq):
    rx["nack"] += 1
    hist = getattr(self, "_RTCRtpSender__rtp_history", {})
    if hist.get(seq % aiortc.rtcrtpsender.RTP_HISTORY_SIZE) is not None:
        rx["hit"] += 1
    if rx["nack"] % 20 == 0:
        print("retransmit:", rx["nack"], "chamadas,", rx["hit"], "acharam o pacote", flush=True)
    return await _retransmit_orig(self, seq)


aiortc.rtcrtpsender.RTCRtpSender._retransmit = _retransmit


class CaptureTrack(MediaStreamTrack):
    kind = "video"

    def __init__(self, container, stream, loop, notify):
        super().__init__()
        self.container = container
        self.stream = stream
        self.loop = loop
        self.gen = container.decode(stream)
        self.notify = notify
        self.start = None
        self.frames = 0

    async def recv(self):
        try:
            frame = await self.loop.run_in_executor(None, next, self.gen)
        except StopIteration:
            return None
        if self.start is None:
            self.start = time.time()
            # o navegador usa isso como origem de mediaTime: mediaTime=0 e este frame
            self.notify({"t": "start", "serverStart": self.start})
            print(f"primeiro frame capturado; serverStart = {self.start}", flush=True)
        self.frames += 1
        if self.frames % 300 == 0:
            print(f"webrtc: {self.frames} frames em {time.time() - self.start:.1f}s", flush=True)
        return frame


def find_window(disp):
    root = disp.screen().root

    def by_name(w):
        try:
            n = (w.get_wm_name() or "").lower()
        except Exception:
            n = ""
        if WINDOW_NAME in n:
            return w
        for c in w.query_tree().children:
            r = by_name(c)
            if r:
                return r
        return None

    w = by_name(root)
    if w and w.id != root.id:
        return w

    def by_size(w):
        try:
            g = w.get_geometry()
        except Exception:
            return None
        if w.id != root.id and g.width == WIDTH and g.height == HEIGHT:
            return w
        for c in w.query_tree().children:
            r = by_size(c)
            if r:
                return r
        return None

    return by_size(root)


def apply_input(disp, ev):
    t = ev.get("t")
    if t == "key":
        sym = string_to_keysym(ev.get("key", ""))
        if not sym:
            return
        code = disp.keysym_to_keycode(sym)
        if not code:
            return
        xtest.fake_input(disp, X.KeyPress if ev.get("down") else X.KeyRelease, code)
    elif t in ("move", "mouse"):
        x = int(ev.get("x", 0))
        y = int(ev.get("y", 0))
        if t == "move":
            xtest.fake_input(disp, X.MotionNotify, x=x, y=y)
        else:
            button = int(ev.get("button", 1))
            down = bool(ev.get("down", True))
            xtest.fake_input(disp, X.ButtonPress if down else X.ButtonRelease, button, x=x, y=y)
    disp.sync()


async def hide_later(marker, seconds=4.0):
    await asyncio.sleep(seconds)
    marker.hide()


async def handler(ws):
    loop = asyncio.get_running_loop()
    disp = Display(DISPLAY_NAME)
    marker = Marker(disp)

    try:
        raw = await ws.recv()
        ev = json.loads(raw)
    except (websockets.exceptions.ConnectionClosed, json.JSONDecodeError):
        disp.close()
        return

    if ev.get("decoder") != "webrtc":
        await ws.send(json.dumps({"t": "error", "msg": "use ?mode=webrtc"}))
        disp.close()
        return

    container = av.open(f"{DISPLAY_NAME}.0", format="x11grab",
                        options={"framerate": str(FPS), "video_size": f"{WIDTH}x{HEIGHT}"})
    stream = container.streams.video[0]

    def send_json(obj):
        try:
            asyncio.create_task(ws.send(json.dumps(obj)))
        except Exception:
            pass

    pc = RTCPeerConnection()
    track = CaptureTrack(container, stream, loop, send_json)
    pc.addTrack(track)

    @pc.on("icecandidate")
    def on_ice(candidate):
        if candidate and candidate.candidate:
            send_json({"t": "ice", "candidate": {
                "candidate": candidate.candidate,
                "sdpMLineIndex": 0,
            }})

    @pc.on("connectionstatechange")
    def on_state():
        print("estado:", pc.connectionState, flush=True)

    await ws.send(json.dumps({"t": "hello-ok", "mode": "webrtc"}))

    try:
        async for raw in ws:
            try:
                ev = json.loads(raw)
            except Exception:
                continue
            t = ev.get("t")
            if t == "offer":
                await pc.setRemoteDescription(
                    RTCSessionDescription(ev["sdp"], "offer"))
                answer = await pc.createAnswer()
                # aiortc nao faz trickle ICE: o gathering acontece em setLocalDescription
                # e os candidatos entram no SDP. Enviar localDescription, nao o createAnswer.
                await pc.setLocalDescription(answer)
                # diagnostico de retransmissao: aiortc so retransmite se o answer
                # contiver um video/rtx cujo apt = payloadType do codec negociado.
                # __rtx_payload_type e definido dentro de receive(), que roda depois de
                # setLocalDescription -> imprimir agora mostra sempre None. Imprime 8 s
                # depois, quando o sender ja esta rodando.
                async def rtx_diag():
                    await asyncio.sleep(8)
                    for tr in pc.getTransceivers():
                        s = tr.sender
                        pt = getattr(s, "_RTCRtpSender__rtx_payload_type", None)
                        codecs = getattr(s, "_RTCRtpSender__codecs", [])
                        print("negociado:", [f"{c.mimeType}/{c.payloadType}" for c in codecs],
                              "| rtx payload type:", pt, flush=True)

                asyncio.create_task(rtx_diag())
                await ws.send(json.dumps({
                    "t": "answer",
                    "sdp": pc.localDescription.sdp,
                    "serverStart": track.start,
                }))
            elif t == "ice":
                cand = ev.get("candidate", {})
                line = cand.get("candidate", "")
                if not line:
                    await pc.addIceCandidate(None)
                    continue
                try:
                    c = candidate_from_sdp(line)
                    c.sdpMLineIndex = cand.get("sdpMLineIndex", 0)
                    c.sdpMid = cand.get("sdpMid")
                    await pc.addIceCandidate(c)
                except Exception as e:
                    print("ice:", e, flush=True)
            elif t == "ping":
                await ws.send(json.dumps({
                    "t": "pong",
                    "client_ts": ev.get("ts"),
                    "server_ts": time.time(),
                }))
            elif t == "probe":
                drawn = marker.draw()
                await ws.send(json.dumps({"t": "probe-at", "drawn": drawn}))
                asyncio.create_task(hide_later(marker))
            else:
                apply_input(disp, ev)
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        await pc.close()
        container.close()
        marker.close()
        disp.close()
        print("cliente desconectado", flush=True)


HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")


async def process_request(connection, request):
    path = request.path.split("?")[0]
    if path == "/" and request.headers.get("Upgrade", "").lower() != "websocket":
        with open(HTML_PATH, "rb") as f:
            body = f.read()
        return Response(200, "OK", Headers({"Content-Type": "text/html; charset=utf-8"}), body)
    return None


async def main():
    print(f"webrtc (aiortc): porta {PORT}, display {DISPLAY_NAME}, {WIDTH}x{HEIGHT}@{FPS}", flush=True)
    async with websockets.serve(handler, "0.0.0.0", PORT, process_request=process_request):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""Loop de streaming: captura o display do jogo, codifica com NVENC, manda para o
navegador por WebSocket, e recebe input de volta via XTest.

O navegador declara o decoder no hello e o servidor escolhe o pipeline:
  "webcodecs" -> Annex-B, um NAL/access unit por mensagem (latencia ~1 ms)
  "mse"       -> fMP4 fragmentado, caixas moof+mdat por mensagem (funciona em
                 contexto nao seguro)

Protocolo:
  cliente -> servidor: {"t":"hello","decoder":"webcodecs"|"mse"}
  servidor -> cliente: mensagem binaria = [1 byte kind][8 bytes double: timestamp do
    servidor em segundos][payload]
    kind 0 = init segment (ftyp+moov), kind 1 = frame/fragmento
  cliente -> servidor: JSON
    {"t":"ping","ts":<performance.now()>}          -> {"t":"pong",...}
    {"t":"key","down":bool,"key":"a"}
    {"t":"mouse","button":1,"x":..,"y":..,"down":true}
    {"t":"move","x":..,"y":..}
"""
import asyncio
import json
import os
import struct
import time

import websockets
from websockets.datastructures import Headers
from websockets.http11 import Response

from marker import Marker
from Xlib import X
from Xlib.display import Display
from Xlib.ext import xtest
from Xlib.XK import string_to_keysym

DISPLAY_NAME = os.environ.get("DISPLAY", ":2")
WIDTH = int(os.environ.get("WIDTH", "1280"))
HEIGHT = int(os.environ.get("HEIGHT", "720"))
FPS = int(os.environ.get("FPS", "30"))
BITRATE = os.environ.get("BITRATE", "17M")
MAX_BITRATE = os.environ.get("MAX_BITRATE", "20M")
VBV_BUFFER = os.environ.get("VBV_BUFFER", "28000000")
PRESET = os.environ.get("PRESET", "p4")
PORT = int(os.environ.get("PORT", "8080"))
WINDOW_NAME = os.environ.get("WINDOW_NAME", "Age of Mythology").lower()

START_CODE = b"\x00\x00\x01"
# NAL types 1..5 sao slices; 6=SEI, 7=SPS, 8=PPS, 9=AUD (delimiter, NAO e slice).
SLICE_TYPES = {1, 2, 3, 4, 5}

BASE_CMD = [
    "ffmpeg",
    "-hide_banner",
    "-loglevel", "error",
    "-f", "x11grab",
    "-video_size", f"{WIDTH}x{HEIGHT}",
    "-framerate", str(FPS),
    "-i", f"{DISPLAY_NAME}.0",
    "-c:v", "h264_nvenc",
    "-preset", PRESET,
    "-tune", "ll",
    # main permite CABAC: compressa melhor que baseline na mesma cena
    "-profile:v", "main",
    "-delay", "0",
    "-rc", "vbr",
    # GOP 15: resync 0.5 s em vez de 1 s sob perda, com o mesmo PSNR medido
    "-g", "15",
    "-b:v", BITRATE,
    "-maxrate", MAX_BITRATE,
    # "-vbv-bufsize" NAO existe como opcao do CLI para h264_nvenc no FFmpeg 7:
    # ffmpeg sai com "Unrecognized option 'vbv-bufsize'". O equivalente e
    # "-bufsize" (campo rc_buffer_size do AVCodecContext).
    "-bufsize", VBV_BUFFER,
    # aqui as opcoes REALMENTE aplicam: o ffmpeg CLI define os campos do
    # AVCodecContext. No PyAV (caminho WebRTC) "g"/"maxrate"/"vbv-bufsize"/
    # "qmin"/"qmax" NAO sao AVOptions do h264_nvenc no FFmpeg 7 e eram ignorados.
    # qmax e o teto de QP = teto de qualidade. Medido no jogo real: qmax 38 ->
    # 34.2 dB, qmax 34 -> 36.6 dB, qmax 32 -> 37.6 dB, qmax 30 -> 38.8 dB. O alvo
    # de bitrate sozinho nao melhora nada: com alvo de 2 Mbps o encoder entregou
    # 8.4 Mbps porque nao podia subir o QP acima de 38.
    "-qmin", "18",
    "-qmax", "32",
    "-spatial-aq", "1",
    "-temporal-aq", "1",
    "-flush_packets", "1",
]


def find_window(disp):
    """Janela do jogo: primeiro por nome, senao a janela (na raiz) que ocupa a tela."""
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


def header(kind, ts):
    return bytes([kind]) + struct.pack("<d", ts)


async def read_box(stdout):
    """Le uma caixa MP4 (8 bytes de cabecalho, ou 16 com largesize)."""
    hdr = await stdout.readexactly(8)
    size = int.from_bytes(hdr[:4], "big")
    typ = hdr[4:8]
    if size == 1:
        larg = await stdout.readexactly(8)
        size = int.from_bytes(larg, "big")
        return typ, hdr + larg + await stdout.readexactly(size - 16)
    if size < 8:
        return typ, b""
    return typ, hdr + await stdout.readexactly(size - 8)


async def encoder_annexb(client, disp):
    proc = await asyncio.create_subprocess_exec(
        *(BASE_CMD + ["-f", "h264", "-"]),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    buf, prefix, is_key, sent, t0 = b"", [], False, 0, time.time()

    try:
        while True:
            chunk = await proc.stdout.read(65536)
            if not chunk:
                break
            buf += chunk
            while True:
                i = buf.find(START_CODE)
                if i == -1:
                    buf = b""
                    break
                j = buf.find(START_CODE, i + 3)
                if j == -1:
                    buf = buf[i:]
                    break
                nal = buf[i + 3:j]
                buf = buf[j:]
                nal_type = nal[0] & 0x1F
                if nal_type in SLICE_TYPES:
                    if nal_type == 5:
                        is_key = True
                    au = b"".join(prefix) + START_CODE + nal
                    prefix = []
                    await client.send(header(1 if is_key else 0, time.time()) + au)
                    sent += 1
                    is_key = False
                else:
                    prefix.append(START_CODE + nal)
            if sent and sent % 300 == 0:
                print(f"annexb: {sent} access units em {time.time() - t0:.1f}s", flush=True)
    finally:
        if proc.returncode is None:
            proc.terminate()


async def encoder_mp4(client, disp):
    proc = await asyncio.create_subprocess_exec(
        *(BASE_CMD + ["-movflags", "frag_every_frame+empty_moov+default_base_moof", "-f", "mp4", "-"]),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    sent = 0
    t0 = time.time()

    try:
        # init segment: ftyp + moov
        init = b""
        for _ in range(2):
            typ, box = await read_box(proc.stdout)
            init += box
        await client.send(header(0, time.time()) + init)

        while True:
            typ, moof = await read_box(proc.stdout)
            if typ != b"moof":
                continue
            typ2, mdat = await read_box(proc.stdout)
            if typ2 != b"mdat":
                continue
            await client.send(header(1, time.time()) + moof + mdat)
            sent += 1
            if sent % 300 == 0:
                print(f"mp4: {sent} fragmentos em {time.time() - t0:.1f}s", flush=True)
    except (asyncio.IncompleteReadError, Exception) as e:
        print(f"mp4 encoder parou: {e}", flush=True)
    finally:
        if proc.returncode is None:
            proc.terminate()


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
    disp = Display(DISPLAY_NAME)
    window = find_window(disp)
    if window is None:
        await ws.send(json.dumps({"t": "error", "msg": "janela do jogo nao encontrada"}))
        disp.close()
        return

    marker = Marker(disp)

    # o navegador declara o decoder antes de qualquer video
    try:
        raw = await ws.recv()
        ev = json.loads(raw)
    except (websockets.exceptions.ConnectionClosed, json.JSONDecodeError):
        disp.close()
        return

    mode = ev.get("decoder", "webcodecs")
    print(f"cliente conectado; janela {window.id}; decoder={mode}", flush=True)

    if mode == "mse":
        await ws.send(json.dumps({"t": "hello-ok", "mode": "mse"}))
        task = asyncio.create_task(encoder_mp4(ws, disp))
    else:
        await ws.send(json.dumps({"t": "hello-ok", "mode": "webcodecs"}))
        task = asyncio.create_task(encoder_annexb(ws, disp))

    try:
        async for raw in ws:
            try:
                ev = json.loads(raw)
            except Exception:
                continue
            if ev.get("t") == "ping":
                await ws.send(json.dumps({"t": "pong", "client_ts": ev.get("ts"), "server_ts": time.time()}))
            elif ev.get("t") == "probe":
                drawn = marker.draw()
                await ws.send(json.dumps({"t": "probe-at", "drawn": drawn}))
                asyncio.create_task(hide_later(marker))
            else:
                apply_input(disp, ev)
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        task.cancel()
        marker.close()
        disp.close()
        print("cliente desconectado", flush=True)


HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")


async def process_request(connection, request):
    """websockets >= 12: process_request recebe (connection, request).
    Serve a pagina para GET /, mas NAO para o upgrade de WebSocket (que tambem vem com
    path "/"). respond() nao aceita headers, entao construo o Response a mao para
    entregar Content-Type: text/html."""
    path = request.path.split("?")[0]
    if path == "/" and request.headers.get("Upgrade", "").lower() != "websocket":
        with open(HTML_PATH, "rb") as f:
            body = f.read()
        return Response(
            200,
            "OK",
            Headers({"Content-Type": "text/html; charset=utf-8"}),
            body,
        )
    return None


async def main():
    async with websockets.serve(handler, "0.0.0.0", PORT, process_request=process_request):
        print(f"servidor: http://{PORT}", flush=True)
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())

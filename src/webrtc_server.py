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
import base64
import subprocess
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
import uuid

import av
import aiortc.codecs
import aiortc.rtcrtpsender
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.rtcconfiguration import RTCConfiguration, RTCIceServer
from aiortc.codecs.h264 import H264Encoder
from aiortc.mediastreams import MediaStreamError, MediaStreamTrack
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

# Audio do slot do jogador (mesmo esquema do server.py): o jogo toca no sink
# aom_slotN e o monitor dele e capturado. Aqui vai em Opus dentro do WebRTC, nao em
# AAC/fMP4: o RTP leva o audio no mesmo transporte P2P do video.
SLOT = os.environ.get("SLOT", "1")
SLOT_PREFIX = os.environ.get("SLOT_PREFIX", "aom_slot")
AUDIO = os.environ.get("AUDIO", "1") == "1"
AUDIO_RATE = 48000
AUDIO_CHANNELS = 2
# 20 ms por quadro: o encoder Opus do aiortc espera exatamente 960 amostras a 48 kHz
AUDIO_SAMPLES = AUDIO_RATE // 50

# Arquitetura decidida: pagina + sinalizacao pelo tunel Cloudflare (Access -> NPM ->
# este servidor); VIDEO P2P direto por STUN entre o navegador do amigo e o asus. A
# midia nao passa pelo tunel nem pelo Pi.
#
# Medido no asus com UM unico socket UDP, mesma porta local para os dois destinos:
#   stun.cloudflare.com:3478 -> 179.164.111.8:<porta local>
#   stun.l.google.com:19302  -> 179.164.111.8:<porta local>
# IP publico, sem CGNAT, mapeamento independente do destino: o melhor caso para P2P.
# Detalhe do parser: esses servidores devolvem XOR-MAPPED-ADDRESS no codigo legado
# 0x0020 (RFC 3489), nao no 0x0014 (RFC 5389).
#
# Forcar RELAY era erro: o ICE testa todos os pares em paralelo, e um candidato de
# LAN que nao alcanca nao atrasa a conexao. Politicas: ALL nos dois lados.
STUN_URL = os.environ.get("STUN_URL", "stun:stun.cloudflare.com:3478")

# TURN como reserva DESLIGADA: so entra se as duas variaveis existirem no ambiente.
# Sem elas, nenhuma chamada a API e nenhum erro. Com elas, o TURN vai JUNTO com o
# STUN, nunca sozinho.
CF_TURN_KEY_ID = os.environ.get("CF_TURN_KEY_ID", "")
CF_TURN_API_TOKEN = os.environ.get("CF_TURN_API_TOKEN", "")
CF_TURN_TTL = int(os.environ.get("CF_TURN_TTL", "86400"))

# Segredo nunca no chat, no log nem no commit: AOM_SESSION_TOKEN e as variaveis do
# TURN ficam num .env chmod 600 lido pelo processo.
ENV_FILE = os.environ.get("AOM_ENV_FILE",
                          os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


def load_env(path):
    """KEY=VAL de um .env simples, sem dependencia externa. Nunca loga o valor."""
    try:
        st = os.stat(path)
    except OSError:
        return
    if st.st_mode & 0o077:
        print("AVISO: .env legivel por outros:", oct(st.st_mode & 0o777), flush=True)
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


load_env(ENV_FILE)
AOM_SESSION_TOKEN = os.environ.get("AOM_SESSION_TOKEN", "")
# Convites: o jogador 1 (dono) gera um link para outra vaga que vence em 48 h. O
# token e "inv.<jogador>.<vence epoch>.<hmac>", assinado com AOM_INVITE_KEY (no
# .env): nada fica guardado no servidor. Trocar a chave revoga todos os convites.
INVITE_KEY = os.environ.get("AOM_INVITE_KEY", "")
INVITE_TTL = int(os.environ.get("AOM_INVITE_TTL", str(48 * 3600)))
OWNER = 1
MAX_PLAYERS = 8   # teto do NVENC: 8 sessoes simultaneas
# Teto de jogadores para convite. Medido (2026-10-14): cada instancia ~1.2 nucleo e
# 0.8-1.5 GB de RAM; o asus tem 8 nucleos e ~7 GB livres com dois jogos.
INVITE_MAX = min(MAX_PLAYERS, int(os.environ.get("AOM_MAX_PLAYERS", "5")))
# Sobe rede, tela e jogo do jogador N (root, via sudo sem senha restrito a este
# arquivo; scripts/spawn.sh). Sem a regra do sudoers o servidor so convida para
# instancias que ja estao no ar.
SPAWN = os.environ.get("AOM_SPAWN", "/usr/local/lib/aom/spawn.sh")
INVITE_HOLD = 600   # s que um convite segura a vaga ate o amigo entrar


def load_players():
    """Um processo so serve todos os jogadores: o token escolhe a tela e o slot.

    Jogador N: token AOM_TOKEN_N (segredo, no .env), tela AOM_DISPLAY_N (padrao
    :N+1, porque :1 e o dummy e :2 o primeiro Xorg com GPU), slot de audio N.
    AOM_SESSION_TOKEN continua valendo como jogador 1, na tela de DISPLAY/SLOT.
    """
    players = {}
    for n in range(1, MAX_PLAYERS + 1):
        tok = os.environ.get(f"AOM_TOKEN_{n}", "")
        if n == 1 and not tok:
            tok = AOM_SESSION_TOKEN
        # jogador sem token fixo continua existindo: entra por convite (inv.*)
        if not tok and not (INVITE_KEY and n > 1):
            continue
        default_display = DISPLAY_NAME if n == 1 else f":{n + 1}"
        default_slot = SLOT if n == 1 else str(n)
        players[n] = {
            "token": tok,
            "display": os.environ.get(f"AOM_DISPLAY_{n}", default_display),
            "slot": os.environ.get(f"AOM_SLOT_{n}", default_slot),
        }
    return players


PLAYERS = load_players()


def invite_sig(n, exp):
    mac = hmac.new(INVITE_KEY.encode(), f"inv.{n}.{exp}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def make_invite(n):
    exp = int(time.time()) + INVITE_TTL
    return f"inv.{n}.{exp}.{invite_sig(n, exp)}", exp


def check_invite(token):
    """(jogador, None) se o convite vale; (None, motivo) se nao."""
    if not INVITE_KEY:
        return None, "token de sessao invalido"
    parts = token.split(".")
    if len(parts) != 4 or not parts[1].isdigit() or not parts[2].isdigit():
        return None, "token de sessao invalido"
    n, exp = int(parts[1]), int(parts[2])
    if not hmac.compare_digest(parts[3].encode(), invite_sig(n, exp).encode()):
        return None, "token de sessao invalido"
    if n == OWNER or n not in PLAYERS:
        return None, "token de sessao invalido"
    if time.time() > exp:
        return None, "convite expirado: peca um novo link"
    return n, None


def resolve_token(token):
    """Jogador do token (fixo do .env ou convite). (n, None) ou (None, motivo)."""
    if token.startswith("inv."):
        return check_invite(token)
    n = player_for(token)
    return (n, None) if n is not None else (None, "token de sessao invalido")


def display_alive(name):
    try:
        Display(name).close()
        return True
    except Exception:
        return False


def can_spawn():
    try:
        return subprocess.run(["sudo", "-n", "-l", SPAWN], capture_output=True,
                              timeout=5).returncode == 0
    except Exception:
        return False


CAN_SPAWN = can_spawn()
SPAWNING = {}   # jogador -> task do spawn em andamento
RESERVED = {}   # jogador -> ate quando o convite segura a vaga (epoch)


def spawn_instance(n):
    """Dispara o spawn.sh do jogador N em segundo plano (uma vez por vez)."""
    if not CAN_SPAWN or (n in SPAWNING and not SPAWNING[n].done()):
        return

    async def run():
        t0 = time.time()
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-n", SPAWN, str(n),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await proc.communicate()
        tail = out.decode(errors="replace").strip().splitlines()[-1:] or [""]
        print(f"spawn jogador {n}: rc={proc.returncode} em {time.time() - t0:.0f}s "
              f"({tail[0]})", flush=True)

    print(f"spawn jogador {n}: subindo rede, tela {PLAYERS[n]['display']} e jogo", flush=True)
    SPAWNING[n] = asyncio.create_task(run())


def invite_slot():
    """Vaga para o convite: o primeiro jogador (alem do dono) sem sessao e sem convite
    pendente, ate INVITE_MAX. Sem permissao de spawn, so entre os que estao no ar."""
    now = time.time()
    for n in sorted(PLAYERS):
        if n == OWNER or n > INVITE_MAX or n in ACTIVE or RESERVED.get(n, 0) > now:
            continue
        if CAN_SPAWN or display_alive(PLAYERS[n]["display"]):
            return n
    return None


def player_for(token):
    """Numero do jogador dono do token, ou None. Compara contra TODOS os tokens, sem
    sair cedo, para o tempo nao revelar qual jogador quase bateu."""
    found = None
    for n, p in PLAYERS.items():
        if not p["token"]:
            continue
        if hmac.compare_digest(token.encode(), p["token"].encode()) and found is None:
            found = n
    return found


# jogador -> websocket ativo: uma sessao por jogador. Reconectar (F5, troca de rede)
# derruba a sessao antiga, senao duas abas disputariam a mesma tela e o mesmo input.
# Valor: {"ws": websocket da sessao, "end": Event que encerra a sessao}. O Event
# existe porque a sessao pode seguir sem WebSocket (input pelo DataChannel).
ACTIVE = {}

# A pagina chega atras do Cloudflare Access -> NPM -> este servidor. websockets nao
# valida Host (qualquer Host serve a pagina); o Origin so e checado quando a lista
# existe. None na lista aceita cliente sem Origin (curl, script): navegador sempre
# manda Origin, entao a protecao continua.
ALLOWED_ORIGINS = [
    None,
    "https://game.matheustoledo.dev",
    "http://192.168.15.17:8081",
    "http://localhost:8081",
    "http://127.0.0.1:8081",
]

# conexao -> {ip, token, t0}: o IP do jogador vem do tunel, nao do socket.
CONN = {}


def client_ip(request, connection):
    cf = request.headers.get("CF-Connecting-IP", "")
    if cf:
        return cf
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    peer = connection.remote_address
    return peer[0] if peer else "?"


def ice_servers(turn=None):
    """STUN sempre; TURN apenas quando configurado, e sempre junto com o STUN."""
    servers = [RTCIceServer(urls=[STUN_URL])]
    if turn:
        servers.extend(turn)
    return servers


async def turn_credentials():
    """Credenciais de TURN de curta duracao, uma por conexao.

    Reserva desligada: retorna [] quando as variaveis nao existem, sem chamar a API e
    sem erro. O STUN continua sendo o caminho; o TURN e apenas um candidato extra,
    para o caso de NAT simetrico no 4G do amigo.
    """
    if not (CF_TURN_KEY_ID and CF_TURN_API_TOKEN):
        return []

    def fetch():
        req = urllib.request.Request(
            f"https://rtc.live.cloudflare.com/v1/turn/keys/{CF_TURN_KEY_ID}/credentials/generate-ice-servers",
            data=json.dumps({"ttl": CF_TURN_TTL}).encode(),
            headers={
                "Authorization": f"Bearer {CF_TURN_API_TOKEN}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())

    try:
        data = await asyncio.to_thread(fetch)
    except Exception as e:
        print(f"TURN: credenciais falharam: {e}", flush=True)
        return []

    servers = []
    for s in data.get("iceServers") or []:
        urls = s.get("urls") or ([s["url"]] if s.get("url") else [])
        servers.append(RTCIceServer(urls=urls, username=s.get("username"), credential=s.get("credential")))
    return servers


def ice_json(servers):
    """Mesma coisa para o navegador: o cliente cria o RTCPeerConnection antes do
    answer, entao as credenciais vao no hello-ok.

    A API de credenciais da Cloudflare devolve uma URL com porta 53, que o navegador
    bloqueia; ela fica no servidor (aioice aceita) e sai do JSON do cliente.
    """
    out = []
    for s in servers:
        urls = [u for u in (s.urls or []) if ":53" not in u]
        if not urls:
            continue
        out.append({"urls": urls, "username": s.username, "credential": s.credential})
    return out
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
            # O x11grab entrega bgr0, que o NVENC aceita direto e converte na GPU. Com
            # "yuv420p" aqui o PyAV convertia cada quadro na CPU (swscale) antes do
            # encode. Medido no asus, 720p sintetico: encode 6.1 -> 2.6 ms (mediana),
            # 8.6 -> 4.6 ms (p95). Cor no Chrome: pixel a pixel igual nos dois caminhos.
            rgb_in = frame.format.name in ("bgr0", "rgb0", "bgra", "rgba")
            self.codec.pix_fmt = frame.format.name if rgb_in else "yuv420p"
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
        # O decode roda num thread do executor. Fechar o container enquanto o thread
        # esta dentro de next(gen) e use-after-free: segfault medido com faulthandler
        # (crash em concurrent/futures/thread.py:58, dentro do decode do PyAV).
        self.decoding = threading.Lock()

    def _decode(self):
        with self.decoding:
            return next(self.gen)

    async def recv(self):
        try:
            frame = await self.loop.run_in_executor(None, self._decode)
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


class AudioTrack(MediaStreamTrack):
    """PCM do monitor do slot -> quadros de 20 ms; o aiortc codifica em Opus.

    PyAV nao tem demuxer pulse, entao o ffmpeg captura e entrega s16le cru no stdout.
    O ritmo vem da propria captura (tempo real), por isso recv() nao dorme.
    """
    kind = "audio"

    def __init__(self, slot):
        super().__init__()
        self.slot = slot
        self.proc = None
        self.pts = 0
        self.frames = 0

    async def _start(self):
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

    async def recv(self):
        if self.proc is None:
            await self._start()
        size = AUDIO_SAMPLES * AUDIO_CHANNELS * 2
        try:
            data = await self.proc.stdout.readexactly(size)
        except asyncio.IncompleteReadError:
            print(f"audio slot {self.slot}: captura terminou", flush=True)
            self.stop()
            raise MediaStreamError
        frame = av.AudioFrame(format="s16", layout="stereo", samples=AUDIO_SAMPLES)
        frame.planes[0].update(data)
        frame.sample_rate = AUDIO_RATE
        frame.pts = self.pts
        frame.time_base = fractions.Fraction(1, AUDIO_RATE)
        self.pts += AUDIO_SAMPLES
        self.frames += 1
        if self.frames % 1500 == 0:
            print(f"audio slot {self.slot}: {self.frames} quadros", flush=True)
        return frame

    def stop(self):
        super().stop()
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()


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


# Atalhos de sistema que o input remoto nao pode injetar: eles atingem o desktop do
# asus, nao o jogo. O servidor acompanha os modificadores pressionados porque o
# cliente manda a tecla pelo nome (e.key) e a combinacao so pode ser decidida aqui.
MOD_KEYS = {"Alt": "alt", "AltGr": "alt", "Control": "ctrl", "Meta": "meta", "Shift": "shift"}
BLOCKED_KEYS = {"Meta", "Super_L", "Super_R"}
BLOCKED_COMBOS = {("alt", "Tab"), ("alt", "F4"), ("alt", "F2"),
                  ("ctrl+alt", "Delete"), ("ctrl+alt", "Escape")}


# O cliente manda e.key (nome do DOM); o XTest precisa do keysym do X. Os nomes
# batem para letras, digitos, F1-F12, Tab, Escape, Home, End, Insert e Delete, mas
# nao para estes: sem a traducao, string_to_keysym devolvia 0 e a tecla sumia
# (Backspace, Enter, espaco, setas, Shift/Ctrl/Alt e toda a pontuacao, 2026-10-14).
DOM_KEYSYMS = {
    "Backspace": "BackSpace", "Enter": "Return", " ": "space",
    "ArrowLeft": "Left", "ArrowRight": "Right", "ArrowUp": "Up", "ArrowDown": "Down",
    "Shift": "Shift_L", "Control": "Control_L", "Alt": "Alt_L",
    "AltGraph": "ISO_Level3_Shift", "Meta": "Super_L",
    "CapsLock": "Caps_Lock", "NumLock": "Num_Lock", "ScrollLock": "Scroll_Lock",
    "PageUp": "Prior", "PageDown": "Next", "ContextMenu": "Menu", "PrintScreen": "Print",
}


def dom_keysym(key):
    sym = string_to_keysym(DOM_KEYSYMS.get(key, key))
    # caractere unico (pontuacao, acentos): no X o keysym de Latin-1 e o proprio
    # codigo do caractere ("!" -> 0x21, "ç" -> 0xe7)
    if not sym and len(key) == 1 and (0x20 <= ord(key) <= 0x7e or 0xa0 <= ord(key) <= 0xff):
        sym = ord(key)
    return sym


def focus_game(disp, win):
    """Foco de teclado na janela do jogo. Sem gerenciador de janelas ninguem faz isso:
    com o foco em PointerRoot o X entrega a tecla a janela do jogo, mas o Wine nao a
    considera ativa e descarta o teclado (mouse segue funcionando). Aconteceu em
    2026-10-14; xdotool windowfocus resolveu na hora."""
    if win is None:
        return
    try:
        win.set_input_focus(X.RevertToParent, X.CurrentTime)
        disp.sync()
    except Exception as e:
        print("foco:", e, flush=True)


def blocked_key(key, pressed):
    if key in BLOCKED_KEYS:
        return True
    if key in ("Tab", "F4", "F2") and "alt" in pressed:
        return True
    if key in ("Delete", "Escape") and {"ctrl", "alt"} <= pressed:
        return True
    return False


def apply_input(disp, ev, pressed, stats, game=None):
    t = ev.get("t")
    if t == "key":
        key = ev.get("key", "")
        mod = MOD_KEYS.get(key)
        if mod:
            if ev.get("down"):
                pressed.add(mod)
            else:
                pressed.discard(mod)
        if blocked_key(key, pressed):
            stats["blocked"] += 1
            return
        sym = dom_keysym(key)
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
            if down and game is not None:
                # clique = o jogador quer o jogo em foco, como num desktop normal. A
                # janela pode nao existir ainda se a instancia acabou de subir.
                if game["win"] is None:
                    game["win"] = find_window(disp)
                focus_game(disp, game["win"])
            xtest.fake_input(disp, X.ButtonPress if down else X.ButtonRelease, button, x=x, y=y)
    disp.sync()


def log_ws_close(ws, n):
    """Quem fechou o WebSocket e por que. Sem isto uma queda no meio da partida nao
    deixava rastro: o handler engolia o ConnectionClosed (2026-10-14, 17:09:59).

    cliente  = o navegador mandou close (refresh, aba fechada, 4000 de outra aba)
    servidor = este lado fechou (1011 = o ping de 20 s ficou sem resposta)
    nenhum   = a conexao TCP sumiu sem close (1006): tunel, NPM ou rede no caminho
    """
    proto = getattr(ws, "protocol", None)
    rcvd = getattr(proto, "close_rcvd", None)
    sent = getattr(proto, "close_sent", None)
    if rcvd is not None and (sent is None or getattr(proto, "close_rcvd_then_sent", False)):
        quem = "cliente"
    elif sent is not None:
        quem = "servidor"
    else:
        quem = "nenhum"
    print(f"ws fechou: jogador={n} code={ws.close_code} motivo={ws.close_reason!r} "
          f"iniciado_por={quem} hora={time.strftime('%H:%M:%S')}", flush=True)


async def hide_later(marker, seconds=4.0):
    await asyncio.sleep(seconds)
    marker.hide()


async def handler(ws):
    info = CONN.get(ws) or {"ip": "?", "token": "", "t0": time.time()}
    loop = asyncio.get_running_loop()

    try:
        raw = await ws.recv()
        ev = json.loads(raw)
    except (websockets.exceptions.ConnectionClosed, json.JSONDecodeError):
        return

    # Segunda camada alem do Cloudflare Access: o hello so aceita um token de sessao
    # valido, vindo do URL (?t=...) ou do campo "token". Comparacao em tempo
    # constante; o valor do token nunca aparece no log.
    if not PLAYERS:
        print(f"hello negado: nenhum token de jogador configurado, ip={info['ip']}", flush=True)
        await ws.send(json.dumps({"t": "error", "msg": "servidor sem token de sessao"}))
        return
    n, motivo = resolve_token(ev.get("token") or info["token"] or "")
    if n is None:
        print(f"hello negado: {motivo}, ip={info['ip']}", flush=True)
        await ws.send(json.dumps({"t": "error", "msg": motivo}))
        return
    player = PLAYERS[n]
    display_name = player["display"]

    if ev.get("decoder") != "webrtc":
        await ws.send(json.dumps({"t": "error", "msg": "use ?mode=webrtc"}))
        return

    RESERVED.pop(n, None)   # o convidado chegou: a vaga e dele
    try:
        disp = Display(display_name)
    except Exception as e:
        if CAN_SPAWN:
            # a pagina espera e tenta de novo; a instancia sobe em ~30 s
            spawn_instance(n)
            await ws.send(json.dumps({"t": "error", "retry": True,
                                      "msg": f"preparando o jogo do jogador {n}…"}))
            return
        print(f"hello negado: jogador {n} sem tela {display_name} ({e}), ip={info['ip']}",
              flush=True)
        await ws.send(json.dumps({"t": "error", "msg": f"instancia do jogador {n} fora do ar"}))
        return

    old = ACTIVE.get(n)
    if old is not None and old["ws"] is not ws:
        print(f"jogador {n}: nova conexao substitui a anterior", flush=True)
        old["end"].set()
        await old["ws"].close(4000, "sessao aberta em outro lugar")
    session = {"ws": ws, "end": asyncio.Event()}
    ACTIVE[n] = session
    marker = Marker(disp)
    game = {"win": find_window(disp)}
    focus_game(disp, game["win"])

    print(f"entrada: jogador={n} tela={display_name} slot={player['slot']} ip={info['ip']} "
          f"hora={time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)

    pressed = set()          # modificadores atualmente pressionados por este cliente
    stats = {"blocked": 0}   # atalhos de sistema descartados antes de injetar

    container = av.open(f"{display_name}.0", format="x11grab",
                        options={"framerate": str(FPS), "video_size": f"{WIDTH}x{HEIGHT}"})
    stream = container.streams.video[0]

    def send_json(obj):
        try:
            asyncio.create_task(ws.send(json.dumps(obj)))
        except Exception:
            pass

    servers = ice_servers(await turn_credentials())
    # aiortc espera RTCConfiguration; uma lista funciona por acaso so quando esta
    # vazia ([] or RTCConfiguration()). Com STUN a lista nao e mais vazia -> crash.
    pc = RTCPeerConnection(RTCConfiguration(iceServers=servers))
    track = CaptureTrack(container, stream, loop, send_json)
    pc.addTrack(track)
    audio_track = AudioTrack(player["slot"]) if AUDIO else None
    if audio_track:
        audio_sender = pc.addTrack(audio_track)
        # O aiortc da o mesmo stream id (a=msid) a todas as faixas, e o Chrome entao
        # sincroniza o video com o audio: o video passa a esperar o buffer do audio.
        # Medido: jitterBufferDelay do video 119-135 ms com o msid comum, 3-4 ms com
        # msid proprio para o audio. Num RTS o cursor importa mais que o lip sync.
        audio_sender._stream_id = str(uuid.uuid4())

    @pc.on("icecandidate")
    def on_ice(candidate):
        if candidate and candidate.candidate:
            send_json({"t": "ice", "candidate": {
                "candidate": candidate.candidate,
                "sdpMLineIndex": 0,
            }})

    @pc.on("connectionstatechange")
    def on_state():
        print(f"estado: jogador={n} {pc.connectionState} hora={time.strftime('%H:%M:%S')}",
              flush=True)
        if pc.connectionState in ("failed", "closed"):
            session["end"].set()

    def on_event(ev, reply):
        """ping, probe e input: chegam pelo WebSocket ou pelo DataChannel."""
        t = ev.get("t")
        if t == "ping":
            reply({"t": "pong", "client_ts": ev.get("ts"), "server_ts": time.time()})
        elif t == "probe":
            drawn = marker.draw()
            reply({"t": "probe-at", "drawn": drawn})
            asyncio.create_task(hide_later(marker))
        elif t == "invite":
            # so o dono convida; o link (com o token) vai so para a pagina dele, nunca
            # para o log
            if n != OWNER or not INVITE_KEY:
                reply({"t": "invite-err", "msg": "convite indisponivel"})
                return
            m = invite_slot()
            if m is None:
                reply({"t": "invite-err", "msg": f"todas as vagas ocupadas (teto {INVITE_MAX})"})
                return
            RESERVED[m] = time.time() + INVITE_HOLD
            # ja comeca a subir o jogo: quando o amigo abrir o link, esta pronto
            if not display_alive(PLAYERS[m]["display"]):
                spawn_instance(m)
            tok, exp = make_invite(m)
            print(f"convite: jogador {n} convidou jogador {m}, vence "
                  f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(exp))}", flush=True)
            reply({"t": "invite-ok", "token": tok, "player": m, "expires": exp})
        elif t not in ("offer", "ice", "hello"):
            apply_input(disp, ev, pressed, stats, game)

    # Input pelo DataChannel "input" que a pagina abre: vai P2P junto com o video,
    # sem passar pelo tunel. A queda de 2026-10-14 17:09:59 foi o WebSocket, e com
    # ele caia a partida. Ordenado e confiavel: perder um mouseup prende o botao.
    channels = {}

    @pc.on("datachannel")
    def on_datachannel(channel):
        if channel.label != "input":
            return
        channels["input"] = channel

        def reply(obj):
            if channel.readyState == "open":
                channel.send(json.dumps(obj))

        @channel.on("message")
        def on_message(msg):
            if not isinstance(msg, str):
                return
            try:
                on_event(json.loads(msg), reply)
            except Exception as e:
                print(f"datachannel jogador {n}: {e!r}", flush=True)

        @channel.on("close")
        def on_close():
            # sem DataChannel e sem WebSocket nao ha como jogar: encerra. Com o
            # WebSocket vivo o input segue por ele e o Event so e lido quando ele cair.
            session["end"].set()

    await ws.send(json.dumps({
        "t": "hello-ok",
        "mode": "webrtc",
        "ice": ice_json(servers),
        # com TURN configurado, forcar relay: sem isso o navegador tenta primeiro o
        # candidato host, que nunca funciona para um cliente remoto
        "relay": bool(servers),
        # o dono ve o botao de convite
        "owner": n == OWNER and bool(INVITE_KEY),
        "player": n,
    }))

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
            else:
                on_event(ev, send_json)
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        log_ws_close(ws, n)
        try:
            dc = channels.get("input")
            if (not session["end"].is_set() and pc.connectionState == "connected"
                    and dc is not None and dc.readyState == "open"):
                # o tunel caiu, mas video e input andam P2P: a partida segue ate o
                # WebRTC cair (consent do ICE), o navegador fechar ou outra aba entrar
                print(f"jogador {n}: sem WebSocket, sessao segue pelo DataChannel", flush=True)
                await session["end"].wait()
        finally:
            await pc.close()
            track.stop()
            if audio_track:
                audio_track.stop()
            # esperar o decode em andamento: sem isso o thread do executor ainda esta
            # dentro de next(gen) quando o container e liberado -> segfault
            with track.decoding:
                container.close()
            marker.close()
            disp.close()
            CONN.pop(ws, None)
            if ACTIVE.get(n) is session:
                ACTIVE.pop(n, None)
            dur = time.time() - info["t0"]
            # log por conexao: IP, entrada, saida e duracao. Nunca o conteudo digitado.
            print(f"saida: jogador={n} ip={info['ip']} hora={time.strftime('%Y-%m-%d %H:%M:%S')} "
                  f"duracao={dur:.0f}s atalhos bloqueados={stats['blocked']}", flush=True)


HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")


async def process_request(connection, request):
    q = urllib.parse.urlparse(request.path).query
    CONN[connection] = {
        "ip": client_ip(request, connection),
        # o token pode vir no URL (?t=...) ou no campo "token" do hello; o valor nunca
        # aparece no log
        "token": urllib.parse.parse_qs(q).get("t", [""])[0],
        "t0": time.time(),
    }
    path = request.path.split("?")[0]
    if path == "/" and request.headers.get("Upgrade", "").lower() != "websocket":
        with open(HTML_PATH, "rb") as f:
            body = f.read()
        return Response(200, "OK", Headers({"Content-Type": "text/html; charset=utf-8"}), body)
    return None


async def main():
    print(f"webrtc (aiortc): porta {PORT}, {WIDTH}x{HEIGHT}@{FPS}, jogadores: " +
          ", ".join(f"{n}->{p['display']}/slot{p['slot']}" for n, p in PLAYERS.items()), flush=True)
    print(f"convites: {'on' if INVITE_KEY else 'off'}, teto {INVITE_MAX} jogadores, "
          f"spawn {'liberado' if CAN_SPAWN else 'sem permissao'} ({SPAWN})", flush=True)
    # ping de protocolo a cada 20 s + ping JSON do cliente a cada 30 s: um proxy ou o
    # tunel derruba um WebSocket ocioso (o Cloudflare fecha em ~100 s sem trafego).
    async with websockets.serve(handler, "0.0.0.0", PORT, process_request=process_request,
                                origins=ALLOWED_ORIGINS, ping_interval=20, ping_timeout=20):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())

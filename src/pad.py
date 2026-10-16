#!/usr/bin/env python3
"""Pad virtual por jogador (/dev/uinput), com estado por sequencia.

Protocolo de input fechado:
  - o cliente manda o ESTADO completo do pad, com sequencia crescente;
  - o servidor aceita so a sequencia maior que a ultima vista para aquele pad.
    Sequencia menor ou igual e descartada: reorder e retransmissao acontecem mesmo
    num DataChannel confiavel, e aplicar estado antigo solta um botao que o jogador
    ainda esta segurando;
  - o pad e neutralizado quando o peer desconecta OU quando vence o prazo sem
    atualizacao. A neutralizacao acontece AQUI, no servidor, porque a ultima mensagem
    do cliente pode nunca chegar (queda de rede, aba fechada, processo morto);
  - ao perder o foco o cliente manda estado neutro imediatamente, mas isso e
    otimizacao, nao autoridade: sem ele o prazo faz o mesmo trabalho.

Um pad por jogador, indice 0..MAX_PADS-1. O pad e um dispositivo evdev; o emulador
precisa abrir o /dev/input/eventN dele. Isso e ambiente, nao este arquivo.

O estado e mantido mesmo sem dispositivo (maquina de desenvolvimento, sem
/dev/uinput): os testes exercitam exatamente a parte do estado e da sequencia.
"""
import ctypes
import fcntl
import os
import struct
import time

EV_SYN = 0
EV_KEY = 1
EV_ABS = 3
SYN_REPORT = 0

# Codigo dos botoes em linux/input-event-codes.h. Nomes canonicos + apelidos: o
# formato exato do evento ainda precisa ser confirmado, entao o parser aceita os dois
# e normaliza aqui, num lugar so.
PAD_BUTTONS = {
    "cross": 304, "circle": 305, "square": 306, "triangle": 307,
    "l1": 310, "r1": 311, "l2": 312, "r2": 313,
    # BTN_SELECT 314, BTN_START 315, BTN_THUMBL 317 (316 e BTN_MODE, o Guide). Estavam
    # um acima: Enter (start) apertava o Guide, que o PCSX2 ignora, e V (select) o Start.
    "select": 314, "start": 315, "stick": 317,
    "dpad_up": 544, "dpad_down": 545, "dpad_left": 546, "dpad_right": 547,
}
BUTTON_ALIASES = {
    "a": "cross", "b": "circle", "x": "square", "y": "triangle",
    "lb": "l1", "rb": "r1", "lt": "l2", "rt": "r2",
    "up": "dpad_up", "down": "dpad_down", "left": "dpad_left", "right": "dpad_right",
}

# Eixos analogicos (linux/input-event-codes.h). O PCSX2/SDL so mostra "axes" se o
# dispositivo tem EV_ABS com absinfo valido; sem isso o pad e "0 axes" e o jogo que usa
# o analogico no menu nunca ve o movimento.
PAD_AXES = {"lx": 0, "ly": 1, "rx": 2, "ry": 3}
AXIS_MIN, AXIS_MAX = -1000, 1000
AXIS_NEUTRO = 0

# Prazo sem atualizacao do estado do pad. Contado a partir do ultimo evento ACEITO do
# cliente; o estado periodico que o servidor manda nao renova o prazo.
STALE_TIMEOUT = int(os.environ.get("AOM_PAD_STALE", "5"))

# struct input_event no kernel: struct timeval + type + code + value. Em x86_64 o
# timeval sao dois longs (8 bytes cada), entao 28 bytes. Em 32 bits seriam 20.
# struct input_event do kernel em x86_64: timeval (8+8) + __u16 type + __u16 code
# + __u32 value = 24 bytes. Com "qqIII" (28) o write nao devolve erro mas o kernel
# interpreta os bytes errado e o leitor do eventN nao recebe nada — foi o que o
# primeiro teste no asus mostrou.
# struct input_event: timeval(16) + __u16 type + __u16 code + __s32 value = 24.
# O value e SIGNADO: eixo analogico manda negativos, e "I" estoura com
# "struct.error: 'I' format requires 0 <= number <= 4294967295".
EVENT_FMT = "qqHHi"

# _IOC(dir, type, nr, size) do kernel. UINPUT_IOCTL_BASE e 'U' maiusculo
# (/usr/include/linux/uinput.h). Com 'u' minusculo o numero sai errado e o ioctl
# devolve EINVAL — foi o que aconteceu no primeiro teste no asus.
def _ioc(size, nr, direction=1, base="U"):
    return (direction << 30) | (size << 16) | (ord(base) << 8) | nr


UI_SET_EVBIT = _ioc(4, 100)
UI_SET_KEYBIT = _ioc(4, 101)
UI_SET_ABSBIT = _ioc(4, 103)
UI_DEV_SETUP = _ioc(92, 3)          # struct uinput_setup: 8 + 80 + 4
UI_ABS_SETUP = _ioc(28, 4)          # struct uinput_abs_setup: 2 + 2 + 24
UI_DEV_CREATE = _ioc(0, 1, 0)
UI_DEV_DESTROY = _ioc(0, 2, 0)


class InputId(ctypes.Structure):
    _fields_ = [("bustype", ctypes.c_uint16), ("vendor", ctypes.c_uint16),
                ("product", ctypes.c_uint16), ("version", ctypes.c_uint16)]


class UinputSetup(ctypes.Structure):
    # struct uinput_setup do kernel: input_id (8) + name[UINPUT_MAX_NAME_SIZE=80]
    # + ff_effects_max (4) = 92 bytes. O ultimo campo NAO e version.
    _fields_ = [("id", InputId), ("name", ctypes.c_char * 80),
                ("ff_effects_max", ctypes.c_uint32)]


class UinputAbsSetup(ctypes.Structure):
    # struct uinput_abs_setup: code(2) + filler implicito(2) + input_absinfo(24) = 28.
    # O input_absinfo do kernel comeca com VALUE, nao com minimum:
    #   value, minimum, maximum, fuzz, flat, resolution
    # Quem assume minimum no offset 4 escreve no campo errado e o kernel valida
    # [0,0], aceitando so absinfo todo zero. Sem absinfo o SDL enxerga "0 axes".
    _fields_ = [("code", ctypes.c_uint16), ("filler", ctypes.c_uint16),
                ("value", ctypes.c_int32), ("minimum", ctypes.c_int32),
                ("maximum", ctypes.c_int32), ("fuzz", ctypes.c_int32),
                ("flat", ctypes.c_int32), ("resolution", ctypes.c_int32)]


def button_name(key):
    """Nome canonico do botao, ou None. Aceita o nome canonico e os apelidos."""
    name = (key or "").strip().lower()
    return name if name in PAD_BUTTONS else BUTTON_ALIASES.get(name)


def pad_state(ev):
    """(sequencia, [botoes pressionados]) de um evento de estado.

    Aceita as duas formas, ate o formato ser confirmado:
      {"t":"pad","seq":7,"buttons":["cross","r2"]}
      {"t":"pad","seq":7,"state":{"cross":true,"r2":true}}
    """
    seq = ev.get("seq")
    if isinstance(seq, str):
        seq = int(seq) if seq.isdigit() else None
    raw = ev.get("buttons")
    if raw is None:
        raw = ev.get("state")
    if isinstance(raw, dict):
        return seq, [k for k, v in raw.items() if v]
    if isinstance(raw, list):
        return seq, raw
    return seq, []


def pad_axes(ev):
    """{eixo: valor} de um evento de estado. Eixo fora de [-1000,1000] e ignorado.

    O protocolo de botoes esta fechado; os eixos analogicos entraram depois, porque o
    PCSX2/SDL so ve eixos se o pad tem EV_ABS com absinfo, e o jogo pode usar o analogico
    no menu. Valores fora do range do dispositivo sao descartados pelo kernel, entao
    descartar aqui evita injetar algo que o leitor nunca recebe.
    """
    raw = ev.get("axes")
    out = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            name = (k or "").strip().lower()
            if name in PAD_AXES and isinstance(v, (int, float)) and AXIS_MIN <= v <= AXIS_MAX:
                out[name] = int(v)
    return out


class UinputPad:
    """Um pad evdev. Sem /dev/uinput (dev) o estado do Pad continua valendo; so nao
    se injeta nada, e e exatamente o que os testes medem."""

    def __init__(self, index, vendor=0x045E, product=0x028E):
        self.index = index
        self.name = f"aom-pad{index}"
        self.fd = None
        # vendor/product decidem se o SDL reconhece o pad como game controller. Com IDs
        # desconhecidos o PCSX2 nao tem mapeamento e nenhum botao chega ao jogo; com IDs
        # de um controlador conhecido ele aplica o mapeamento padrao. Os codigos evdev do
        # pad (304..317, 544..547; sem o 316) ja sao os standard que o SDL espera.
        self.vendor = vendor
        self.product = product

    def open(self):
        try:
            fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        except OSError as e:
            print(f"pad {self.index}: /dev/uinput indisponivel ({e})", flush=True)
            return False
        try:
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
            for code in PAD_BUTTONS.values():
                fcntl.ioctl(fd, UI_SET_KEYBIT, code)
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_ABS)
            for code in PAD_AXES.values():
                abs_setup = UinputAbsSetup(code=code, value=AXIS_NEUTRO,
                                           minimum=AXIS_MIN, maximum=AXIS_MAX,
                                           fuzz=0, flat=0, resolution=0)
                fcntl.ioctl(fd, UI_ABS_SETUP, bytes(abs_setup))
            setup = UinputSetup()
            setup.id = InputId(bustype=0x19, vendor=self.vendor, product=self.product, version=1)
            setup.name = self.name.encode()
            setup.ff_effects_max = 0
            fcntl.ioctl(fd, UI_DEV_SETUP, bytes(setup))
            fcntl.ioctl(fd, UI_DEV_CREATE)
            self.fd = fd
            return True
        except OSError as e:
            print(f"pad {self.index}: uinput falhou ({e})", flush=True)
            try:
                os.close(fd)
            except OSError:
                pass
            return False

    def press(self, code, down):
        if self.fd is None:
            return
        self._write(EV_KEY, code, 1 if down else 0)
        # O evdev agrupa os eventos de um mesmo instante com SYN_REPORT; sem isso o
        # leitor do emulador pode nao considerar o estado completo.
        self._write(EV_SYN, SYN_REPORT, 0)

    def axis(self, code, value):
        if self.fd is None:
            return
        self._write(EV_ABS, code, max(AXIS_MIN, min(AXIS_MAX, int(value))))
        self._write(EV_SYN, SYN_REPORT, 0)

    def _write(self, ev_type, code, value):
        now = time.time()
        buf = struct.pack(EVENT_FMT, int(now), int((now % 1) * 1000000),
                          ev_type, code, value)
        try:
            os.write(self.fd, buf)
        except OSError as e:
            print(f"pad {self.index}: write: {e}", flush=True)

    def close(self):
        if self.fd is None:
            return
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        except OSError:
            pass
        os.close(self.fd)
        self.fd = None


class Pad:
    """Estado do pad de um jogador. O estado, nao o evento, e a fonte da verdade.

    apply(seq, buttons): aceita so sequencia maior que a ultima vista. O que muda
    entre o estado aceito e o estado anterior e injetado; press duplicado e release
    de botao solto sao ignorados, porque aplicar um evento repetido prende o botao.

    neutralize(): solta tudo que estava pressionado. Chamada quando o peer desconecta
    ou quando vence STALE_TIMEOUT sem atualizacao. Nao mexe na sequencia: o proximo
    estado do cliente (sequencia maior) continua valendo.
    """

    def __init__(self, index, device=None, stale=STALE_TIMEOUT):
        self.index = index
        self.device = device
        self.stale_timeout = stale
        self.seq = 0
        self.pressed = set()
        self.axes = {name: AXIS_NEUTRO for name in PAD_AXES}
        self.updated = time.time()
        self.stats = {"aceitos": 0, "sequencia_antiga": 0, "desconhecidos": 0,
                      "neutralizacoes": 0}

    def _inject(self, name, down):
        if self.device is None:
            return
        self.device.press(PAD_BUTTONS[name], down)

    def _inject_axis(self, name, value):
        if self.device is None:
            return
        self.device.axis(PAD_AXES[name], value)

    def apply(self, seq, buttons, axes=None):
        """(mudou, motivo)."""
        if not isinstance(seq, int):
            return False, "sequencia ausente"
        if seq <= self.seq:
            self.stats["sequencia_antiga"] += 1
            return False, "sequencia antiga"

        state = set()
        for key in buttons:
            name = button_name(key)
            if name is None:
                self.stats["desconhecidos"] += 1
                continue
            state.add(name)

        new_axes = {name: AXIS_NEUTRO for name in PAD_AXES}
        for name, value in (axes or {}).items():
            new_axes[name] = value

        self.seq = seq
        self.updated = time.time()
        self.stats["aceitos"] += 1

        changed = False
        for name in sorted(state - self.pressed):
            self._inject(name, True)
            changed = True
        for name in sorted(self.pressed - state):
            self._inject(name, False)
            changed = True
        for name in sorted(new_axes):
            if new_axes[name] != self.axes[name]:
                self._inject_axis(name, new_axes[name])
                changed = True
        self.pressed = state
        self.axes = new_axes
        return changed, None

    def stale(self):
        return time.time() - self.updated > self.stale_timeout

    def neutralize(self, motivo):
        if not self.pressed and not any(self.axes.values()):
            return False
        soltos = sorted(self.pressed)
        for name in soltos:
            self._inject(name, False)
        for name in sorted(self.axes):
            if self.axes[name] != AXIS_NEUTRO:
                self._inject_axis(name, AXIS_NEUTRO)
        self.pressed = set()
        self.axes = {name: AXIS_NEUTRO for name in PAD_AXES}
        self.stats["neutralizacoes"] += 1
        print(f"pad {self.index}: neutralizado ({motivo}), {len(soltos)} botao(s) "
              f"soltos, seq={self.seq}", flush=True)
        return True

    def snapshot(self):
        return sorted(self.pressed)

    def close(self):
        if self.device is not None:
            self.device.close()

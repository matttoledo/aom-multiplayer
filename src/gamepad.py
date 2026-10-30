"""Gamepad virtual (uinput) para o motorista.

XTest injeta teclado e mouse, nao gamepad: um gamepad e um dispositivo evdev, e o Wine
enumera os controles por /dev/input/event*. O servidor cria um pad virtual e o Wine o ve
como um controle padrao. O dispositivo e um so, compartilhado, por isso so o motorista
injeta: dois espectadores disputariam o mesmo volante.

Precisa de `pip install evdev` no venv e de acesso a /dev/input/uinput (root ou o grupo
input). Sem evdev ou sem acesso o modulo ainda importa e o gamepad fica desligado: o
jogo segue jogavel pelo teclado.

Nao medido ainda: o Wine enxergar o uinput depende do Wine ter o driver de joystick por
evdev (o 11 do WineHQ tem). Confirmar com `wine /dev/input` ou o menu de controles do
NFSU antes de contar com isso.
"""

try:
    from evdev import UInput, AbsInfo, ecodes as E
except ImportError:
    UInput = None
    AbsInfo = None
    E = None

# Ordem dos botoes do "Standard Gamepad" da Gamepad API (w3u), que e o que o navegador
# devolve em navigator.getGamepads()[0].buttons[]. O lado do navegador manda a lista de
# 0/1 na mesma ordem; o codigo evdev de cada posicao esta aqui.
BUTTONS = [
    E.BTN_A,        # 0  A (Cross)
    E.BTN_B,        # 1  B (Circle)
    E.BTN_X,        # 2  X (Square)
    E.BTN_Y,        # 3  Y (Triangle)
    E.BTN_TL,       # 4  L1
    E.BTN_TR,       # 5  R1
    E.BTN_TL2,      # 6  L2
    E.BTN_TR2,      # 7  R2
    E.BTN_SELECT,   # 8  Select
    E.BTN_START,    # 9  Start
    E.BTN_THUMBL,   # 10 L3
    E.BTN_THUMBR,   # 11 R3
    E.BTN_DPAD_UP,  # 12 D-pad cima
    E.BTN_DPAD_DOWN,    # 13 D-pad baixo
    E.BTN_DPAD_LEFT,    # 14 D-pad esquerda
    E.BTN_DPAD_RIGHT,   # 15 D-pad direita
    E.BTN_MODE,     # 16 logo
] if E else []

# Eixos na mesma ordem de navigator.getGamepads()[0].axes: analógico esquerdo x/y,
# direito x/y. Os gatilhos vem como botões (6 e 7), não como eixo, porque a maioria dos
# controles os expõe nos dois e escrever os dois confundiria o jogo.
AXES = [E.ABS_X, E.ABS_Y, E.ABS_RX, E.ABS_RY] if E else []


class Gamepad:
    """Pad virtual. Escreve só o que muda: um evdev por frame de 30 fps é ruído."""

    def __init__(self, name="aom-virtual-pad"):
        self.uinput = None
        if UInput is None:
            return
        # O eixo precisa de AbsInfo: passar a tupla solta (codigo, min, max, fuzz, flat)
        # da TypeError em _prepare_events, e o setup do uinput responde EINVAL. Medido no
        # asus em 2026-10-15: so com AbsInfo o dispositivo e criado.
        caps = {E.EV_KEY: BUTTONS,
                E.EV_ABS: [(a, AbsInfo(0, -1000, 1000, 0, 0, 10)) for a in AXES]}
        try:
            self.uinput = UInput(caps, name=name)
        except Exception as e:
            # UInputError nao e OSError: sem /dev/uinput (grupo input ou root) o pad
            # fica desligado, e o servidor nao pode morrer por causa disso.
            print(f"gamepad: {e}", flush=True)
            return
        self.pressed = {}
        self.axes = {}

    def available(self):
        return self.uinput is not None

    def write(self, buttons, axes):
        if not self.uinput:
            return False
        for i, code in enumerate(BUTTONS):
            down = bool(buttons[i]) if i < len(buttons) else False
            if self.pressed.get(code) != down:
                self.uinput.write(E.EV_KEY, code, 1 if down else 0)
                self.pressed[code] = down
        for i, code in enumerate(AXES):
            val = int(axes[i] * 1000) if i < len(axes) else 0
            if self.axes.get(code) != val:
                self.uinput.write(E.EV_ABS, code, val)
                self.axes[code] = val
        self.uinput.write(E.EV_SYN, 0, 0)
        return True

    def close(self):
        if self.uinput:
            for code in self.pressed:
                if self.pressed[code]:
                    self.uinput.write(E.EV_KEY, code, 0)
            for code in self.axes:
                if self.axes[code]:
                    self.uinput.write(E.EV_ABS, code, 0)
            self.uinput.write(E.EV_SYN, 0, 0)
            self.uinput.close()

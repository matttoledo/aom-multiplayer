"""Teste: o PCSX2 mapeia sozinho um pad que o SDL reconhece como game controller?

O inputmapping.ini esta vazio, entao nenhum botao chega ao jogo. O SDL decide se um
dispositivo e um game controller pela tabela vendor/product. Um uinput com IDs de um
controlador conhecido, usando os codigos evdev standard (BTN_A=304, BTN_DPAD_UP=544...),
pode ser reconhecido e ai o PCSX2 aplica o mapeamento padrao.

Uso: python3 scripts/idtest.py <vendor_hex> <product_hex>
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from pad import PAD_BUTTONS, UinputPad  # noqa: E402

VENDOR = int(sys.argv[1], 16) if len(sys.argv) > 1 else 0x045E
PRODUCT = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0x028E

p = UinputPad(0)
p.name = "aom-pad0"
fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
import ctypes  # noqa: E402
import fcntl  # noqa: E402
import struct  # noqa: E402

from pad import EV_KEY, UI_DEV_CREATE, UI_DEV_SETUP, UI_SET_EVBIT, UI_SET_KEYBIT, InputId, UinputSetup  # noqa: E402

fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
for code in PAD_BUTTONS.values():
    fcntl.ioctl(fd, UI_SET_KEYBIT, code)

setup = UinputSetup()
setup.id = InputId(bustype=0x19, vendor=VENDOR, product=PRODUCT, version=1)
setup.name = p.name.encode()
setup.ff_effects_max = 0
fcntl.ioctl(fd, UI_DEV_SETUP, bytes(setup))
fcntl.ioctl(fd, UI_DEV_CREATE)
print(f"pad criado vendor={VENDOR:04x} product={PRODUCT:04x}", flush=True)

# SDL ve o dispositivo pelo node de evento; imprimir qual e
for d in sorted(os.listdir("/sys/class/input")):
    if not d.startswith("input"):
        continue
    try:
        name = open(os.path.join("/sys/class/input", d, "name")).read().strip()
    except OSError:
        continue
    if name == p.name:
        print(f"/sys/class/input/{d} name={name}", flush=True)

time.sleep(120)

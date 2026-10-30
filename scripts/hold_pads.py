"""Mantem N pads uinput vivos e aceita comandos por FIFO, para a Fase 0 do Bomba Patch.

O emulador precisa dos pads existindo ANTES de enumerar, e o mapeamento do PCSX2 e por
dispositivo: sem mapeamento nenhum botao chega ao jogo. Este script usa exatamente o
caminho do servidor (Pad sobre UinputPad, estado com sequencia crescente), so que a
fonte dos comandos e um fifo em vez de um DataChannel.

Uso: python3 scripts/hold_pads.py [n] [fifo]
Comandos no fifo, um por linha:
  <pad> <botao> press|release       ex.: 0 cross press
  <pad> <botao>                     pressao e solta em 400 ms
  estado                            imprime o estado de cada pad
Sem fifo o script so fica vivo e imprime o node de evento de cada pad.
"""
import glob
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from pad import PAD_AXES, PAD_BUTTONS, Pad, UinputPad  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 2
FIFO = sys.argv[2] if len(sys.argv) > 2 else ""
# vendor/product definem se o SDL reconhece o dispositivo como game controller. Com IDs
# de um controlador conhecido o PCSX2 aplica o mapeamento padrao; com IDs desconhecidos
# o inputmapping.ini fica vazio e nenhum botao chega ao jogo.
VENDOR = int(sys.argv[3], 16) if len(sys.argv) > 3 else 0x045E
PRODUCT = int(sys.argv[4], 16) if len(sys.argv) > 4 else 0x028E


def event_node(index):
    """input_eventN nao e o numero do dispositivo: resolver pelo nome em /sys/class/input.

    O numero do node e independente do numero do dispositivo (input200 pode ser event14),
    entao o node e o entry `event*` dentro do diretorio do dispositivo.
    """
    name = f"aom-pad{index}"
    for d in glob.glob("/sys/class/input/input*"):
        try:
            # o arquivo "device" e um symlink para o dispositivo de barramento; o nome do
            # pad esta no arquivo "name"
            got = open(os.path.join(d, "name")).read().strip()
        except OSError:
            continue
        if got == name:
            for e in glob.glob(os.path.join(d, "event*")):
                node = f"/dev/input/{os.path.basename(e)}"
                if os.path.exists(node):
                    return node
            return None
    return None


def main():
    pads = []
    for i in range(N):
        device = UinputPad(i, VENDOR, PRODUCT)
        if not device.open():
            print(f"pad {i}: nao criou", flush=True)
            continue
        node = None
        for _ in range(20):
            node = event_node(i)
            if node:
                break
            time.sleep(0.5)
        print(f"pad {i}: {device.name} node={node}", flush=True)
        pads.append(Pad(i, device))

    if not pads:
        return

    if not FIFO:
        print("pads vivos; Ctrl-C para sair", flush=True)
        while True:
            time.sleep(1)

    if not os.path.exists(FIFO):
        os.mkfifo(FIFO)
    print(f"comandos em {FIFO}", flush=True)

    seq = 0
    # um escritor no fifo fecha e o leitor recebe EOF; sem reabrir o script sairia e o pad
    # desapareceria (foi exatamente o que aconteceu no primeiro teste: PCSX2 registrou
    # "Gamepad 2 removed")
    while True:
        with open(FIFO, "r") as fh:
            for line in fh:
                parts = line.split()
                if not parts:
                    continue
                if parts[0] == "estado":
                    for p in pads:
                        print(f"pad {p.index}: seq={p.seq} {sorted(p.pressed)}", flush=True)
                    continue
                idx = int(parts[0])
                name = parts[1]
                if idx >= len(pads):
                    print(f"comando invalido: {line.strip()}", flush=True)
                    continue
                if name in PAD_AXES:
                    # <pad> <eixo> <valor>, ex.: 0 ly -1000 (stick para cima)
                    try:
                        valor = int(parts[2])
                    except (IndexError, ValueError):
                        print(f"comando invalido: {line.strip()}", flush=True)
                        continue
                    seq += 1
                    pads[idx].apply(seq, [], {name: valor})
                    continue
                if name not in PAD_BUTTONS:
                    print(f"comando invalido: {line.strip()}", flush=True)
                    continue
                action = parts[2] if len(parts) > 2 else "press"
                seq += 1
                changed, motivo = pads[idx].apply(seq, [name] if action == "press" else [])
                print(f"pad {idx}: {name} {action} seq={seq} "
                      f"{'aplicado' if changed else motivo} estado={sorted(pads[idx].pressed)}",
                      flush=True)
                if action == "press" and len(parts) == 2:
                    time.sleep(0.4)
                    seq += 1
                    pads[idx].apply(seq, [])

    for p in pads:
        if p.device:
            p.device.close()


main()

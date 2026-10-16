#!/usr/bin/env python3
"""Teste funcional do uinput: criar um pad, confirmar que o node de evento aparece e
que um leitor recebe o press. Ter o header instalado nao comprova acesso funcional.

Dois fatos que o teste precisa tratar e que o primeiro teste ignorou:
  - o numero do node NAO e o numero do input device: /sys/class/input/input57 traz o
    symlink `event15`, e e event15 que vira /dev/input/event15;
  - o node aparece de forma assincrona, pelo udev, ~1-2 s depois do UI_DEV_CREATE.
    Ler antes disso da ENOENT e parece falha de permissao;
  - o registro do evento tem 24 bytes em x86_64 (timeval 16 + u16 type + u16 code +
    u32 value). Com 28 bytes o write nao da erro, mas o kernel interpreta os bytes
    errado e o leitor nao recebe nada.

Uso: python3 uinput_probe.py [indice]
"""
import os
import select
import struct
import time

import pad

WAIT_NODE = 5.0


def find_sysfs(name, wait=3.0):
    end = time.time() + wait
    while time.time() < end:
        for d in sorted(os.listdir("/sys/class/input")):
            path = os.path.join("/sys/class/input", d)
            try:
                with open(os.path.join(path, "name")) as f:
                    if f.read().strip() == name:
                        return path
            except OSError:
                pass
    return None


def event_node(sysfs):
    """Nome do eventN que o kernel expoe para este input device."""
    for entry in os.listdir(sysfs):
        if entry.startswith("event"):
            return entry
    return None


def main(index=0):
    p = pad.UinputPad(index)
    if not p.open():
        print("FALHOU: /dev/uinput nao abriu (permissoes)")
        return 1

    sysfs = find_sysfs(p.name)
    if not sysfs:
        print("FALHOU: dispositivo nao apareceu em /sys/class/input")
        p.close()
        return 1
    print("MEDIDO: input device:", sysfs)

    ev_name = event_node(sysfs)
    node = "/dev/input/" + ev_name
    end = time.time() + WAIT_NODE
    while not os.path.exists(node) and time.time() < end:
        time.sleep(0.2)
    if not os.path.exists(node):
        print(f"FALHOU: {node} nao foi criado pelo udev em {WAIT_NODE} s")
        p.close()
        return 1
    print("MEDIDO: event node:", node, "(criado pelo udev, nao e o numero do input device)")

    # O node aparece antes de o udev terminar de ajustar as permissoes: abrir logo de
    # vez pode dar EACCES. Tentar ate o prazo, com o grupo input ja confirmado.
    fd = None
    end = time.time() + WAIT_NODE
    while fd is None and time.time() < end:
        try:
            fd = os.open(node, os.O_RDONLY)
        except OSError as e:
            if e.errno != 13:
                print("FALHOU: abrir", node, ":", e)
                p.close()
                return 1
            time.sleep(0.3)
    if fd is None:
        print(f"FALHOU: {node} nao abriu em {WAIT_NODE} s (permissoes)")
        p.close()
        return 1
    os.set_blocking(fd, False)
    print("MEDIDO: node aberto por matt (grupo input, sem root)")

    p.press(pad.PAD_BUTTONS["cross"], True)

    # O evento chega pelo udev/kernel de forma assincrona; ler sem esperar da EAGAIN
    # e parece falha de acesso.
    if not select.select([fd], [], [], 2.0)[0]:
        print("FALHOU: nenhum evento em 2 s")
        os.close(fd)
        p.close()
        return 1

    raw = os.read(fd, struct.calcsize(pad.EVENT_FMT) * 8)
    os.close(fd)

    n = struct.calcsize(pad.EVENT_FMT)
    got = [struct.unpack(pad.EVENT_FMT, raw[i:i + n]) for i in range(0, len(raw), n)]
    print("MEDIDO: eventos lidos:", [(t, c, v) for _, _, t, c, v in got])
    ok = any(t == pad.EV_KEY and c == pad.PAD_BUTTONS["cross"] and v == 1
            for _, _, t, c, v in got)
    print("RESULTADO:", "leitor recebeu o press" if ok else "leitor NAO recebeu")

    p.press(pad.PAD_BUTTONS["cross"], False)
    p.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

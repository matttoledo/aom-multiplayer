#!/usr/bin/env python3
"""Liga o stream de audio de uma instancia do jogo ao slot daquele jogador.

O game roda sob Wine e usa winepulse.drv, que conecta ao PipeWire. O policy do
PipeWire encaminha o stream para o sink default (aom_default, que nao e capturado).
Este script adiciona o link explicito stream -> slot; o servidor captura
slot.monitor e entrega esse audio apenas para aquele jogador.

Uso:
    python3 audio_route.py <pid> <slot>        # slot 1..8
    python3 audio_route.py --list              # streams, sinks e links atuais

Dependencias: pw-dump e pw-link (PipeWire 1.0). pw-cli create-link NAO funciona
aqui: ele retorna 0 e nao cria nada. pw-link cria.

Fatos que o codigo depende (todos medidos):
  - o node do stream traz client.id, nao application.process.id; o pid vem do objeto
    Client correspondente (pipewire.sec.pid), e o client.id desse objeto e o proprio
    id do objeto no pw-dump (nao um prop);
  - os ports dos sinks aparecem como objetos PipeWire:Interface:Port no pw-dump
    (o param Port do node vem vazio), e e por esses ids que pw-link trabalha;
  - o stream so existe quando a instancia esta tocando som.
"""
import argparse
import json
import subprocess
import sys

SLOT_PREFIX = "aom_slot"
DEFAULT_SINK = "aom_default"


def dump():
    raw = subprocess.check_output(["pw-dump"], timeout=20)
    if not raw.strip():
        # pw-dump as vezes devolve vazio quando o daemon esta ocupado
        raise SystemExit("pw-dump nao devolveu nada; tente de novo")
    return json.loads(raw.decode())


def index(objects):
    nodes, ports, links, clients = {}, {}, [], {}
    for o in objects:
        props = (o.get("info") or {}).get("props") or {}
        t = o.get("type")
        if t == "PipeWire:Interface:Node":
            nodes[o["id"]] = props
        elif t == "PipeWire:Interface:Port":
            ports[o["id"]] = props
        elif t == "PipeWire:Interface:Link":
            links.append(props)
        elif t == "PipeWire:Interface:Client":
            # No Client, client.id NAO e um prop: o id do objeto e o client.id.
            clients[o["id"]] = props.get("pipewire.sec.pid")
    return nodes, ports, links, clients


def find_stream(nodes, clients, pid):
    for node_id, props in nodes.items():
        if props.get("media.class") != "Stream/Output/Audio":
            continue
        if str(clients.get(props.get("client.id"))) == str(pid):
            return node_id
    return None


def ports_of(ports, node_id, direction):
    return {
        p.get("port.name"): p_id
        for p_id, p in ports.items()
        if p.get("node.id") == node_id and p.get("port.name", "").startswith(direction)
    }


def already_linked(links, stream_id, slot_id):
    return any(
        l.get("link.output.node") == stream_id and l.get("link.input.node") == slot_id
        for l in links
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pid", nargs="?", help="pid da instancia do jogo")
    ap.add_argument("slot", nargs="?", type=int, help="slot 1..8")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    objects = dump()
    nodes, ports, links, clients = index(objects)

    if args.list or not args.pid:
        for node_id, props in nodes.items():
            if props.get("media.class", "").startswith("Stream/"):
                print(f"stream {node_id} {props.get('node.name')} pid={clients.get(props.get('client.id'))}")
        for node_id, props in nodes.items():
            if props.get("media.class") == "Audio/Sink":
                print(f"sink {node_id} {props.get('node.name')}")
        for l in links:
            print(f"link {l.get('link.output.node')} -> {l.get('link.input.node')}")
        return 0

    slot_name = f"{SLOT_PREFIX}{args.slot}"
    slot_id = next((i for i, p in nodes.items() if p.get("node.name") == slot_name), None)
    if slot_id is None:
        print(f"slot {slot_name} nao existe (instale scripts/pipewire-slots.conf)", file=sys.stderr)
        return 1

    stream_id = find_stream(nodes, clients, args.pid)
    if stream_id is None:
        # O stream so aparece quando a instancia toca som. Sem stream nao ha o que
        # linkar: o chamador deve tentar de novo quando o jogo comecar a tocar.
        print(f"pid {args.pid} sem stream de audio", file=sys.stderr)
        return 2

    out_ports = ports_of(ports, stream_id, "output")
    in_ports = ports_of(ports, slot_id, "playback")
    if not out_ports or not in_ports:
        print("ports nao encontrados", file=sys.stderr)
        return 3

    for ch in ("FL", "FR"):
        o, i = out_ports.get("output_" + ch), in_ports.get("playback_" + ch)
        if o is None or i is None:
            continue
        subprocess.run(["pw-link", str(o), str(i)], check=False, timeout=20)
        print(f"linkado {ch}: stream {stream_id} -> {slot_name}")

    if already_linked(links, stream_id, slot_id):
        print(f"(ja havia link para {slot_name})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Comparativo de qualidade no conteudo real do jogo, sem visao.

PSNR por frame (subamostrado, sem numpy) + tamanho do access unit.
O que importa para "pixelizado" e o QP: qmax e o teto de qualidade. Se o encoder
bate em qmax=38, aparece blockiness de macroblock.
"""
import fractions
import math
import av

STEP = 8


def psnr_frame(src, dec):
    a = src.reformat(width=1280, height=720, format="yuv420p")
    b = dec.reformat(width=1280, height=720, format="yuv420p")
    total = 0.0
    n = 0
    for pl_a, pl_b in zip(a.planes, b.planes):
        ba = bytes(pl_a)
        bb = bytes(pl_b)
        stride = pl_a.line_size
        h = 720 if pl_a == a.planes[0] else 360
        w = 1280 if pl_a == a.planes[0] else 640
        for y in range(0, h, STEP):
            row = y * stride
            for x in range(0, w, STEP):
                d = ba[row + x] - bb[row + x]
                total += d * d
                n += 1
    if n == 0 or total == 0:
        return 99.0
    return 10 * math.log10(n * 255.0 * 255.0 / total)


def run(opts, attrs, frames=120):
    enc = av.CodecContext.create("h264_nvenc", "w")
    enc.options = dict(opts, delay="0")
    enc.width, enc.height = 1280, 720
    enc.bit_rate = 6000000
    enc.pix_fmt = "yuv420p"
    enc.framerate = fractions.Fraction(1, 30)
    enc.time_base = fractions.Fraction(1, 30)
    for k, v in attrs.items():
        setattr(enc, k, v)

    dec = av.CodecContext.create("h264", "r")
    d = av.open(":2.0", format="x11grab", options={"framerate": "30", "video_size": "1280x720"})
    s = d.streams.video[0]

    vals = []
    sizes = []
    for i, f in enumerate(d.decode(s)):
        if i >= frames:
            break
        out = None
        for pk in enc.encode(f):
            if pk.size:
                sizes.append(pk.size)
                r = dec.decode(pk)
                if r:
                    out = r[0]
        if out is not None:
            vals.append(psnr_frame(f, out))

    if not vals:
        print(opts, attrs, "-> sem frames decodificados")
        return
    mx = max(sizes)
    p95 = sorted(sizes)[int(len(sizes) * 0.95)]
    print(f"{opts} {attrs}: PSNR medio {sum(vals)/len(vals):.2f} dB | "
          f"min {min(vals):.2f} | max AU {mx} B ({mx/1250:.0f} pkt) | "
          f"p95 AU {p95} B ({p95/1250:.0f} pkt) | "
          f"media {sum(sizes)/len(sizes):.0f} B/f -> {sum(sizes)/len(sizes)*30*8/1000:.0f} kbps")


base = {"tune": "ll", "profile": "main", "rc": "vbr", "coder": "cabac"}

run(dict(base, preset="p2"), {"gop_size": 15, "qmin": 24, "qmax": 38})
run(dict(base, preset="p4"), {"gop_size": 15, "qmin": 24, "qmax": 38})
run(dict(base, preset="p5"), {"gop_size": 15, "qmin": 24, "qmax": 38})
run(dict(base, preset="p4", **{"spatial-aq": "1", "temporal-aq": "1"}),
    {"gop_size": 15, "qmin": 24, "qmax": 38})
run(dict(base, preset="p4", **{"spatial-aq": "1", "temporal-aq": "1"}),
    {"gop_size": 15, "qmin": 20, "qmax": 30})
run(dict(base, preset="p4", **{"spatial-aq": "1", "temporal-aq": "1"}),
    {"gop_size": 15, "qmin": 18, "qmax": 26})

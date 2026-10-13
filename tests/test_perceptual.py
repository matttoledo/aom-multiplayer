#!/usr/bin/env python3
"""Efeito das opcoes perceptuais no mesmo QP teto.

Blockiness e o que o deblocking filter e o adaptive quantization enderecam.
PSNR nao captura percepcao, mas mostra se as opcoes realmente aplicam.
"""
import fractions
import math
import av

STEP = 8
BASE = {"tune": "ll", "profile": "main", "rc": "vbr", "coder": "cabac", "preset": "p4"}


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


d = av.open(":2.0", format="x11grab", options={"framerate": "30", "video_size": "1280x720"})
s = d.streams.video[0]
frames = []
for i, f in enumerate(d.decode(s)):
    if i >= 120:
        break
    frames.append(f)


def run(opts, qmax):
    enc = av.CodecContext.create("h264_nvenc", "w")
    enc.options = dict(opts, delay="0")
    enc.width, enc.height = 1280, 720
    enc.bit_rate = 6000000
    enc.pix_fmt = "yuv420p"
    enc.framerate = fractions.Fraction(1, 30)
    enc.time_base = fractions.Fraction(1, 30)
    enc.gop_size = 15
    enc.qmin = 18
    enc.qmax = qmax
    applied = dict(enc.options)

    dec = av.CodecContext.create("h264", "r")
    vals, sizes = [], []
    for f in frames:
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
        print(opts, "-> sem frames (opcao pode ter sido recusada)")
        return
    real = sum(sizes) / len(sizes) * 30 * 8 / 1000
    print(f"qmax {qmax} {sorted(applied)}: PSNR {sum(vals)/len(vals):.2f} dB | real {real:.0f} kbps")


run(BASE, 34)
run(dict(BASE, **{"spatial-aq": "1", "temporal-aq": "1"}), 34)
run(dict(BASE, **{"spatial-aq": "1", "temporal-aq": "1", "deblock": "1"}), 34)
run(dict(BASE, **{"spatial-aq": "1", "temporal-aq": "1", "deblock": "0"}), 34)

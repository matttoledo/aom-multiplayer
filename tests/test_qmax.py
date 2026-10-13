#!/usr/bin/env python3
"""Varredura de qmax no conteudo real do jogo.

qmax e o teto de QP. Acima dele o encoder nao pode comprimir mais, entao o
bitrate real estoura o alvo. A qualidade percebida (blockiness) e definida pelo QP.
"""
import fractions
import math
import statistics
import av

STEP = 8
OPTS = {"tune": "ll", "profile": "main", "rc": "vbr", "coder": "cabac", "preset": "p4",
        "spatial-aq": "1", "temporal-aq": "1"}


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

print(f"conteudo: {len(frames)} frames")


def run(fps, qmin, qmax, target):
    enc = av.CodecContext.create("h264_nvenc", "w")
    enc.options = dict(OPTS, delay="0")
    enc.width, enc.height = 1280, 720
    enc.bit_rate = target
    enc.pix_fmt = "yuv420p"
    enc.framerate = fractions.Fraction(1, fps)
    enc.time_base = fractions.Fraction(1, fps)
    enc.gop_size = fps // 2
    enc.qmin = qmin
    enc.qmax = qmax

    dec = av.CodecContext.create("h264", "r")
    vals, sizes = [], []
    step = 30 // fps
    for i, f in enumerate(frames):
        if i % step:
            continue
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
        print(f"fps {fps} qmax {qmax} alvo {target//1000}k -> sem frames")
        return
    real = sum(sizes) / len(sizes) * fps * 8 / 1000
    print(f"fps {fps} qmax {qmax} alvo {target//1000}k: PSNR {sum(vals)/len(vals):.2f} dB | "
          f"min {min(vals):.2f} | real {real:.0f} kbps | max AU {max(sizes)/1250:.0f} pkt")


print("--- 30 fps, alvo 6 Mbps, varredura de qmax ---")
for qmax in (38, 36, 34, 32, 30, 28, 26):
    run(30, 18, qmax, 6000000)

print("--- fps menor, mesmo alvo 6 Mbps, qmax 38 (mais bits por frame) ---")
for fps in (30, 24, 20, 15):
    run(fps, 18, 38, 6000000)

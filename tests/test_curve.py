#!/usr/bin/env python3
"""Curva qualidade x bitrate no conteudo real do jogo + antes/depois em PNG.

Sem visao: o PNG vai para o Mac e o Matheus olha. O que se mede aqui e o QP teto
(qmax), que e o que produz o blockiness.
"""
import fractions
import math
import av

STEP = 8
BASE = {"tune": "ll", "profile": "main", "rc": "vbr", "coder": "cabac", "preset": "p4",
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


def run(target, qmax):
    enc = av.CodecContext.create("h264_nvenc", "w")
    enc.options = dict(BASE, delay="0")
    enc.width, enc.height = 1280, 720
    enc.bit_rate = target
    enc.pix_fmt = "yuv420p"
    enc.framerate = fractions.Fraction(1, 30)
    enc.time_base = fractions.Fraction(1, 30)
    enc.gop_size = 15
    enc.qmin = 18
    enc.qmax = qmax

    dec = av.CodecContext.create("h264", "r")
    vals, sizes = [], []
    first = None
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
            if first is None:
                first = (f, out)
    real = sum(sizes) / len(sizes) * 30 * 8 / 1000
    print(f"alvo {target//1000}k qmax {qmax}: PSNR {sum(vals)/len(vals):.2f} dB | "
          f"real {real:.0f} kbps | max AU {max(sizes)/1250:.0f} pkt")
    return first


# curva: encoder livre para atingir o alvo (qmax alto) -> mostra o que cada
# bitrate compra de qualidade neste conteudo
for t, q in ((6000000, 45), (10000000, 45), (14000000, 34), (20000000, 30)):
    run(t, q)

# antes/depois: mesma frame, qmax 38 (config atual) vs qmax 34
src, dec38 = run(6000000, 38)
_, dec34 = run(14000000, 34)
for name, fr in (("antes_qmax38", dec38), ("depois_qmax34", dec34), ("original", src)):
    fr.to_image().save(f"/tmp/{name}.png")
    print(f"/tmp/{name}.png")

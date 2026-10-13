#!/usr/bin/env python3
"""Experimento decisivo: o que limita a qualidade, o bitrate alvo ou o qmax?

Se o encoder produz MAIS bits que o alvo, o qmax esta bindando: ele nao pode
subir o QP para caber no alvo, e a tela fica pixelizada mesmo com bitrate alto.
"""
import fractions
import math
import statistics
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


def luma_stats(frame):
    f = frame.reformat(width=1280, height=720, format="yuv420p")
    vals = bytes(f.planes[0])[::STEP]
    return statistics.mean(vals), statistics.pstdev(vals)


OPTS = {"tune": "ll", "profile": "main", "rc": "vbr", "coder": "cabac", "preset": "p4",
        "spatial-aq": "1", "temporal-aq": "1"}

# frames capturados uma vez, reencodados com cada config (mesmo conteudo)
d = av.open(":2.0", format="x11grab", options={"framerate": "30", "video_size": "1280x720"})
s = d.streams.video[0]
frames = []
for i, f in enumerate(d.decode(s)):
    if i >= 120:
        break
    frames.append(f)

print(f"conteudo: {len(frames)} frames | luma media {statistics.mean([luma_stats(f)[0] for f in frames]):.1f} "
      f"| desvio {statistics.mean([luma_stats(f)[1] for f in frames]):.1f}")


def run(bitrate, qmin, qmax):
    enc = av.CodecContext.create("h264_nvenc", "w")
    enc.options = dict(OPTS, delay="0")
    enc.width, enc.height = 1280, 720
    enc.bit_rate = bitrate
    enc.pix_fmt = "yuv420p"
    enc.framerate = fractions.Fraction(1, 30)
    enc.time_base = fractions.Fraction(1, 30)
    enc.gop_size = 15
    enc.qmin = qmin
    enc.qmax = qmax

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
        print(f"bitrate {bitrate} qmin {qmin} qmax {qmax} -> sem frames")
        return
    mx = max(sizes)
    real = sum(sizes) / len(sizes) * 30 * 8 / 1000
    flag = "qmax bindando" if real > bitrate / 1000 * 1.15 else "bitrate bindando"
    print(f"alvo {bitrate//1000}k qmin {qmin} qmax {qmax}: PSNR {sum(vals)/len(vals):.2f} dB | "
          f"real {real:.0f} kbps | max AU {mx/1250:.0f} pkt | {flag}")


run(2000000, 24, 38)
run(6000000, 24, 38)
run(12000000, 24, 38)
run(6000000, 20, 30)
run(12000000, 20, 30)
run(10000000, 22, 32)

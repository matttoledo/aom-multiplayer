#!/usr/bin/env python3
"""Testa a afirmacao "todos os quadros sao keyframe" no caminho VP8.

Reproduz exatamente o que aiortc faz no Vp8Encoder (mesmos options) e conta
pacotes com is_keyframe. Se gop_size=3000, keyframe e raro; se o encoder for
recriado a cada frame (mudanca de bitrate >10%), todo frame vira keyframe.
"""
import fractions
import math
import av

STEP = 8
OPTS = {
    "bufsize": "4800000",
    "cpu-used": "-6",
    "deadline": "realtime",
    "lag-in-frames": "0",
    "minrate": "4800000",
    "maxrate": "4800000",
    "noise-sensitivity": "4",
    "overshoot-pct": "15",
    "partitions": "0",
    "static-thresh": "1",
    "undershoot-pct": "100",
}


def psnr_frame(src, dec):
    a = src.reformat(width=1280, height=720, format="yuv420p")
    b = dec.reformat(width=1280, height=720, format="yuv420p")
    total = 0.0
    n = 0
    for pl_a, pl_b in zip(a.planes, b.planes):
        ba, bb = bytes(pl_a), bytes(pl_b)
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

enc = av.CodecContext.create("libvpx", "w")
enc.options = OPTS
enc.width, enc.height = 1280, 720
enc.bit_rate = 4800000
enc.pix_fmt = "yuv420p"
enc.gop_size = 3000
enc.qmin = 2
enc.qmax = 56
enc.framerate = fractions.Fraction(1, 30)
enc.time_base = fractions.Fraction(1, 30)

dec = av.CodecContext.create("libvpx", "r")
kf = 0
sizes = []
vals = []
for f in frames:
    out = None
    for pk in enc.encode(f):
        if pk.size:
            sizes.append(pk.size)
            kf += 1 if pk.is_keyframe else 0
            r = dec.decode(pk)
            if r:
                out = r[0]
    if out is not None:
        vals.append(psnr_frame(f, out))

avg = sum(sizes) / len(sizes)
print(f"VP8 (libvpx, o que o aiortc faz): keyframes {kf}/{len(sizes)} pacotes | "
      f"media {avg:.0f} B/frame -> {avg*30*8/1000:.0f} kbps | "
      f"PSNR {sum(vals)/len(vals):.2f} dB")

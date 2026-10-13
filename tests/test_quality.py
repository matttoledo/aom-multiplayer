import fractions
import math
import av

STEP = 8  # subamostragem: sem numpy, PSNR completo seria lento


def psnr_frame(src, dec):
    a = src.reformat(width=1280, height=720, format="yuv420p")
    b = dec.reformat(width=1280, height=720, format="yuv420p")
    total = 0.0
    n = 0
    for pl_a, pl_b in zip(a.planes, b.planes):
        ba = bytes(pl_a)
        bb = bytes(pl_b)
        stride = pl_a.line_size
        for y in range(0, 720 if pl_a == a.planes[0] else 360, STEP):
            row = y * stride
            for x in range(0, 1280 if pl_a == a.planes[0] else 640, STEP):
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


base = {"preset": "p2", "tune": "ll", "profile": "main", "rc": "vbr", "coder": "cabac"}
run(base, {"gop_size": 30})
run(base, {"gop_size": 30, "qmin": 20, "qmax": 34})
run(base, {"gop_size": 30, "qmin": 24, "qmax": 38})
run(base, {"gop_size": 30, "qmin": 28, "qmax": 42})
run(dict(base, rc="cbr"), {"gop_size": 30, "qmin": 24, "qmax": 38})

# periodic intra refresh: em vez de um IDR gigante, alguns MB intra por frame ao
# longo do GOP -> access units pequenos e regulares (melhor pacing, menos fragil)
run(dict(base, **{"intra-refresh": "1"}), {"gop_size": 30, "qmin": 24, "qmax": 38})
run(dict(base, **{"intra-refresh": "1"}), {"gop_size": 60, "qmin": 24, "qmax": 38})
run(base, {"gop_size": 15, "qmin": 24, "qmax": 38})
run(base, {"gop_size": 20, "qmin": 24, "qmax": 38})

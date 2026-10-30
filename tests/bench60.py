"""Custo por etapa a 60 fps: grab x11grab (PyAV) e encode h264_nvenc, 640x480 no :1."""
import av, fractions, statistics, sys, time
off = sys.argv[1] if len(sys.argv) > 1 else "1,25"
N = 300
c = av.open(f":1.0+{off}", format="x11grab",
            options={"framerate": "60", "video_size": "640x480", "draw_mouse": "0"})
g = c.decode(c.streams.video[0])
enc = av.CodecContext.create("h264_nvenc", "w")
enc.options = {"preset": "p4", "tune": "ll", "profile": "main", "delay": "0", "rc": "vbr",
               "spatial-aq": "1", "temporal-aq": "1"}
grab, encs, sizes, uniq, last = [], [], [], 0, None
t0 = time.time()
for i in range(N):
    a = time.perf_counter(); f = next(g); b = time.perf_counter()
    if i == 0:
        enc.width, enc.height, enc.pix_fmt = f.width, f.height, f.format.name
        enc.framerate = fractions.Fraction(60, 1); enc.time_base = fractions.Fraction(1, 60)
        enc.gop_size = 30; enc.bit_rate = 17_000_000
    raw = bytes(f.planes[0])
    h = hash(raw[::97])
    if h != last: uniq += 1
    last = h
    d = b"".join(bytes(p) for p in enc.encode(f)); e = time.perf_counter()
    grab.append((b - a) * 1000); encs.append((e - b) * 1000); sizes.append(len(d))
el = time.time() - t0
q = lambda v: f"med {statistics.median(v):.1f} p95 {sorted(v)[int(len(v)*.95)]:.1f} max {max(v):.1f}"
print(f"{N} frames em {el:.2f}s = {N/el:.1f} fps de captura | frames DIFERENTES do anterior: {uniq} ({uniq/el:.1f}/s)")
print(f"grab ms: {q(grab)}")
print(f"encode ms: {q(encs)}")
print(f"bytes/frame med {statistics.median(sizes):.0f} -> {statistics.mean(sizes)*60*8/1e6:.1f} Mbps a 60 fps")

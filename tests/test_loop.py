import fractions
import av
import webrtc_server
from aiortc.codecs.h264 import H264PayloadDescriptor, H264Decoder

enc = webrtc_server.NvencH264Encoder()
dec = H264Decoder()

ok = 0
fail = 0
for i in range(10):
    f = av.VideoFrame(1280, 720, "bgr0")
    # padrao reconhecivel: nao preto puro
    for p in f.planes:
        p.update(bytes([i * 7 % 256] * p.buffer_size))
    f.pts = i
    f.time_base = fractions.Fraction(1, 30)
    pkgs, ts = enc.encode(f, i == 0)
    for pk in pkgs:
        obj, data = H264PayloadDescriptor.parse(pk)
        try:
            frames = dec.decode(type("JF", (), {"data": data, "timestamp": ts})())
            ok += 1
            print("payload", len(pk), "nal", pk[0] & 0x1F, "-> frames:", len(frames))
        except Exception as e:
            fail += 1
            print("payload", len(pk), "nal", pk[0] & 0x1F, "-> DECODE FALHOU:", e)
print("ok:", ok, "fail:", fail)

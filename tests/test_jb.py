import fractions
import av
import webrtc_server
from aiortc.codecs.h264 import H264PayloadDescriptor, H264Decoder
from aiortc.jitterbuffer import JitterBuffer

enc = webrtc_server.NvencH264Encoder()
dec = H264Decoder()
jb = JitterBuffer(capacity=128, is_video=True)

seq = 0
ok = 0
fail = 0
partial = 0

for i in range(60):
    f = av.VideoFrame(1280, 720, "bgr0")
    for p in f.planes:
        p.update(bytes([(i * 37 + 11) % 256] * p.buffer_size))
    f.pts = i
    f.time_base = fractions.Fraction(1, 30)

    pkgs, ts = enc.encode(f, i == 0)
    nals = 0
    for pk in pkgs:
        nals += 1
        obj, data = H264PayloadDescriptor.parse(pk)

        class P:
            pass
        p = P()
        p.sequence_number = seq
        p.timestamp = ts
        p._data = data
        seq += 1

        pli, frame = jb.add(p)
        if frame is not None:
            # frame completo? comeca com start code
            starts = frame.data[:4] == b"\x00\x00\x00\x01"
            if not starts:
                partial += 1
            try:
                frames = dec.decode(frame)
                if frames:
                    ok += 1
                else:
                    fail += 1
            except Exception as e:
                fail += 1
            if i < 12 or not starts:
                print("frame", i, "nals", nals, "pacotes", len(pkgs),
                      "| jitter frame", len(frame.data), "bytes",
                      "| start code" if starts else "| SEM start code",
                      "| decode:", "ok" if frames else "FALHOU")

print("decodados:", ok, "falhos:", fail, "parciais:", partial)

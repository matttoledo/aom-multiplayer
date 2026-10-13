import fractions
import av
import webrtc_server
from aiortc.codecs.h264 import H264PayloadDescriptor, H264Decoder
from aiortc.jitterbuffer import JitterBuffer

enc = webrtc_server.NvencH264Encoder()
dec = H264Decoder()
jb = JitterBuffer(capacity=128, is_video=True)

c = av.open(":2.0", format="x11grab", options={"framerate": "30", "video_size": "1280x720"})
s = c.streams.video[0]

seq = 0
ok = 0
fail = 0
partial = 0
maxpk = 0

for i, f in enumerate(c.decode(s)):
    if i >= 60:
        break
    pkgs, ts = enc.encode(f, i == 0)
    maxpk = max(maxpk, len(pkgs))
    for pk in pkgs:
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
            starts = frame.data[:4] == b"\x00\x00\x00\x01"
            if not starts:
                partial += 1
            try:
                fr = dec.decode(frame)
                if fr:
                    ok += 1
                else:
                    fail += 1
            except Exception as e:
                fail += 1
            if i < 10 or not starts:
                print("frame", i, "pacotes", len(pkgs), "ts", ts,
                      "| jitter", len(frame.data), "B",
                      "| start code" if starts else "| SEM start code",
                      "| decode:", "ok" if fr else "FALHOU")

print("max pacotes por frame:", maxpk, "| decodados:", ok, "falhos:", fail, "parciais:", partial)

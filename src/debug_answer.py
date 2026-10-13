import gi, time
gi.require_version("Gst", "1.0")
gi.require_version("GstSdp", "1.0")
gi.require_version("GstWebRTC", "1.0")
from gi.repository import Gst, GstSdp, GstWebRTC, GLib
Gst.init(None)

OFFER = (
    "v=0\r\n"
    "o=- 0 0 IN IP4 127.0.0.1\r\n"
    "s=-\r\n"
    "t=0 0\r\n"
    "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
    "c=IN IP4 0.0.0.0\r\n"
    "a=mid:0\r\n"
    "a=setup:actpass\r\n"
    "a=recvonly\r\n"
    "a=rtpmap:96 H264/90000\r\n"
    "a=fmtp:96 level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42001f\r\n"
    "a=ice-ufrag:testufrag\r\n"
    "a=ice-pwd:testpwd\r\n"
)

w = Gst.ElementFactory.make("webrtcbin", "w")
p = Gst.parse_bin_from_description(
    "ximagesrc display-name=:2 use-damage=false startx=0 starty=0 endx=1279 endy=711 "
    "! videorate ! video/x-raw,framerate=30/1 ! videoconvert ! nvh264enc rc-mode=cbr bitrate=3000 "
    "gop-size=30 bframes=0 ! h264parse ! rtph264pay config-interval=1 name=pay0", True)
p.add(w)

def on_pad(bin_el, pad):
    print("pad-added:", pad.get_direction(), flush=True)
    if pad.get_direction() == Gst.PadDirection.SINK:
        pay = bin_el.get_by_name("pay0")
        if pay:
            print("link:", pay.get_static_pad("src").link(pad), flush=True)

p.connect("pad-added", on_pad)
p.set_state(Gst.State.PLAYING)

loop = GLib.MainLoop()
def run():
    res, msg = GstSdp.SDPMessage.new_from_text(OFFER)
    print("parse:", res, flush=True)
    desc = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.OFFER, msg)
    pr = Gst.Promise.new()
    w.emit("set-remote-description", desc, pr)
    time.sleep(1)
    r = pr.get_reply()
    print("set-remote reply:", r.to_string() if r else None, flush=True)
    tr = w.emit("get-transceivers")
    print("transceivers:", tr, flush=True)
    pr2 = Gst.Promise.new()
    w.emit("create-answer", None, pr2)
    time.sleep(1)
    r2 = pr2.get_reply()
    print("answer reply:", r2.to_string() if r2 else None, flush=True)
    if r2:
        d = r2.get_value("sdp")
        print("desc:", d, flush=True)
        if d is not None:
            print("sdp text:", d.sdp.as_text()[:200], flush=True)
    loop.quit()

threading = __import__("threading")
threading.Thread(target=run, daemon=True).start()
loop.run()

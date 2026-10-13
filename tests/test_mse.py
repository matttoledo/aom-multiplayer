import asyncio, json, struct, time, websockets

async def main():
    async with websockets.connect("ws://127.0.0.1:8080") as ws:
        await ws.send(json.dumps({"t": "hello", "decoder": "mse"}))
        m = json.loads(await ws.recv())
        print("hello-ok:", m)
        t0 = time.time()
        init = None
        frags = 0
        bytes_total = 0
        delays = []
        while time.time() - t0 < 10:
            try: msg = await asyncio.wait_for(ws.recv(), timeout=2)
            except asyncio.TimeoutError: break
            if isinstance(msg, str): continue
            kind = msg[0]
            ts = struct.unpack("<d", msg[1:9])[0]
            payload = msg[9:]
            if kind == 0:
                init = payload
                print("init segment:", len(payload), "bytes", payload[4:8])
            else:
                frags += 1
                bytes_total += len(payload)
                delays.append(time.time() - ts)
        print(f"fragmentos: {frags} ({frags/10:.1f}/s), {bytes_total/10:.0f} bytes/s")
        if delays:
            print(f"delay de pipeline: media {1000*sum(delays)/len(delays):.1f} ms, max {1000*max(delays):.1f} ms")

asyncio.run(main())

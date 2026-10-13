import asyncio, json, struct, time, websockets

async def main():
    async with websockets.connect("ws://127.0.0.1:8080") as ws:
        # warmup
        t_end = time.time() + 3
        while time.time() < t_end:
            try: await asyncio.wait_for(ws.recv(), timeout=1)
            except asyncio.TimeoutError: break
        t0 = time.time()
        units = 0; keys = 0; bytes_total = 0
        delays = []
        while time.time() - t0 < 10:
            try: msg = await asyncio.wait_for(ws.recv(), timeout=2)
            except asyncio.TimeoutError: break
            if isinstance(msg, str): continue
            units += 1
            keys += 1 if msg[0] == 1 else 0
            bytes_total += len(msg) - 9
            delays.append(time.time() - struct.unpack("<d", msg[1:9])[0])
        print(f"10s: {units} access units ({units/10:.1f}/s), {keys} keyframes, {bytes_total/10:.0f} bytes/s")
        print(f"delay de pipeline: media {1000*sum(delays)/len(delays):.1f} ms, max {1000*max(delays):.1f} ms")

asyncio.run(main())

#!/usr/bin/env python3
"""Cliente WebSocket minimo (sem dependencias) para provar que o servidor entrega
frames binarios a um cliente remoto, a partir do Mac."""
import base64
import os
import socket
import struct
import time

HOST, PORT = "192.168.15.17", 8080

key = base64.b64encode(os.urandom(16)).decode()
req = (
    "GET / HTTP/1.1\r\n"
    f"Host: {HOST}:{PORT}\r\n"
    "Upgrade: websocket\r\n"
    "Connection: Upgrade\r\n"
    f"Sec-WebSocket-Key: {key}\r\n"
    "Sec-WebSocket-Version: 13\r\n\r\n"
).encode()

s = socket.create_connection((HOST, PORT), timeout=10)
s.sendall(req)

# ler o header de resposta
buf = b""
while b"\r\n\r\n" not in buf:
    d = s.recv(4096)
    if not d:
        break
    buf += d
print("resposta do handshake:", buf.split(b"\r\n")[0].decode())

s.settimeout(5)
t0 = time.time()
frames = 0
opcodes = {}
bytes_total = 0
first_delay = None

def readexactly(n):
    b = b""
    while len(b) < n:
        d = s.recv(n - len(b))
        if not d:
            raise EOFError
        b += d
    return b

try:
    while time.time() - t0 < 12:
        hdr = readexactly(2)
        op = hdr[0] & 0x0F
        ln = hdr[1] & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", readexactly(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", readexactly(8))[0]
        payload = readexactly(ln) if ln else b""
        opcodes[op] = opcodes.get(op, 0) + 1
        if op == 2:  # frame binario
            frames += 1
            bytes_total += len(payload)
            if first_delay is None and len(payload) >= 9:
                ts = struct.unpack("<d", payload[1:9])[0]
                first_delay = round((time.time() - ts) * 1000)
except (EOFError, socket.timeout):
    pass

print(f"frames binarios: {frames} | bytes: {bytes_total} | opcodes: {opcodes}")
print(f"delay de rede+pipeline: {first_delay} ms")
s.close()

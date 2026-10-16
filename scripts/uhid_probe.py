#!/usr/bin/env python3
"""Teste: o PCSX2 2.x (SDL3) ve um pad criado via /dev/uhid, mas NAO ve um pad criado
via /dev/uinput.

Medido no asus: SDL2 e SDL3 enumeram 0 joysticks com um pad uinput presente, mesmo com
udev marcando o device como ID_INPUT_JOYSTICK=1. O backend de joystick do SDL3 e HIDAPI,
que exige um report descriptor HID; um device uinput nao tem. Este teste cria um device
HID pelo uhid e confirma se o SDL3 o enumera.

Uhid NAO e ioctl: e um fd de escrita, com `struct uhid_event` = u32 type + union.
UHID_CREATE2 = 11.

/dev/uhid e crw------- root root, entao este teste precisa de root.

Uso: sudo python3 uhid_probe.py
"""
import ctypes
import os
import struct
import time

HID_MAX_RD = 4096
UHID_CREATE2 = 11
UHID_DESTROY = 1
UHID_INPUT2 = 12

CREATE_FMT = "128s64s64sHHIIIi" + str(HID_MAX_RD) + "s"

RD = bytes([
    0x05, 0x01, 0x09, 0x05, 0xA1, 0x01,          # Usage Page (GD), Usage (Joystick), Collection
    0x05, 0x09, 0x19, 0x01, 0x29, 0x0A,          # Usage Page (Button), Usage 1..10
    0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x0A,
    0x81, 0x02,                                   # Input, Variable
    0x75, 0x06, 0x95, 0x01, 0x81, 0x03,           # padding
    0x05, 0x01, 0x09, 0x30, 0x09, 0x31,           # Usage X, Y
    0x15, 0x00, 0x25, 0xFF, 0x75, 0x08, 0x95, 0x02,
    0x81, 0x02,
    0xC0,                                         # End Collection
])

SDL_LIB = "/mnt/windows/pcsx2/squashfs-root/usr/lib/libSDL3.so.0"


def create2(name):
    return struct.pack("I" + CREATE_FMT, UHID_CREATE2,
                       name.encode(), b"", b"",
                       len(RD), 0x19, 0x045E, 0x00E0, 1, 0,
                       RD + b"\x00" * (HID_MAX_RD - len(RD)))


def main():
    try:
        fd = os.open("/dev/uhid", os.O_WRONLY)
    except OSError as e:
        print("FALHOU: abrir /dev/uhid:", e)
        return 1

    try:
        os.write(fd, create2("aom-hid-pad0"))
    except OSError as e:
        print("FALHOU: UHID_CREATE2:", e)
        return 1

    time.sleep(3)

    lib = ctypes.CDLL(SDL_LIB)
    lib.SDL_GetJoystickNameForID.restype = ctypes.c_char_p
    if lib.SDL_Init(0x20) != 0:
        print("SDL_Init falhou")
        os.close(fd)
        return 1
    cnt = ctypes.c_int(0)
    ids = lib.SDL_GetJoysticks(ctypes.byref(cnt))
    print("MEDIDO: SDL3 enumera", cnt.value, "joysticks com o device HID")
    if ids:
        for i in range(cnt.value):
            jid = ctypes.cast(ids, ctypes.POINTER(ctypes.c_int32))[i]
            print("  ", ctypes.string_at(lib.SDL_GetJoystickNameForID(jid)).decode())

    os.write(fd, struct.pack("I", UHID_DESTROY))
    os.close(fd)
    lib.SDL_Quit()
    return 0 if cnt.value > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

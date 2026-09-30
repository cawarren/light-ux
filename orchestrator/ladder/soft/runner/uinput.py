# Vendored from tools/a1-check/a1lib/uinput.py at commit 6ca4bc9 (A1 on-device check).
# Keep in sync by hand; local changes are marked 'ladder:'.
"""Linux virtual keyboard through /dev/uinput, standard library only.

Path exercised: uinput -> evdev -> libinput -> KWin -> wl_keyboard.key -> Chrome
(phase-a §3.1, the "python-evdev UInput" row, without python-evdev).

Kernel ABI used (include/uapi/linux/uinput.h, input.h):
  UI_SET_EVBIT  = _IOW('U', 100, int)
  UI_SET_KEYBIT = _IOW('U', 101, int)
  UI_DEV_SETUP  = _IOW('U', 3, struct uinput_setup)   (kernel >= 4.5)
  UI_DEV_CREATE = _IO('U', 1)
  UI_DEV_DESTROY= _IO('U', 2)
  UI_GET_SYSNAME(len) = _IOC(_IOC_READ, 'U', 44, len)
  struct input_id      { __u16 bustype, vendor, product, version; }
  struct uinput_setup  { struct input_id id; char name[80]; __u32 ff_effects_max; }
  struct input_event   { struct timeval time; __u16 type; __u16 code; __s32 value; }
The input_event time field is ignored on write: the kernel stamps events itself.
"""
from __future__ import annotations

import os
import struct

# --- ioctl number encoding (asm-generic/ioctl.h; same on x86_64 and arm64) ---
_IOC_NRBITS, _IOC_TYPEBITS, _IOC_SIZEBITS = 8, 8, 14
_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS
_IOC_NONE, _IOC_WRITE, _IOC_READ = 0, 1, 2


def _IOC(direction, typ, nr, size):
    return ((direction << _IOC_DIRSHIFT) | (ord(typ) << _IOC_TYPESHIFT)
            | (nr << _IOC_NRSHIFT) | (size << _IOC_SIZESHIFT))


def _IO(typ, nr):
    return _IOC(_IOC_NONE, typ, nr, 0)


def _IOW(typ, nr, size):
    return _IOC(_IOC_WRITE, typ, nr, size)


INPUT_ID_FMT = "HHHH"                       # bustype, vendor, product, version
UINPUT_SETUP_FMT = "=" + INPUT_ID_FMT + "80sI"  # no padding: 8 + 80 + 4 = 92
UINPUT_SETUP_SIZE = struct.calcsize(UINPUT_SETUP_FMT)
# struct timeval is two native longs on 64-bit Linux; 'l' follows the platform.
INPUT_EVENT_FMT = "llHHi"
INPUT_EVENT_SIZE = struct.calcsize(INPUT_EVENT_FMT)

UI_SET_EVBIT = _IOW("U", 100, struct.calcsize("i"))
UI_SET_KEYBIT = _IOW("U", 101, struct.calcsize("i"))
UI_DEV_SETUP = _IOW("U", 3, UINPUT_SETUP_SIZE)
UI_DEV_CREATE = _IO("U", 1)
UI_DEV_DESTROY = _IO("U", 2)


def UI_GET_SYSNAME(length):
    return _IOC(_IOC_READ, "U", 44, length)


EV_SYN, EV_KEY = 0x00, 0x01
SYN_REPORT = 0
BUS_VIRTUAL = 0x06

DEVICE_PATH = "/dev/uinput"
DEVICE_NAME = b"ladder-kbd"  # ladder: was ladder-a1-kbd


def pack_setup(name: bytes = DEVICE_NAME, vendor=0x1d6b, product=0x0a01, version=1) -> bytes:
    return struct.pack(UINPUT_SETUP_FMT, BUS_VIRTUAL, vendor, product, version,
                       name[:79], 0)


def pack_event(etype: int, code: int, value: int) -> bytes:
    return struct.pack(INPUT_EVENT_FMT, 0, 0, etype, code, value)


def key_bytes(code: int, down: bool) -> bytes:
    """EV_KEY + SYN_REPORT in one buffer, so one write() delivers the report."""
    return pack_event(EV_KEY, code, 1 if down else 0) + pack_event(EV_SYN, SYN_REPORT, 0)


def access_problem() -> str | None:
    """None if /dev/uinput is usable by this process, else a short reason."""
    if not os.path.exists(DEVICE_PATH):
        return "missing"          # module not loaded
    if not os.access(DEVICE_PATH, os.W_OK):
        return "not_writable"
    return None


def fix_instructions(problem: str) -> str:
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or "$USER"
    lines = []
    if problem == "missing":
        lines += ["/dev/uinput does not exist. Load the module (one-time, until reboot):",
                  "    sudo modprobe uinput"]
    lines += [
        "/dev/uinput is not writable by you. Quickest temporary fix (resets on reboot):",
        f"    sudo setfacl -m u:{user}:rw /dev/uinput",
        "Or rerun with --sudo-injector: only the small injector process runs",
        "under sudo (it asks for your password once); Chrome and the rest stay unprivileged.",
        "Permanent alternative (udev rule, then log out/in):",
        "    echo 'KERNEL==\"uinput\", SUBSYSTEM==\"misc\", TAG+=\"uaccess\", OPTIONS+=\"static_node=uinput\"' \\",
        "      | sudo tee /etc/udev/rules.d/60-ladder-uinput.rules",
        "    sudo udevadm control --reload && sudo udevadm trigger",
    ]
    return "\n".join(lines)


class UInputKeyboard:
    """A virtual keyboard. Use as a context manager."""

    def __init__(self, keycodes):
        self.keycodes = sorted(set(keycodes))
        self.fd = None
        self.sysname = None

    def open(self):
        import fcntl
        self.fd = os.open(DEVICE_PATH, os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_SYN)
        # udev's input_id builtin tags a device ID_INPUT_KEYBOARD only if it has
        # every key 1..31 (KEY_ESC..KEY_S). Without that tag libinput/KWin treat
        # it as a plain "keys" device and may not route it as a keyboard. So we
        # advertise 1..127 (a normal keyboard), but only ever press letters.
        for code in sorted(set(range(1, 128)) | set(self.keycodes)):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
        fcntl.ioctl(self.fd, UI_DEV_SETUP, pack_setup())
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        try:
            buf = bytearray(64)
            fcntl.ioctl(self.fd, UI_GET_SYSNAME(len(buf)), buf, True)
            self.sysname = bytes(buf).split(b"\0", 1)[0].decode()
        except OSError:
            self.sysname = None
        return self

    def key(self, code: int, down: bool) -> None:
        data = key_bytes(code, down)
        n = os.write(self.fd, data)
        if n != len(data):
            raise OSError(f"short write to uinput: {n}/{len(data)}")

    def close(self):
        if self.fd is not None:
            import fcntl
            try:
                fcntl.ioctl(self.fd, UI_DEV_DESTROY)
            finally:
                os.close(self.fd)
                self.fd = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

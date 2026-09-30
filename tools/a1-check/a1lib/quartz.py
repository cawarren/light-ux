"""macOS keyboard injection through Quartz (CGEventPost), ctypes only.

Path exercised: CGEventPost(kCGHIDEventTap) -> WindowServer -> NSApplication
-> Chrome's NSEvent handling. This is the lowest user-space tap point; it skips
the keyboard hardware/HID driver only.

Everything platform-specific is bound lazily so this module imports (and its
signature table can be unit-tested) on Linux.
"""
from __future__ import annotations

import ctypes
import ctypes.util

CG_PATH = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
CF_PATH = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
AS_PATH = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
LIBSYSTEM = "/usr/lib/libSystem.B.dylib"
OBJC = "/usr/lib/libobjc.A.dylib"
APPKIT = "/System/Library/Frameworks/AppKit.framework/AppKit"

kCGEventSourceStateHIDSystemState = 1
kCGHIDEventTap = 0
NSApplicationActivateIgnoringOtherApps = 1 << 1


class mach_timebase_info_data_t(ctypes.Structure):
    _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]


# name -> (library key, restype, argtypes). Kept as data so tests can check it.
SIGNATURES = {
    "CGEventSourceCreate": ("cg", ctypes.c_void_p, [ctypes.c_int32]),
    "CGEventCreateKeyboardEvent": ("cg", ctypes.c_void_p,
                                   [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]),
    "CGEventSetFlags": ("cg", None, [ctypes.c_void_p, ctypes.c_uint64]),
    "CGEventPost": ("cg", None, [ctypes.c_uint32, ctypes.c_void_p]),
    "CGEventGetTimestamp": ("cg", ctypes.c_uint64, [ctypes.c_void_p]),
    "CGPreflightPostEventAccess": ("cg", ctypes.c_bool, []),
    "CGRequestPostEventAccess": ("cg", ctypes.c_bool, []),
    "CFRelease": ("cf", None, [ctypes.c_void_p]),
    "AXIsProcessTrusted": ("as", ctypes.c_uint8, []),   # Boolean = unsigned char
    "mach_absolute_time": ("sys", ctypes.c_uint64, []),
    "mach_timebase_info": ("sys", ctypes.c_int,
                           [ctypes.POINTER(mach_timebase_info_data_t)]),
}

_fns = None


def bind(libs: dict) -> dict:
    """Attach restype/argtypes to each function from the given libraries."""
    out = {}
    for name, (lib, restype, argtypes) in SIGNATURES.items():
        fn = getattr(libs[lib], name, None)
        if fn is None:
            continue  # e.g. CGPreflightPostEventAccess on very old macOS
        fn.restype = restype
        fn.argtypes = argtypes
        out[name] = fn
    return out


def fns() -> dict:
    global _fns
    if _fns is None:
        libs = {"cg": ctypes.CDLL(CG_PATH), "cf": ctypes.CDLL(CF_PATH),
                "as": ctypes.CDLL(AS_PATH), "sys": ctypes.CDLL(LIBSYSTEM)}
        _fns = bind(libs)
    return _fns


def mach_absolute_time() -> int:
    return fns()["mach_absolute_time"]()


def mach_timebase() -> tuple:
    info = mach_timebase_info_data_t()
    fns()["mach_timebase_info"](ctypes.byref(info))
    return info.numer, info.denom


def post_access() -> dict:
    """Whether this process may post events. Posting needs the Accessibility
    permission for the app that runs Python (Terminal, iTerm, VS Code...)."""
    f = fns()
    res = {"ax_trusted": bool(f["AXIsProcessTrusted"]())}
    if "CGPreflightPostEventAccess" in f:
        res["post_event_access"] = bool(f["CGPreflightPostEventAccess"]())
    else:
        res["post_event_access"] = res["ax_trusted"]
    return res


def request_access() -> None:
    """Ask macOS to show its permission prompt (adds the terminal to the list)."""
    f = fns()
    if "CGRequestPostEventAccess" in f:
        f["CGRequestPostEventAccess"]()


FIX_INSTRUCTIONS = """\
macOS needs to allow your terminal app to send key presses (one-time):
  1. Open System Settings > Privacy & Security > Accessibility.
  2. Turn ON the app you are running this script from (Terminal, iTerm,
     Visual Studio Code, ...). If it is not listed, press "+" and add it
     from /Applications (Terminal is in /Applications/Utilities).
  3. Quit that terminal app completely (Cmd-Q) and reopen it; permission is
     only picked up by a fresh process. Then rerun the same command.
(The script has just asked macOS to show this prompt, so a dialog may be open.)
You can remove the permission again after the check."""


class QuartzKeyboard:
    def __init__(self):
        f = fns()
        self.f = f
        self.src = f["CGEventSourceCreate"](kCGEventSourceStateHIDSystemState)

    def make(self, vk: int, down: bool):
        ev = self.f["CGEventCreateKeyboardEvent"](self.src, vk, down)
        if not ev:
            raise OSError("CGEventCreateKeyboardEvent returned NULL")
        self.f["CGEventSetFlags"](ev, 0)  # no modifiers, whatever the user holds
        return ev

    def timestamp(self, ev) -> int:
        return int(self.f["CGEventGetTimestamp"](ev))

    def post(self, ev) -> None:
        self.f["CGEventPost"](kCGHIDEventTap, ev)

    def release(self, ev) -> None:
        self.f["CFRelease"](ev)

    def close(self):
        if self.src:
            self.f["CFRelease"](self.src)
            self.src = None


def activate_pid(pid: int) -> bool:
    """Bring the app with this pid to the front (NSRunningApplication). Best effort:
    macOS 14+ may refuse 'cooperative activation'; the owner then clicks."""
    objc = ctypes.CDLL(OBJC)
    ctypes.CDLL(APPKIT)
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    send_addr = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
    # objc_msgSend must be called through a prototype matching each method (arm64 ABI).
    by_pid = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.c_int32)(send_addr)
    activate = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p,
                                ctypes.c_ulong)(send_addr)
    cls = objc.objc_getClass(b"NSRunningApplication")
    app = by_pid(cls, objc.sel_registerName(b"runningApplicationWithProcessIdentifier:"), pid)
    if not app:
        return False
    return bool(activate(app, objc.sel_registerName(b"activateWithOptions:"),
                         NSApplicationActivateIgnoringOtherApps))

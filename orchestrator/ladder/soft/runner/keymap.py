"""Keys the harness injects, with per-platform codes.

Extended from tools/a1-check/a1lib/keymap.py (letters only there) with digits, space and
Backspace: the rig-typeable subset of report 05 (lower-case ASCII, digits, space; US layout)
plus the Backspace that clears a query (phase-a §4.5).
"""

# Linux evdev codes (linux/input-event-codes.h)
EVDEV = {
    "q": 16, "w": 17, "e": 18, "r": 19, "t": 20, "y": 21, "u": 22, "i": 23,
    "o": 24, "p": 25, "a": 30, "s": 31, "d": 32, "f": 33, "g": 34, "h": 35,
    "j": 36, "k": 37, "l": 38, "z": 44, "x": 45, "c": 46, "v": 47, "b": 48,
    "n": 49, "m": 50,
    "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10, "0": 11,
    " ": 57,             # KEY_SPACE
    "Backspace": 14,     # KEY_BACKSPACE
}

# macOS virtual keycodes (HIToolbox/Events.h, kVK_ANSI_* / kVK_Space / kVK_Delete)
MAC_VK = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8,
    "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, "n": 45,
    "m": 46,
    "1": 18, "2": 19, "3": 20, "4": 21, "5": 23, "6": 22, "7": 26, "8": 28, "9": 25, "0": 29,
    " ": 49,             # kVK_Space
    "Backspace": 51,     # kVK_Delete (the Backspace key)
}

TYPEABLE = set("abcdefghijklmnopqrstuvwxyz0123456789 ")
BACKSPACE = "Backspace"


def dom_key(k: str) -> str:
    """KeyboardEvent.key the page should see for injected key k."""
    return k


def dom_code(k: str) -> str:
    """KeyboardEvent.code (physical position, layout-independent)."""
    if k == BACKSPACE:
        return "Backspace"
    if k == " ":
        return "Space"
    if k.isdigit():
        return "Digit" + k
    return "Key" + k.upper()


def windows_vk(k: str) -> int:
    if k == BACKSPACE:
        return 8
    if k == " ":
        return 32
    return ord(k.upper())


def cdp_params(k: str, down: bool) -> dict:
    """CDP Input.dispatchKeyEvent params (simulate mode only)."""
    p = {"key": k, "code": dom_code(k), "windowsVirtualKeyCode": windows_vk(k),
         "nativeVirtualKeyCode": windows_vk(k)}
    if k == BACKSPACE:
        p["type"] = "rawKeyDown" if down else "keyUp"
    else:
        p["type"] = "keyDown" if down else "keyUp"
        if down:
            p["text"] = k
            p["unmodifiedText"] = k
    return p


def apply(value: str, k: str) -> str:
    """The input value after pressing k (caret at the end, as in every harness trial)."""
    return value[:-1] if k == BACKSPACE else value + k

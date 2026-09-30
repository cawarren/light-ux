"""Letter keys used by the check, with their per-platform codes.

Only unmodified lower-case letters are injected: if focus is ever lost, stray
letters are the least harmful thing to type into another window (no Enter,
no modifiers, no Backspace).
"""

# Linux evdev codes (linux/input-event-codes.h)
EVDEV = {
    "q": 16, "w": 17, "e": 18, "r": 19, "t": 20, "y": 21, "u": 22, "i": 23,
    "o": 24, "p": 25, "a": 30, "s": 31, "d": 32, "f": 33, "g": 34, "h": 35,
    "j": 36, "k": 37, "l": 38, "z": 44, "x": 45, "c": 46, "v": 47, "b": 48,
    "n": 49, "m": 50,
}

# macOS virtual keycodes (HIToolbox/Events.h, kVK_ANSI_*)
MAC_VK = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8,
    "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, "n": 45,
    "m": 46,
}

LETTERS = sorted(EVDEV)


def dom_code(letter: str) -> str:
    """KeyboardEvent.code is physical-position based, so layout-independent."""
    return "Key" + letter.upper()

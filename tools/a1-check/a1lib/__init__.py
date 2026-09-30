"""Phase A "A1" on-device check. Standard library only.

Modules:
  clock        - the one host clock used by server, injector and analysis
  keymap       - letter -> Linux evdev code / macOS virtual keycode / DOM code
  uinput       - Linux /dev/uinput virtual keyboard (fcntl/ioctl/struct)
  quartz       - macOS CGEventPost keyboard via ctypes
  injector     - injector child process (runs a seeded schedule, logs timestamps)
  cdp          - minimal Chrome DevTools Protocol client over a stdlib websocket
  server       - local HTTP server (COOP/COEP) with page command channel and clock sync
  chrome       - find / launch / stop Chrome with a fresh profile
  envinfo      - host environment capture (OS, session type, power, refresh hints)
  analysis     - turns the raw report into PASS/FAIL/UNKNOWN rows
"""

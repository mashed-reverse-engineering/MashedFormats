#!/usr/bin/env python3
"""Settings and save files the game writes at run time: videocfg.bin, contcfg<n>.bin, gamesave.bin.

All three are raw memory dumps of global structs (`fwrite(&global, 1, size, file)`), so every class keeps the
original bytes and exposes the known fields as properties; `build()` returns the (possibly edited) bytes. Layouts
were read from MFL.exe (addresses in the docstrings); see FORMATS.md.

  mashed_config.py dump FILE        print the fields of a videocfg.bin / contcfg<n>.bin / gamesave.bin
Standard library only."""
import struct, sys
from pathlib import Path

CFG_SIZE = 0x200
SAVE_SIZE = 0x42334
SAVE_MAGIC = 0xDEADBEEF


def _field(fmt, off, doc=""):
    s = struct.Struct("<" + fmt)

    def get(self):
        v = s.unpack_from(self.raw, off)
        return v[0] if len(v) == 1 else v

    def put(self, v):
        s.pack_into(self.raw, off, *(v if isinstance(v, tuple) else (v,)))
    return property(get, put, doc=doc)


def _text(off, size, doc=""):
    def get(self):
        return bytes(self.raw[off:off + size]).split(b"\0")[0].decode("latin-1")

    def put(self, v):
        b = v.encode("latin-1")
        if len(b) >= size:
            raise ValueError("text too long")
        self.raw[off:off + size] = b + b"\0" * (size - len(b))
    return property(get, put, doc=doc)


class _Dump:
    SIZE = CFG_SIZE

    def __init__(self, raw):
        if len(raw) != self.SIZE:
            raise ValueError(f"{type(self).__name__}: expected {self.SIZE:#x} bytes, got {len(raw):#x}")
        self.raw = bytearray(raw)

    @classmethod
    def parse(cls, data):
        return cls(data)

    def build(self):
        return bytes(self.raw)


class VideoCfg(_Dump):
    """videocfg.bin: 0x200 bytes = the global at 0x7712F8 (load FUN_004a85c0, save FUN_004a8620; the settings
    dialog is FUN_004a8bd0 / the apply code FUN_004a8b10). Unused bytes are zero."""
    version = _field("I", 0x000, "5, written on every save (not checked when loading)")
    subsystem = _text(0x004, 80, "name of the Direct3D adapter (RwSubSystemInfo)")
    mode_name = _text(0x104, 64, "text of the chosen video mode, e.g. '800 x 600 x 32'")
    subsystem_index = _field("I", 0x184)
    mode_index = _field("I", 0x188, "index into the adapter's mode list (RwEngineSetVideoMode)")
    width = _field("I", 0x18C)
    height = _field("I", 0x190)
    depth = _field("I", 0x194)
    fullscreen = _field("I", 0x198, "RwVideoMode flags & 1 (rwVIDEOMODEEXCLUSIVE)")
    no_multitexture = _field("I", 0x19C, "Performance Settings > Multi-Texturing is checked when this is 0")
    no_dynamic_shadows = _field("I", 0x1A0, "Dynamic Shadows is checked when this is 0")
    trilinear = _field("I", 0x1A4, "Trilinear Filtering is checked when this is not 0")
    no_env_effects = _field("I", 0x1A8, "Environmental Effects is checked when this is 0")


ACTIONS = ("accelerate", "brake", "fire", "drop", "menu_select", "menu_back", "pause", "menu_next", "insult",
           "left", "right", "up", "down")
DEVICE_NONE, DEVICE_GAMEPAD, DEVICE_KEYBOARD = 0, 1, 2


class ControllerCfg(_Dump):
    """contcfg<n>.bin: 0x200 bytes = controller n of the array at 0x7E7740 (stride 0x200; load FUN_004a6e20, save
    FUN_004a6ea0, defaults FUN_004a8180, dialog FUN_004a77d0). The game keeps a loaded file only if `version` is 6
    and `name` equals the name of the device it detects now, else the defaults stay."""
    version = _field("I", 0x000, "6")
    name = _text(0x004, 0x104, "device name (the joystick's product name, or the localised word 'Keyboard')")
    device = _field("I", 0x13C, "0 no controller, 1 gamepad/joystick, 2 keyboard")
    joystick = _field("I", 0x140, "joystick index (device 1)")

    @property
    def bindings(self):
        """13 u32: button number (gamepad) or DirectInput scancode (keyboard) per action in ACTIONS. Slots 4, 5 and 7
        have no entry in the dialog (copies of accelerate / brake / fire used for menus); the last four are the
        directions (analog stick axes on a gamepad, arrow keys on the keyboard)."""
        return dict(zip(ACTIONS, struct.unpack_from("<13I", self.raw, 0x108)))

    def set_binding(self, action, value):
        struct.pack_into("<I", self.raw, 0x108 + 4 * ACTIONS.index(action), value)


KEYBOARD_DEFAULT = (0x1F, 0x2D, 0x1E, 0x20, 0x1C, 0x01, 0x01, 0x1E, 0x2E, 0xCB, 0xCD, 0xC8, 0xD0)
GAMEPAD_DEFAULT = (0, 1, 2, 3, 0, 1, 4, 2, 5)


def default_controller(device, name="", joystick=0):
    """The config FUN_004a8180 builds before it loads the file (gamepad: 9 buttons, directions come from the stick)."""
    c = ControllerCfg(bytes(CFG_SIZE))
    c.version, c.device, c.name = 6, device, name
    if device == DEVICE_GAMEPAD:
        c.joystick = joystick
        struct.pack_into("<9I", c.raw, 0x108, *GAMEPAD_DEFAULT)
    elif device == DEVICE_KEYBOARD:
        struct.pack_into("<13I", c.raw, 0x108, *KEYBOARD_DEFAULT)
    return c


def _array(fmt, off, count):
    s = struct.Struct("<" + fmt)

    def get(self):
        return list(struct.unpack_from(f"<{count}{fmt}", self.raw, self.STATE + off))

    def put(self, v):
        if len(v) != count:
            raise ValueError(f"need {count} values")
        struct.pack_into(f"<{count}{fmt}", self.raw, self.STATE + off, *v)
    return property(get, put)


PROGRESS_ROWS, PROGRESS_COLS = 15, 18
PROGRESS_LOCKED, PROGRESS_OPEN, PROGRESS_DONE = 0, 1, 2


class SaveGame(_Dump):
    """gamesave.bin: 0x42334 bytes = the global at 0x801B40 (memory-card style state machine FUN_0040b080 ..
    FUN_0040b920; load FUN_00405cf0, save FUN_00405df0, gather FUN_00405d80, apply FUN_00405d20).

      0x00000  u32 0xDEADBEEF
      0x00004  0x4178C bytes: the ghost / replay buffer (`DAT_008d8b08`, "Replay buffer will be %d bytes"), a list of
               REP records (see `Rep` in mashed_formats.py)
      0x41790  0xB64 bytes: the game state block at 0x7EEBC0 (offsets below are relative to it)
      0x422F4  0x40 bytes: rest of the dumped global (untouched by the gather code)
    Game state block:
      0x000  u32 progress[15][18]   (0 locked, 1 open, 2 done; one row per entry of the career menu, one column per
                                     event type; written as 2 by FUN_00435810 when an event is won)
      0x438  i32 records[15][5]     (-1 = empty; 20 bytes per row)
      0x564  600 bytes              not seen used
      0x7BC  u32 minutes[45]  0x870  u32 seconds[45]  0x924  f32 fraction[45]
             best time table, index = FUN_00435f10() * 3 + FUN_00435f20() (the three best entries per course); the
             defaults (FUN_00429680) are 9 min 59.xx s
      0x9D8  u8 enabled[15][18]     (1 = selectable; the 18 columns are the same as in `progress`)
      0xAE8  rest: option sliders (+0xB00 .. +0xB10 = 0x7EF6C0..), flags at +0xB2C..+0xB3C, +0xB54 = mirrored-track
             mode (course id + 1000), +0xB58..+0xB63 one byte per player (copy of 0x7EF718[12])"""
    SIZE = SAVE_SIZE
    BUFFER = 4
    BUFFER_SIZE = 0x4178C
    STATE = 0x41790
    STATE_SIZE = 0xB64
    TRAILER = 0x40   # 0x422F4..0x42334: part of the dumped global, never written by the gather code (zero)

    magic = _field("I", 0)
    progress_raw = _array("I", 0x000, PROGRESS_ROWS * PROGRESS_COLS)
    records_raw = _array("i", 0x438, PROGRESS_ROWS * 5)
    best_minutes = _array("I", 0x7BC, 45)
    best_seconds = _array("I", 0x870, 45)
    best_fraction = _array("f", 0x924, 45)
    enabled_raw = _array("B", 0x9D8, PROGRESS_ROWS * PROGRESS_COLS)
    mirror_mode = property(lambda self: struct.unpack_from("<I", self.raw, self.STATE + 0xB54)[0],
                           lambda self, v: struct.pack_into("<I", self.raw, self.STATE + 0xB54, v))

    def __init__(self, raw):
        super().__init__(raw)
        if self.magic != SAVE_MAGIC:
            raise ValueError("bad magic (expected 0xDEADBEEF)")

    @property
    def progress(self):
        p = self.progress_raw
        return [p[r * PROGRESS_COLS:(r + 1) * PROGRESS_COLS] for r in range(PROGRESS_ROWS)]

    @property
    def replay_buffer(self):
        return bytes(self.raw[self.BUFFER:self.BUFFER + self.BUFFER_SIZE])

    @replay_buffer.setter
    def replay_buffer(self, data):
        if len(data) > self.BUFFER_SIZE:
            raise ValueError("replay buffer too big")
        self.raw[self.BUFFER:self.BUFFER + self.BUFFER_SIZE] = data + bytes(self.BUFFER_SIZE - len(data))

    def best_times(self):
        """[(minutes, seconds, fraction)] x 45, seconds + fraction = the time within the minute."""
        return list(zip(self.best_minutes, self.best_seconds, self.best_fraction))


def new_save(replay=b""):
    """An empty save: magic, an empty replay buffer, a zero state block."""
    s = SaveGame.__new__(SaveGame)
    s.raw = bytearray(SAVE_SIZE)
    struct.pack_into("<I", s.raw, 0, SAVE_MAGIC)
    s.replay_buffer = replay
    return s


def load(path):
    data = Path(path).read_bytes()
    name = Path(path).name.lower()
    if name.startswith("videocfg"):
        return VideoCfg.parse(data)
    if name.startswith("contcfg"):
        return ControllerCfg.parse(data)
    if name.startswith("gamesave"):
        return SaveGame.parse(data)
    raise ValueError("not a videocfg / contcfg / gamesave file")


def summary(o):
    if isinstance(o, VideoCfg):
        return (f"video config: {o.subsystem!r} mode {o.mode_index} ({o.width}x{o.height}x{o.depth}, "
                f"{'fullscreen' if o.fullscreen else 'window'}), multitexture {not o.no_multitexture}, shadows "
                f"{not o.no_dynamic_shadows}, trilinear {bool(o.trilinear)}, env effects {not o.no_env_effects}")
    if isinstance(o, ControllerCfg):
        return f"controller config: {('none', 'gamepad', 'keyboard')[o.device] if o.device < 3 else o.device} {o.name!r} {o.bindings}"
    done = sum(v == PROGRESS_DONE for v in o.progress_raw)
    return f"save game: {done} of {len(o.progress_raw)} progress cells done, mirror mode {o.mirror_mode}"


def main(argv):
    if len(argv) != 3 or argv[1] != "dump":
        print(__doc__)
        return 2
    try:
        print(summary(load(argv[2])))
    except (ValueError, OSError) as e:
        print(f"{argv[2]}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

#!/usr/bin/env python3
"""Generic RenderWare binary stream chunk walker.

  rwchunks.py FILE [maxdepth]       print the chunk tree (type name, size, library version)

A chunk is `u32 type, u32 size, u32 stamp` followed by `size` bytes. Types listed in CONTAINERS hold child
chunks; everything else is a leaf (STRUCT, STRING and plugin data). Standard library only."""
import struct, sys
from pathlib import Path

NAMES = {
    0x01: "STRUCT", 0x02: "STRING", 0x03: "EXTENSION", 0x05: "CAMERA", 0x06: "TEXTURE", 0x07: "MATERIAL", 0x08: "MATLIST",
    0x09: "ATOMICSECT", 0x0A: "PLANESECT", 0x0B: "WORLD", 0x0C: "SPLINE", 0x0D: "MATRIX", 0x0E: "FRAMELIST", 0x0F: "GEOMETRY",
    0x10: "CLUMP", 0x12: "LIGHT", 0x14: "ATOMIC", 0x15: "TEXTURENATIVE", 0x16: "TEXDICTIONARY", 0x17: "ANIMDICT",
    0x18: "IMAGE", 0x19: "SKINANIMATION", 0x1A: "GEOMETRYLIST", 0x1B: "ANIMANIMATION", 0x1C: "HANIMANIMATION",
    0x1F: "RIGHTTORENDER", 0x23: "PITEXDICTIONARY", 0x2B: "UVANIMDICT",
    0x0116: "SKIN", 0x011E: "HANIM", 0x011F: "USERDATA", 0x0120: "MATFX", 0x0126: "UVANIMATION_PLG",
    0x0253F2F3: "PDS", 0x0253F2F6: "ADC", 0x0253F2F8: "UVANIM", 0x0253F2FA: "COLLISION",
    0x0294: "SKYMIPMAP",
    0x050E: "BINMESH", 0x0510: "NATIVEDATA", 0x0511: "VERTEXFMT",
    0x13269901: "GAME_LED", 0x13269902: "GAME_AI", 0x0199: "GAME_FONT",
}
# (0x1B ANIMANIMATION, 0x18 IMAGE and 0x15 TEXTURENATIVE hold raw payloads mixed with chunks or only data)
CONTAINERS = {0x03, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0E, 0x0F, 0x10, 0x14, 0x16, 0x17, 0x1A, 0x2B}


def stamp_version(stamp):
    if stamp & 0xFFFF0000 == 0:
        return f"{stamp >> 8:x}"
    v = ((stamp >> 14) & 0x3FF00) + 0x30000 | ((stamp >> 16) & 0x3F)
    return f"{v >> 16 & 0xF}.{v >> 12 & 0xF}.{v >> 8 & 0xF}.{v & 0xFF}"


class Chunk:
    def __init__(self, type_, size, stamp, offset):
        self.type, self.size, self.stamp, self.offset = type_, size, stamp, offset
        self.children = []

    @property
    def name(self):
        return NAMES.get(self.type, f"0x{self.type:X}")


def parse(data, start=0, end=None, depth=0, max_depth=64):
    """List of Chunk for the chunks between start and end (children are parsed for container types)."""
    end = len(data) if end is None else end
    out, pos = [], start
    while pos + 12 <= end:
        t, s, st = struct.unpack_from("<III", data, pos)
        if pos + 12 + s > end:
            raise ValueError(f"chunk 0x{t:X} at {pos:#x} (size {s}) runs past its parent ({end:#x})")
        c = Chunk(t, s, st, pos)
        if t in CONTAINERS and depth < max_depth and not (t == 0x16 and False):
            c.children = parse(data, pos + 12, pos + 12 + s, depth + 1, max_depth)
        out.append(c)
        pos += 12 + s
    if pos != end:
        raise ValueError(f"{end - pos} trailing bytes at {pos:#x}")
    return out


def dump(chunks, maxdepth=99, indent=0, out=None):
    out = [] if out is None else out
    for c in chunks:
        out.append(f"{'  ' * indent}{c.name} size={c.size} v{stamp_version(c.stamp)} @{c.offset:#x}")
        if indent < maxdepth:
            dump(c.children, maxdepth, indent + 1, out)
    return out


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    data = Path(argv[1]).read_bytes()
    try:
        tree = parse(data)
    except ValueError as e:
        print(f"{argv[1]}: {e}", file=sys.stderr)
        return 1
    print("\n".join(dump(tree, int(argv[2]) if len(argv) > 2 else 99)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

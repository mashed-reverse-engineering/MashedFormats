#!/usr/bin/env python3
"""Platform specific RenderWare texture dictionaries (chunk type 0x16) of the PS2 and Xbox versions.

  nativetxd.py info FILE...         list textures
  nativetxd.py export FILE OUTDIR   write all mip levels as PNG

  chunk 0x16 TEXDICTIONARY
    STRUCT (4): u16 numTextures, u16 deviceId (6 = PS2, 8 = Xbox)
    numTextures x chunk 0x15 TEXTURENATIVE   (layout depends on the platform, see Ps2Texture / XboxTexture)
    chunk 0x03 EXTENSION

The decoding follows RenderWare's native raster formats as implemented by librw (src/ps2/ps2raster.cpp,
src/d3d/xbox.cpp, see ../references). Verified here by decoding the PS2 and Xbox copies of the same textures and
comparing them with the platform independent PC versions. Standard library only."""
import struct, sys
from dataclasses import dataclass
from pathlib import Path

from pitexd import png_bytes

TEXTURENATIVE, STRUCT, STRING, EXTENSION, TEXDICTIONARY = 0x15, 0x01, 0x02, 0x03, 0x16
FOURCC_PS2 = 0x00325350  # 'PS2\0'
PLATFORM_XBOX = 5

# RenderWare raster format bits
FMT_MASK, C1555, C565, C4444, LUM8, C8888, C888, C555 = 0xF00, 0x100, 0x200, 0x300, 0x400, 0x500, 0x600, 0xA00
MIPMAP, AUTOMIPMAP, PAL8, PAL4 = 0x8000, 0x1000, 0x2000, 0x4000


def _chunk(t, stamp, payload):
    return struct.pack("<III", t, len(payload), stamp) + payload


def _pad4(b):
    return b + b"\0" * ((-len(b) - 1) % 4 + 1) if len(b) % 4 == 0 else b + b"\0" * (-len(b) % 4)


def _read_chunks(data, start, end):
    pos, out = start, []
    while pos < end:
        t, s, st = struct.unpack_from("<III", data, pos)
        if pos + 12 + s > end:
            raise ValueError(f"chunk 0x{t:X} at {pos:#x} runs past its parent")
        out.append((t, st, data[pos + 12:pos + 12 + s]))
        pos += 12 + s
    return out


# ---------------------------------------------------------------- PS2
def _log2ceil(n):
    k = 0
    while (1 << k) < n:
        k += 1
    return k


def _swizzle_index(x, y, logw):
    yb = lambda n: (y >> n) & 1
    xb = lambda n: (x >> n) & 1
    x ^= (yb(1) ^ yb(2)) << 2
    nx = (x & 7) | ((x >> 1) & ~7)
    ny = (y & 1) | ((y >> 1) & ~1)
    n = yb(1) | xb(3) << 1
    return n | nx << 2 | ny << (logw - 1 + 2)


def ps2_unswizzle8(buf, w, h):
    """buf holds a (w x h) 8 bit image in PS2 'PSMT8 stored as PSMCT32' order, returns the linear image."""
    logw = _log2ceil(w)
    mask = (1 << (logw + 2)) - 1
    out = bytearray(w * h)
    for y in range(0, h, 4):
        tmp = buf[y << logw:(y << logw) + 4 * w]
        for i in range(4):
            if y + i >= h:
                break
            base = (y + i) << logw
            for x in range(w):
                out[base + x] = tmp[_swizzle_index(x, y + i, logw) & mask]
    return bytes(out)


def ps2_unswizzle4(buf, w, h):
    """4 bit variant: returns one palette index per byte (w x h)."""
    logw = _log2ceil(w)
    mask = (1 << (logw + 2)) - 1
    out = bytearray(w * h)
    for y in range(0, h, 4):
        tmp = buf[y << (logw - 1):(y << (logw - 1)) + 2 * w]
        for i in range(4):
            if y + i >= h:
                break
            base = (y + i) << logw
            for x in range(w):
                s = _swizzle_index(x, y + i, logw) & mask
                b = tmp[s >> 1]
                out[base + x] = (b >> 4) if s & 1 else (b & 15)
    return bytes(out)


def ps2_swizzle8(lin, w, h):
    """Inverse of ps2_unswizzle8 (linear 8 bit image -> PS2 storage order)."""
    logw = _log2ceil(w)
    mask = (1 << (logw + 2)) - 1
    out = bytearray(w * h)
    for y in range(0, h, 4):
        tmp = bytearray(4 * w)
        for i in range(4):
            if y + i >= h:
                break
            for x in range(w):
                tmp[_swizzle_index(x, y + i, logw) & mask] = lin[(y + i) * w + x]
        out[y << logw:(y << logw) + 4 * w] = tmp[:min(4 * w, len(out) - (y << logw))]
    return bytes(out)


def ps2_swizzle4(lin, w, h):
    """Inverse of ps2_unswizzle4 (one index per byte -> packed nibbles in PS2 storage order)."""
    logw = _log2ceil(w)
    mask = (1 << (logw + 2)) - 1
    out = bytearray(w * h // 2)
    for y in range(0, h, 4):
        tmp = bytearray(2 * w)
        for i in range(4):
            if y + i >= h:
                break
            for x in range(w):
                s = _swizzle_index(x, y + i, logw) & mask
                v = lin[(y + i) * w + x] & 15
                tmp[s >> 1] = (tmp[s >> 1] & 15) | v << 4 if s & 1 else (tmp[s >> 1] & 0xF0) | v
        out[y << (logw - 1):(y << (logw - 1)) + 2 * w] = tmp[:min(2 * w, len(out) - (y << (logw - 1)))]
    return bytes(out)


@dataclass
class Ps2Texture:
    """TEXTURENATIVE of the PS2:  STRUCT(8: u32 'PS2\\0', u32 filterAddressing), STRING name, STRING mask,
    STRUCT { STRUCT(64 byte header), STRUCT(pixels + palette) }, EXTENSION.
    Header: u32 width, height, depth; u16 rasterFormat; i16 version (2 = packets with GIF tags);
    u64 tex0; u32 paletteOffset; u32 tex1low (mip levels - 1) << 2; u64 miptbp1, miptbp2;
    u32 pixelSize, paletteSize, totalSize, mipmapVal.
    Pixels: per mip level a 0x50 byte GIF packet followed by the level's data (16 byte aligned, width at least
    16 and height at least 4 for swizzled 8 bit, 32/4 for 4 bit); then the palette packet (0x50 bytes + entries)."""
    stamp: int
    name: str
    mask: str
    filter: int
    width: int
    height: int
    depth: int
    raster_format: int
    version: int
    gs: tuple          # (tex0, paletteOffset, tex1low, miptbp1, miptbp2, totalSize, mipmapVal)
    pixels: bytes      # pixelSize bytes
    palette: bytes     # paletteSize bytes
    extension: bytes   # EXTENSION payload (e.g. SKYMIPMAP)
    ext_stamp: int

    platform = "ps2"

    @property
    def num_levels(self):
        return (self.gs[2] >> 2) + 1 if self.raster_format & MIPMAP else 1

    def _min_size(self):
        fmt = self.raster_format & FMT_MASK
        if self.raster_format & PAL8:
            return (16, 4) if self.version >= 2 else (8, 1)
        if self.raster_format & PAL4:
            return (32, 4) if self.version >= 2 else (8, 1)
        return {C8888: (2, 1), C1555: (4, 1), C555: (4, 1)}.get(fmt, (8, 1))

    def level_data(self):
        """[(width, height, raw bytes)] of the mip levels as stored."""
        minw, minh = self._min_size()
        w, h, off, out = self.width, self.height, 0, []
        for _ in range(self.num_levels):
            mw, mh = max(w, minw), max(h, minh)
            size = (mw * mh * self.depth // 8 + 15) & ~15
            off += 0x50
            out.append((w, h, mw, mh, self.pixels[off:off + size]))
            off += size
            w, h = max(w // 2, 1), max(h // 2, 1)
        return out

    def palette_rgba(self):
        n = 16 if self.raster_format & PAL4 else 256
        raw = self.palette[0x50:0x50 + n * 4]
        ents = [bytearray(raw[i * 4:i * 4 + 4]) for i in range(n)]
        if n == 256:  # CSM1: entries with bit 0x10 set and 0x08 clear are swapped with the one at i ^ 0x18
            for i in range(256):
                if i & 0x18 == 0x10:
                    ents[i], ents[i ^ 0x18] = ents[i ^ 0x18], ents[i]
        for e in ents:
            e[3] = min(255, e[3] * 255 // 128)
        return [bytes(e) for e in ents]

    def levels_rgba(self):
        """[(width, height, rgba bytes)] for every mip level."""
        out = []
        pal = self.palette_rgba() if self.raster_format & (PAL4 | PAL8) else None
        swz = self.version >= 2
        for w, h, mw, mh, raw in self.level_data():
            if self.raster_format & PAL8:
                idx = ps2_unswizzle8(raw, mw, mh) if swz else raw
                px = b"".join(pal[idx[y * mw + x]] for y in range(h) for x in range(w))
            elif self.raster_format & PAL4:
                idx = ps2_unswizzle4(raw, mw, mh) if swz else _nibbles(raw, mw, mh)
                px = b"".join(pal[idx[y * mw + x]] for y in range(h) for x in range(w))
            else:
                fmt = self.raster_format & FMT_MASK
                if fmt == C8888:
                    px = bytearray()
                    for y in range(h):
                        for x in range(w):
                            r, g, b, a = raw[(y * mw + x) * 4:(y * mw + x) * 4 + 4]
                            px += bytes((r, g, b, min(255, a * 255 // 128)))
                    px = bytes(px)
                elif fmt in (C1555, C555):
                    px = bytearray()
                    for y in range(h):
                        for x in range(w):
                            v = struct.unpack_from("<H", raw, (y * mw + x) * 2)[0]
                            px += bytes((((v & 31) * 255) // 31, (((v >> 5) & 31) * 255) // 31, (((v >> 10) & 31) * 255) // 31,
                                         255 if fmt == C555 or v & 0x8000 else 0))
                    px = bytes(px)
                else:
                    raise ValueError(f"unsupported PS2 raster format {self.raster_format:#x}")
            out.append((w, h, px))
        return out

    def build_native(self):
        head = struct.pack("<IIIHhQIIQQIIII", self.width, self.height, self.depth, self.raster_format, self.version,
                           self.gs[0], self.gs[1], self.gs[2], self.gs[3], self.gs[4], len(self.pixels), len(self.palette),
                           self.gs[5], self.gs[6])
        wrapper = _chunk(STRUCT, self.stamp, head) + _chunk(STRUCT, self.stamp, self.pixels + self.palette)
        body = (_chunk(STRUCT, self.stamp, struct.pack("<II", FOURCC_PS2, self.filter))
                + _chunk(STRING, self.stamp, _string(self.name)) + _chunk(STRING, self.stamp, _string(self.mask))
                + _chunk(STRUCT, self.stamp, wrapper) + _chunk(EXTENSION, self.ext_stamp, self.extension))
        return _chunk(TEXTURENATIVE, self.stamp, body)


def _nibbles(raw, w, h):
    out = bytearray(w * h)
    for i in range(w * h):
        b = raw[i >> 1]
        out[i] = (b >> 4) if i & 1 else (b & 15)
    return bytes(out)


def _string(s):
    b = s.encode("latin-1")
    return b + b"\0" * (4 - len(b) % 4)


def _parse_ps2(stamp, payload):
    ch = _read_chunks(payload, 0, len(payload))
    if [c[0] for c in ch] != [STRUCT, STRING, STRING, STRUCT, EXTENSION]:
        raise ValueError("unexpected PS2 texture layout")
    plat, filt = struct.unpack("<II", ch[0][2])
    name = ch[1][2].split(b"\0")[0].decode("latin-1")
    mask = ch[2][2].split(b"\0")[0].decode("latin-1")
    inner = _read_chunks(ch[3][2], 0, len(ch[3][2]))
    if [c[0] for c in inner] != [STRUCT, STRUCT]:
        raise ValueError("unexpected PS2 raster layout")
    w, h, d, fmt, ver, tex0, paloff, tex1low, mip1, mip2, pix, pal, tot, mm = struct.unpack("<IIIHhQIIQQIIII", inner[0][2])
    data = inner[1][2]
    if len(data) != pix + pal:
        raise ValueError("pixel data size mismatch")
    return Ps2Texture(stamp, name, mask, filt, w, h, d, fmt, ver, (tex0, paloff, tex1low, mip1, mip2, tot, mm),
                      data[:pix], data[pix:], ch[4][2], ch[4][1])


# ---------------------------------------------------------------- Xbox
def xbox_unswizzle(src, w, h, bpp):
    """Morton order -> linear (D3D swizzled texture)."""
    mask_u = mask_v = 0
    i = j = 1
    while True:
        c = 0
        if i < w:
            mask_u |= j
            j <<= 1
            c = j
        if i < h:
            mask_v |= j
            j <<= 1
            c = j
        i <<= 1
        if not c:
            break
    out = bytearray(w * h * bpp)
    v = 0
    for y in range(h):
        u = 0
        for x in range(w):
            o = (u | v) * bpp
            out[(y * w + x) * bpp:(y * w + x + 1) * bpp] = src[o:o + bpp]
            u = (u - mask_u) & mask_u
        v = (v - mask_v) & mask_v
    return bytes(out)


def xbox_swizzle(lin, w, h, bpp):
    """Inverse of xbox_unswizzle (linear -> Morton order)."""
    mask_u = mask_v = 0
    i = j = 1
    while True:
        c = 0
        if i < w:
            mask_u |= j
            j <<= 1
            c = j
        if i < h:
            mask_v |= j
            j <<= 1
            c = j
        i <<= 1
        if not c:
            break
    out = bytearray(w * h * bpp)
    v = 0
    for y in range(h):
        u = 0
        for x in range(w):
            o = (u | v) * bpp
            out[o:o + bpp] = lin[(y * w + x) * bpp:(y * w + x + 1) * bpp]
            u = (u - mask_u) & mask_u
        v = (v - mask_v) & mask_v
    return bytes(out)


@dataclass
class XboxTexture:
    """TEXTURENATIVE of the Xbox: one STRUCT: u32 platform (5), u32 filterAddressing, char name[32], char mask[32],
    i32 rasterFormat, u16 hasAlpha, u16 unknown (0), u16 width, u16 height, u8 depth (bits per stored pixel),
    u8 numLevels, u8 type, u8 compression (0 here), i32 totalSize; then the palette (16 or 256 BGRA entries for PAL4/PAL8,
    4 bytes each) and totalSize bytes of swizzled (Morton order) pixel data, mip levels back to back (each level
    padded to 4 bytes); EXTENSION."""
    stamp: int
    name: str
    mask: str
    filter: int
    raster_format: int
    has_alpha: int
    unknown: int
    width: int
    height: int
    depth: int
    num_levels: int
    type: int
    compression: int
    palette: bytes
    data: bytes
    ext_stamp: int
    extension: bytes

    platform = "xbox"

    def levels_rgba(self):
        if self.compression:
            raise ValueError(f"compressed Xbox textures (format {self.compression:#x}) are not supported")
        bpp = self.depth // 8
        out, w, h, off = [], self.width, self.height, 0
        pal = None
        if self.raster_format & (PAL4 | PAL8):
            n = 16 if self.raster_format & PAL4 else 256
            pal = [bytes((self.palette[i * 4 + 2], self.palette[i * 4 + 1], self.palette[i * 4], self.palette[i * 4 + 3]))
                   for i in range(n)]
        for _ in range(self.num_levels):
            lin = xbox_unswizzle(self.data[off:off + w * h * bpp], w, h, bpp)
            if pal:
                px = b"".join(pal[lin[i]] for i in range(w * h))
            elif (self.raster_format & FMT_MASK) in (C8888, C888) and bpp == 4:
                a = (self.raster_format & FMT_MASK) == C8888
                px = b"".join(bytes((lin[i * 4 + 2], lin[i * 4 + 1], lin[i * 4], lin[i * 4 + 3] if a else 255)) for i in range(w * h))
            else:
                raise ValueError(f"unsupported Xbox raster format {self.raster_format:#x}")
            out.append((w, h, px))
            off += w * h * bpp  # levels are back to back, only the whole block is padded to 4 bytes
            w, h = max(w // 2, 1), max(h // 2, 1)
        return out

    def build_native(self):
        body = (struct.pack("<II", PLATFORM_XBOX, self.filter) + self.name.encode("latin-1").ljust(32, b"\0")
                + self.mask.encode("latin-1").ljust(32, b"\0")
                + struct.pack("<iHHHHBBBBi", self.raster_format, self.has_alpha, self.unknown, self.width, self.height,
                              self.depth, self.num_levels, self.type, self.compression, len(self.data))
                + self.palette + self.data)
        return _chunk(TEXTURENATIVE, self.stamp, _chunk(STRUCT, self.stamp, body) + _chunk(EXTENSION, self.ext_stamp, self.extension))


def _parse_xbox(stamp, payload):
    ch = _read_chunks(payload, 0, len(payload))
    if [c[0] for c in ch] != [STRUCT, EXTENSION]:
        raise ValueError("unexpected Xbox texture layout")
    b = ch[0][2]
    plat, filt = struct.unpack_from("<II", b, 0)
    name = b[8:40].split(b"\0")[0].decode("latin-1")
    mask = b[40:72].split(b"\0")[0].decode("latin-1")
    fmt, alpha, unk, w, h, depth, nlev, typ, comp, total = struct.unpack_from("<iHHHHBBBBi", b, 72)
    pal_len = 128 if fmt & PAL4 else 1024 if fmt & PAL8 else 0  # PAL4 palettes occupy 32 entries on the Xbox
    pal = b[92:92 + pal_len]  # 72 byte name/mask block + 20 byte raster header
    data = b[92 + pal_len:]
    if len(data) != total:
        raise ValueError("pixel data size mismatch")
    return XboxTexture(stamp, name, mask, filt, fmt, alpha, unk, w, h, depth, nlev, typ, comp, pal, data, ch[1][1], ch[1][2])


# ---------------------------------------------------------------- dictionary
@dataclass
class NativeTexDict:
    stamp: int
    device_id: int
    textures: list
    ext_stamp: int = 0
    extension: bytes = b""
    struct_stamp: int = 0

    @classmethod
    def parse(cls, data):
        t, size, stamp = struct.unpack_from("<III", data, 0)
        if t != TEXDICTIONARY or size + 12 != len(data):
            raise ValueError("not a texture dictionary")
        ch = _read_chunks(data, 12, len(data))
        if ch[0][0] != STRUCT or ch[-1][0] != EXTENSION:
            raise ValueError("unexpected dictionary layout")
        num, dev = struct.unpack("<HH", ch[0][2])
        natives = ch[1:-1]
        if len(natives) != num or any(c[0] != TEXTURENATIVE for c in natives):
            raise ValueError("texture count mismatch")
        texs = []
        for _, st, payload in natives:
            plat = struct.unpack_from("<I", payload, 12)[0]
            if plat == FOURCC_PS2:
                texs.append(_parse_ps2(st, payload))
            elif plat == PLATFORM_XBOX:
                texs.append(_parse_xbox(st, payload))
            else:
                raise ValueError(f"unknown platform {plat:#x}")
        return cls(stamp, dev, texs, ch[-1][1], ch[-1][2], ch[0][1])

    def build(self):
        body = (_chunk(STRUCT, self.struct_stamp, struct.pack("<HH", len(self.textures), self.device_id))
                + b"".join(t.build_native() for t in self.textures) + _chunk(EXTENSION, self.ext_stamp, self.extension))
        return _chunk(TEXDICTIONARY, self.stamp, body)


def load(path):
    return NativeTexDict.parse(Path(path).read_bytes())


def main(argv):
    if len(argv) < 3 or argv[1] not in ("info", "export"):
        print(__doc__)
        return 2
    if argv[1] == "info":
        for f in argv[2:]:
            d = load(f)
            print(f"{f}: {len(d.textures)} textures, device {d.device_id}")
            for t in d.textures:
                print(f"  {t.name:<22} {t.platform} {t.width}x{t.height} depth {t.depth} format {t.raster_format:#x} "
                      f"{t.num_levels} levels")
        return 0
    d = load(argv[2])
    out = Path(argv[3])
    out.mkdir(parents=True, exist_ok=True)
    for t in d.textures:
        for n, (w, h, px) in enumerate(t.levels_rgba()):
            (out / f"{t.name}_mip{n}.png").write_bytes(png_bytes(w, h, px))
    print(f"{len(d.textures)} textures -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

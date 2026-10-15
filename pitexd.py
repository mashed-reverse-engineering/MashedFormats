#!/usr/bin/env python3
"""RenderWare 'platform independent texture dictionary' (PITexD, chunk type 0x23) as used by the game's *.TXD files.

  pitexd.py info FILE...            list textures (TXD or zlib compressed .CMP)
  pitexd.py export FILE OUTDIR      write every texture (all mip levels) as PNG

Layout (found by dissecting the files, confirmed against the 3.7.0.2 SDK headers and the statically linked
RtPITexDictionaryStreamRead; see FORMATS.md):
  chunk 0x23 (PITEXDICTIONARY)
    u16 numTextures, u16 deviceId                       (deviceId is 1 in all shipped files)
    numTextures times:
      u32 numImages                                     number of mip levels
      numImages x chunk 0x18 (IMAGE)
        chunk 0x01 (STRUCT, 16 bytes): u32 width, height, depth, stride
        pixels   height * stride bytes (8/4 bit indexed, or 32 bit RGBA)
        palette  (1 << depth) RGBA entries AFTER the pixels, only if depth <= 8
      chunk 0x06 (TEXTURE)
        chunk 0x01 (STRUCT, 4 bytes): u32 filtering | addressing  (low byte filter mode, bits 8..11 U, 12..15 V addressing)
        chunk 0x02 (STRING) texture name, chunk 0x02 (STRING) mask name   (NUL padded to 4 bytes)
        chunk 0x03 (EXTENSION)                          plugin data, kept raw
Only the standard library is used."""
import struct, sys, zlib
from dataclasses import dataclass, field
from pathlib import Path

PITEXDICT, IMAGE, STRUCT, STRING, EXTENSION, TEXTURE = 0x23, 0x18, 0x01, 0x02, 0x03, 0x06


def _hdr(data, off):
    t, s, st = struct.unpack_from("<III", data, off)
    return t, s, st


def _chunk(t, stamp, payload):
    return struct.pack("<III", t, len(payload), stamp) + payload


@dataclass
class Image:
    width: int
    height: int
    depth: int
    stride: int
    palette: bytes   # b"" for 32 bit images
    pixels: bytes
    stamp: int       # library stamp of the IMAGE chunk
    struct_stamp: int

    def build(self):
        st = struct.pack("<4I", self.width, self.height, self.depth, self.stride)
        return _chunk(IMAGE, self.stamp, _chunk(STRUCT, self.struct_stamp, st) + self.pixels + self.palette)

    def rgba(self):
        """Pixels as a flat RGBA byte string (width*height*4)."""
        out = bytearray()
        if self.depth == 32:
            for y in range(self.height):
                out += self.pixels[y * self.stride:y * self.stride + self.width * 4]
        elif self.depth == 8:
            for y in range(self.height):
                for x in range(self.width):
                    i = self.pixels[y * self.stride + x] * 4
                    out += self.palette[i:i + 4]
        elif self.depth == 4:
            for y in range(self.height):
                for x in range(self.width):
                    b = self.pixels[y * self.stride + x // 2]
                    i = ((b >> 4) if x & 1 else (b & 15)) * 4
                    out += self.palette[i:i + 4]
        else:
            raise ValueError(f"unsupported depth {self.depth}")
        return bytes(out)

    def png(self):
        return png_bytes(self.width, self.height, self.rgba())


@dataclass
class Texture:
    name: str
    mask: str
    flags: int
    images: list          # mip levels, largest first
    stamp: int            # TEXTURE chunk stamp
    parts: list           # (stamp, ) of the struct/string/extension sub chunks, kept for a lossless rebuild
    extension: bytes      # payload of the EXTENSION chunk
    mask_pad: tuple       # raw (name bytes, mask bytes) including NUL padding

    @property
    def filtering(self):
        return self.flags & 0xFF

    @property
    def addressing(self):
        return (self.flags >> 8) & 0xF, (self.flags >> 12) & 0xF

    def build(self):
        s_st, n_st, m_st, e_st = self.parts
        body = (_chunk(STRUCT, s_st, struct.pack("<I", self.flags)) + _chunk(STRING, n_st, self.mask_pad[0])
                + _chunk(STRING, m_st, self.mask_pad[1]) + _chunk(EXTENSION, e_st, self.extension))
        return struct.pack("<I", len(self.images)) + b"".join(i.build() for i in self.images) + _chunk(TEXTURE, self.stamp, body)


@dataclass
class PITexDict:
    stamp: int
    device_id: int
    textures: list

    @classmethod
    def parse(cls, data):
        t, size, stamp = _hdr(data, 0)
        if t != PITEXDICT or size + 12 != len(data):
            raise ValueError("not a platform independent texture dictionary")
        num, dev = struct.unpack_from("<HH", data, 12)
        pos, textures = 16, []
        for _ in range(num):
            n_img = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            images = []
            for _ in range(n_img):
                t, s, st = _hdr(data, pos)
                if t != IMAGE:
                    raise ValueError(f"expected image chunk at {pos:#x}")
                t2, s2, st2 = _hdr(data, pos + 12)
                if (t2, s2) != (STRUCT, 16):
                    raise ValueError(f"bad image struct at {pos:#x}")
                w, h, d, stride = struct.unpack_from("<4I", data, pos + 24)
                pal = (4 << d) if d <= 8 else 0
                if s != 28 + pal + h * stride:
                    raise ValueError(f"image size mismatch at {pos:#x}")
                a = pos + 40  # image header 12 + struct chunk 12 + 16
                images.append(Image(w, h, d, stride, data[a + h * stride:a + h * stride + pal], data[a:a + h * stride], st, st2))
                pos += 12 + s
            t, s, st = _hdr(data, pos)
            if t != TEXTURE:
                raise ValueError(f"expected texture chunk at {pos:#x}")
            end = pos + 12 + s
            p = pos + 12
            parts, raws = [], []
            flags = None
            for want in (STRUCT, STRING, STRING, EXTENSION):
                ct, cs, cst = _hdr(data, p)
                if ct != want:
                    raise ValueError(f"bad texture sub chunk at {p:#x}")
                body = data[p + 12:p + 12 + cs]
                if want == STRUCT:
                    flags = struct.unpack("<I", body)[0]
                elif want == STRING:
                    raws.append(body)
                else:
                    ext = body
                parts.append(cst)
                p += 12 + cs
            if p != end:
                raise ValueError(f"texture chunk size mismatch at {pos:#x}")
            names = [r.split(b"\0")[0].decode("latin-1") for r in raws]
            textures.append(Texture(names[0], names[1], flags, images, st, parts, ext, tuple(raws)))
            pos = end
        if pos != len(data):
            raise ValueError("trailing data")
        return cls(stamp, dev, textures)

    def build(self):
        body = struct.pack("<HH", len(self.textures), self.device_id) + b"".join(t.build() for t in self.textures)
        return _chunk(PITEXDICT, self.stamp, body)


# ---------------------------------------------------------------- tiny PNG writer (stdlib only)
def png_bytes(width, height, rgba):
    def chunk(tag, payload):
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    raw = b"".join(b"\0" + rgba[y * width * 4:(y + 1) * width * 4] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def load(path):
    data = Path(path).read_bytes()
    if data[:2] == b"\x78\xda":
        data = zlib.decompress(data)
    return PITexDict.parse(data)


def main(argv):
    if len(argv) < 3 or argv[1] not in ("info", "export"):
        print(__doc__)
        return 2
    if argv[1] == "info":
        for f in argv[2:]:
            d = load(f)
            print(f"{f}: {len(d.textures)} textures")
            for t in d.textures:
                i = t.images[0]
                print(f"  {t.name:<24} mask {t.mask or '-':<10} {i.width}x{i.height} {i.depth}bit, {len(t.images)} mips, "
                      f"filter {t.filtering:#x} address {t.addressing}")
        return 0
    d = load(argv[2])
    out = Path(argv[3])
    out.mkdir(parents=True, exist_ok=True)
    for t in d.textures:
        for n, im in enumerate(t.images):
            (out / f"{t.name}_mip{n}.png").write_bytes(im.png())
    print(f"{len(d.textures)} textures -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

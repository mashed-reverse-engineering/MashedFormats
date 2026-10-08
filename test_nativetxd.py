"""Tests for nativetxd.py (PS2 / Xbox texture dictionaries). Run: python -m unittest -v"""
import os, random, struct, tempfile, unittest
from pathlib import Path

import nativetxd as nt

GAME = Path(os.environ.get("PIZ_GAME_DIR", Path(__file__).parent.parent / "MashedGame"))
ST = 0x1C02000A


class Swizzle(unittest.TestCase):
    def rnd(self, n, mod=256, seed=1):
        r = random.Random(seed)
        return bytes(r.randrange(mod) for _ in range(n))

    def test_ps2_8bit_roundtrip(self):
        for w, h in ((16, 4), (16, 8), (64, 64), (128, 256), (32, 16)):
            lin = self.rnd(w * h)
            sw = nt.ps2_swizzle8(lin, w, h)
            self.assertNotEqual(sw, lin)
            self.assertEqual(nt.ps2_unswizzle8(sw, w, h), lin, (w, h))

    def test_ps2_4bit_roundtrip(self):
        for w, h in ((32, 4), (64, 64), (128, 32)):
            lin = self.rnd(w * h, 16)
            sw = nt.ps2_swizzle4(lin, w, h)
            self.assertEqual(len(sw), w * h // 2)
            self.assertEqual(nt.ps2_unswizzle4(sw, w, h), lin, (w, h))

    def test_ps2_known_values(self):
        # the swizzle is a permutation: first 16 pixels of an 16x4 image go to these byte positions
        lin = bytes(range(64))
        sw = nt.ps2_swizzle8(lin, 16, 4)
        self.assertEqual(sorted(sw), list(range(64)))
        self.assertEqual(sw[0], 0)

    def test_xbox_roundtrip_and_morton(self):
        for w, h, bpp in ((1, 1, 1), (4, 4, 1), (8, 4, 4), (32, 8, 1), (16, 16, 4), (2, 1, 1)):
            lin = self.rnd(w * h * bpp, seed=w + h)
            self.assertEqual(nt.xbox_unswizzle(nt.xbox_swizzle(lin, w, h, bpp), w, h, bpp), lin, (w, h, bpp))
        # square texture: Morton order interleaves x and y bits (x in the even bits)
        lin = bytes(range(16))
        sw = nt.xbox_swizzle(lin, 4, 4, 1)
        self.assertEqual(sw[:4], bytes([0, 1, 4, 5]))


def ps2_texture(w=16, h=4, depth=8, fmt=0x2504, name="T"):
    """A PS2 texture with a single mip level, built from a recognisable index image."""
    lin = bytes((x + y * w) & 255 for y in range(h) for x in range(w))
    pixels = b"\0" * 0x50 + nt.ps2_swizzle8(lin, w, h)
    pixels += b"\0" * (-len(pixels) % 16)
    ent = [bytearray((i, 255 - i, i // 2, 0x80 if i else 0)) for i in range(256)]
    # the file stores entries in CSM1 order: swap entries whose index has bit 4 set and bit 3 clear
    for i in range(256):
        if i & 0x18 == 0x10:
            ent[i], ent[i ^ 0x18] = ent[i ^ 0x18], ent[i]
    palette = b"\0" * 0x50 + b"".join(bytes(e) for e in ent)
    return nt.Ps2Texture(ST, name, "", 0x1102, w, h, depth, fmt, 2, (0x2000000625320000, 512, 0, 0x40000400004000, 0x40000400004000, 4032, 0),
                         pixels, palette, b"", ST), lin


class Synthetic(unittest.TestCase):
    def test_ps2_dictionary_roundtrip_and_decode(self):
        tex, lin = ps2_texture()
        d = nt.NativeTexDict(ST, 6, [tex], ST, b"", ST)
        data = d.build()
        back = nt.NativeTexDict.parse(data)
        self.assertEqual(back.build(), data)
        t = back.textures[0]
        self.assertEqual((t.platform, t.name, t.width, t.height, t.num_levels), ("ps2", "T", 16, 4, 1))
        (w, h, rgba), = t.levels_rgba()
        self.assertEqual((w, h), (16, 4))
        for i in (0, 1, 17, 63):  # pixel i uses palette entry lin[i]; alpha 0x80 -> 255
            self.assertEqual(rgba[i * 4:i * 4 + 4], bytes((lin[i], 255 - lin[i], lin[i] // 2, 255 if lin[i] else 0)), i)

    def test_xbox_dictionary_roundtrip_and_decode(self):
        w = h = 4
        lin = bytes(range(16))
        pal = b"".join(bytes(((i * 2) & 255, i, 255 - i, 255)) for i in range(256))  # stored as B, G, R, A
        tex = nt.XboxTexture(ST, "X", "", 0x1102, 0xB500, 1, 0, w, h, 8, 3, 4, 0, pal,
                             nt.xbox_swizzle(lin, 4, 4, 1) + nt.xbox_swizzle(lin[:4], 2, 2, 1) + b"\x05" + b"\0\0\0", ST, b"")
        # 4x4 + 2x2 + 1x1 = 21 bytes, padded to 24 by the writer of the original files
        d = nt.NativeTexDict(ST, 8, [tex], ST, b"", ST)
        data = d.build()
        back = nt.NativeTexDict.parse(data)
        self.assertEqual(back.build(), data)
        lv = back.textures[0].levels_rgba()
        self.assertEqual([(a, b) for a, b, _ in lv], [(4, 4), (2, 2), (1, 1)])
        self.assertEqual(lv[0][2][5 * 4:5 * 4 + 4], bytes((255 - 5, 5, 10, 255)))  # BGRA -> RGBA
        self.assertEqual(lv[2][2], bytes((255 - 5, 5, 10, 255)))

    def test_bad_input(self):
        tex, _ = ps2_texture()
        data = nt.NativeTexDict(ST, 6, [tex], ST, b"", ST).build()
        with self.assertRaises(ValueError):
            nt.NativeTexDict.parse(data[:-3])
        bad = bytearray(data)
        struct.pack_into("<I", bad, 0x28 + 12, 0x1234)  # unknown platform id
        with self.assertRaises(ValueError):
            nt.NativeTexDict.parse(bytes(bad))
        with self.assertRaises(ValueError):
            nt.NativeTexDict.parse(b"\x23\0\0\0" + bytes(20))

    def test_cli_export(self):
        tex, _ = ps2_texture()
        data = nt.NativeTexDict(ST, 6, [tex], ST, b"", ST).build()
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "t.txd"
            f.write_bytes(data)
            self.assertEqual(nt.main(["x", "export", str(f), str(Path(tmp) / "o")]), 0)
            self.assertTrue((Path(tmp) / "o" / "T_mip0.png").read_bytes().startswith(b"\x89PNG"))


def native_files():
    out = []
    for p in sorted(GAME.glob("*/extracted/**/*")):
        if p.suffix.lower() == ".txd" and p.is_file():
            d = p.read_bytes()
            if struct.unpack_from("<I", d, 0)[0] == 0x16:
                out.append((p, d))
    return out


@unittest.skipUnless(list(GAME.glob("*/extracted")), "no extracted game files")
class RealFiles(unittest.TestCase):
    def test_all_roundtrip_and_decode(self):
        files = native_files()
        self.assertEqual(len(files), 36)
        for p, d in files:
            nd = nt.NativeTexDict.parse(d)
            self.assertEqual(nd.build(), d, f"{p} differs after rebuild")
            for t in nd.textures:
                lv = t.levels_rgba()
                self.assertEqual(len(lv), t.num_levels if t.platform == "xbox" else t.num_levels)
                for w, h, px in lv:
                    self.assertEqual(len(px), w * h * 4)

    def test_ps2_level_sizes_add_up(self):
        for p, d in native_files():
            for t in nt.NativeTexDict.parse(d).textures:
                if t.platform == "ps2":
                    total = sum(0x50 + len(x[4]) for x in t.level_data())
                    self.assertEqual(total, len(t.pixels), (p, t.name))
                    for _, _, mw, mh, raw in t.level_data():
                        self.assertEqual(len(raw), (mw * mh * t.depth // 8 + 15) & ~15)

    def test_platforms_agree(self):
        """The same textures exist for PS2 and Xbox (different swizzling, palettes and mip chains): they must decode alike."""
        ps2 = xbox = None
        for p, d in native_files():
            if p.parent.name == "PS2" and p.name == "BADGES.TXD":
                ps2 = nt.NativeTexDict.parse(d)
            if p.parent.name == "XBOX" and p.name == "BADGES.TXD":
                xbox = nt.NativeTexDict.parse(d)
        self.assertIsNotNone(ps2)
        self.assertIsNotNone(xbox)
        self.assertEqual([t.name for t in ps2.textures], [t.name for t in xbox.textures])
        for a, b in zip(ps2.textures, xbox.textures):
            (w, h, pa), (w2, h2, pb) = a.levels_rgba()[0], b.levels_rgba()[0]
            self.assertEqual((w, h), (w2, h2))
            n = w * h
            if a.name in ("Air", "Tritex", "Box"):  # exact copies (8 bit, 24 bit and 32 bit)
                # (PS2 alpha is 0..0x80 and gets scaled, which is off by up to 4)
                bad = sum(1 for i in range(n) if any(abs(pa[i * 4 + k] - pb[i * 4 + k]) > (6 if k == 3 else 3) for k in range(4)))
                self.assertEqual(bad, 0, a.name)
            # every texture: opaque pixels have (nearly) the same colour on both platforms
            both = [i for i in range(n) if pa[i * 4 + 3] > 200 and pb[i * 4 + 3] > 200]
            close = sum(1 for i in both if all(abs(pa[i * 4 + k] - pb[i * 4 + k]) <= 3 for k in range(3)))
            if both:
                self.assertGreaterEqual(close * 100 // len(both), 60 if a.name.startswith("Semi") else 90, a.name)

    def test_controller_font_atlas(self):
        """PS2FGDC20 / XboxFGDC20 hold the button glyphs in the lower left and controllers in the lower right."""
        for p, d in native_files():
            # (the Drive to Survive copies hold a different atlas)
            if p.name in ("PS2FGDC20.TXD", "XBOXFGDC20.TXD") and "FullyLoaded" in p.parts:
                t = nt.NativeTexDict.parse(d).textures[0]
                w, h, px = t.levels_rgba()[0]
                self.assertEqual((w, h), (512, 256))
                # white glyphs with alpha: the ASCII font fills the upper rows, the pads the lower left
                top = sum(1 for y in range(0, 160) for x in range(w) if px[(y * w + x) * 4 + 3] > 128)
                glyphs = sum(1 for y in range(230, 256) for x in range(0, 140) if px[(y * w + x) * 4 + 3] > 128)
                self.assertGreater(top, 2000, p)
                self.assertGreater(glyphs, 300, p)


if __name__ == "__main__":
    unittest.main()

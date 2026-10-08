"""Tests for pitexd.py. Run: python -m unittest -v"""
import os, struct, tempfile, unittest, zlib
from pathlib import Path

import pitexd

GAME = Path(os.environ.get("PIZ_GAME_DIR", Path(__file__).parent.parent / "MashedGame"))


def make_image(w, h, depth, fill, stamp=0x1803FFFF):
    stride = {32: w * 4, 8: w, 4: (w + 1) // 2}[depth]
    pal = bytes((i * 7) & 255 for i in range(4 << depth)) if depth <= 8 else b""
    return pitexd.Image(w, h, depth, stride, pal, bytes([fill]) * (h * stride), stamp, stamp)


def make_texture(name, images, flags=0x1104):
    pad = lambda s: (s.encode() + b"\0" * 4)[: (len(s) + 4) & ~3]
    st = 0x1803FFFF
    return pitexd.Texture(name, "", flags, images, st, [st, st, st, st], b"", (pad(name), pad("")))


class Synthetic(unittest.TestCase):
    def dict(self):
        t1 = make_texture("Air", [make_image(4, 4, 8, 3), make_image(2, 2, 8, 3), make_image(1, 1, 8, 3)])
        t2 = make_texture("Box", [make_image(2, 2, 32, 9)])
        t3 = make_texture("Sv", [make_image(5, 3, 4, 0x21)])
        return pitexd.PITexDict(0x1803FFFF, 1, [t1, t2, t3])

    def test_roundtrip(self):
        d = self.dict()
        data = d.build()
        self.assertEqual(struct.unpack_from("<I", data, 0)[0], 0x23)
        self.assertEqual(struct.unpack_from("<I", data, 4)[0], len(data) - 12)
        back = pitexd.PITexDict.parse(data)
        self.assertEqual([t.name for t in back.textures], ["Air", "Box", "Sv"])
        self.assertEqual([len(t.images) for t in back.textures], [3, 1, 1])
        self.assertEqual(back.build(), data)

    def test_flags(self):
        t = make_texture("x", [make_image(1, 1, 32, 0)], 0x1104)
        self.assertEqual((t.filtering, t.addressing), (4, (1, 1)))

    def test_rgba_and_png(self):
        im = make_image(2, 2, 32, 0x80)
        self.assertEqual(im.rgba(), bytes([0x80] * 16))
        png = im.png()
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        w, h = struct.unpack(">II", png[16:24])
        self.assertEqual((w, h), (2, 2))
        # zero palette = intensity image (fonts): white with the index as alpha
        im = pitexd.Image(2, 1, 8, 2, bytes(1024), bytes([0, 200]), 0, 0)
        self.assertEqual(im.rgba(), bytes([255, 255, 255, 0, 255, 255, 255, 200]))
        # normal palette
        pal = bytes([1, 2, 3, 4]) + bytes([9, 8, 7, 6]) + bytes(1016)
        im = pitexd.Image(2, 1, 8, 2, pal, bytes([1, 0]), 0, 0)
        self.assertEqual(im.rgba(), bytes([9, 8, 7, 6, 1, 2, 3, 4]))
        # 4 bit: low nibble is the left pixel
        pal4 = bytes([1, 1, 1, 1]) + bytes([2, 2, 2, 2]) + bytes(56)
        im = pitexd.Image(2, 1, 4, 1, pal4, bytes([0x10]), 0, 0)
        self.assertEqual(im.rgba(), bytes([1, 1, 1, 1, 2, 2, 2, 2]))

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            pitexd.PITexDict.parse(b"\x16\0\0\0" + bytes(20))
        data = bytearray(self.dict().build())
        with self.assertRaises(ValueError):
            pitexd.PITexDict.parse(bytes(data[:-5]))
        data[16 + 4] = 0x99  # first image chunk type
        with self.assertRaises(ValueError):
            pitexd.PITexDict.parse(bytes(data))

    def test_cli_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "t.txd"
            f.write_bytes(self.dict().build())
            c = Path(tmp) / "t.txd.cmp"
            c.write_bytes(zlib.compress(f.read_bytes(), 9))
            for src in (f, c):
                self.assertEqual(pitexd.main(["x", "export", str(src), str(Path(tmp) / "out")]), 0)
            self.assertTrue((Path(tmp) / "out" / "Air_mip2.png").exists())
            self.assertTrue((Path(tmp) / "out" / "Box_mip0.png").exists())


@unittest.skipUnless(list(GAME.glob("*/extracted")), "no extracted game files")
class RealFiles(unittest.TestCase):
    def files(self):
        out = []
        for p in GAME.glob("*/extracted/**/*"):
            if p.suffix.lower() in (".txd", ".cmp") and p.is_file():
                data = p.read_bytes()
                if data[:2] == b"\x78\xda":
                    data = zlib.decompress(data)
                out.append((p, data))
        return out

    def test_all_pitexd_files_roundtrip(self):
        n = 0
        for p, data in self.files():
            if struct.unpack_from("<I", data, 0)[0] != 0x23:
                continue  # 0x16 = platform specific dictionaries of the PS2/Xbox versions, not handled here
            d = pitexd.PITexDict.parse(data)
            self.assertEqual(d.build(), data, f"{p} differs after rebuild")
            for t in d.textures:
                for im in t.images:
                    self.assertIn(im.depth, (4, 8, 32))
                    self.assertEqual(len(im.rgba()), im.width * im.height * 4)
            n += 1
        self.assertGreaterEqual(n, 100)

    def test_mip_chain_halves(self):
        for p, data in self.files():
            if struct.unpack_from("<I", data, 0)[0] != 0x23:
                continue
            for t in pitexd.PITexDict.parse(data).textures:
                for a, b in zip(t.images, t.images[1:]):
                    self.assertEqual((b.width, b.height), (max(1, a.width // 2), max(1, a.height // 2)), (p, t.name))


if __name__ == "__main__":
    unittest.main()

"""Tests for rwchunks.py (generic chunk walker) and mashedfmt.py (dispatcher). Run: python -m unittest -v"""
import os, struct, tempfile, unittest, zlib
from pathlib import Path

import mashed_formats as mf
import mashedfmt
import rwchunks
import rwscene
import pitexd

GAME = Path(os.environ.get("PIZ_GAME_DIR", Path(__file__).parent.parent / "MashedGame"))
ST = 0x1C02000A


def chunk(t, payload, stamp=ST):
    return struct.pack("<III", t, len(payload), stamp) + payload


class Rwchunks(unittest.TestCase):
    def test_walk_and_dump(self):
        data = chunk(0x10, chunk(1, b"\0" * 12) + chunk(0x0E, chunk(1, b"\0" * 4) + chunk(3, b"")) + chunk(3, b""))
        tree = rwchunks.parse(data)
        self.assertEqual([c.name for c in tree], ["CLUMP"])
        self.assertEqual([c.name for c in tree[0].children], ["STRUCT", "FRAMELIST", "EXTENSION"])
        lines = rwchunks.dump(tree)
        self.assertEqual(len(lines), 6)
        self.assertIn("CLUMP size=", lines[0])
        self.assertTrue(lines[1].startswith("  STRUCT"))
        self.assertEqual(rwchunks.dump(tree, maxdepth=0), [lines[0]])

    def test_errors(self):
        with self.assertRaises(ValueError):
            rwchunks.parse(chunk(0x10, b"\0" * 8)[:-2])
        with self.assertRaises(ValueError):
            rwchunks.parse(chunk(0x10, b"") + b"abc")

    def test_stamp_version(self):
        self.assertEqual(rwchunks.stamp_version(0x1C02000A), "3.7.0.2")
        self.assertEqual(rwchunks.stamp_version(0x1803FFFF), "3.6.0.3")

    def test_unknown_type_is_leaf(self):
        tree = rwchunks.parse(chunk(0x13269901, b"\x01\x02\x03\x04"))
        self.assertEqual(tree[0].name, "GAME_LED")
        self.assertEqual(tree[0].children, [])


class Dispatcher(unittest.TestCase):
    def test_info_dump_export(self):
        d = pitexd.PITexDict(ST, 1, [])
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            led = tmp / "x.led"
            led.write_bytes(mf.Led(ST, [(-1.0, -1.0, -1.0)] * 384).build())
            txd = tmp / "a.txd"
            txd.write_bytes(d.build())
            cmp_ = tmp / "a.txd.cmp"
            cmp_.write_bytes(zlib.compress(d.build(), 9))
            bum = tmp / "s.bum"
            bum.write_bytes(struct.pack("<8h", *range(8)))
            self.assertEqual(mashedfmt.main(["x", "info", str(led), str(txd), str(cmp_), str(bum)]), 0)
            self.assertEqual(mashedfmt.main(["x", "dump", str(txd)]), 0)
            self.assertEqual(mashedfmt.main(["x", "export", str(bum), str(tmp / "s.wav")]), 0)
            self.assertEqual((tmp / "s.wav").read_bytes()[:4], b"RIFF")
            self.assertEqual(mashedfmt.main(["x", "export", str(cmp_), str(tmp / "a.out")]), 0)  # a .cmp of a texture dictionary -> PNGs
            self.assertTrue((tmp / "a.out").is_dir())
            self.assertEqual(mf.cmp_decompress(cmp_.read_bytes()), d.build())
            bad = tmp / "bad.xyz"
            bad.write_bytes(b"x")
            self.assertEqual(mashedfmt.main(["x", "info", str(bad)]), 1)
            self.assertEqual(mashedfmt.main(["x", "export", str(bad), str(tmp / "o")]), 1)
            self.assertEqual(mashedfmt.main(["x"]), 2)

    def test_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            p = tmp / "a.txd"
            p.write_bytes(pitexd.PITexDict(ST, 1, []).build())
            self.assertEqual(mashedfmt._kind(p), "pitexd")
            p.write_bytes(chunk(0x16, b""))
            self.assertEqual(mashedfmt._kind(p), "nativetxd")
            self.assertEqual(mashedfmt._kind(tmp / "a.dff"), "rwscene")
            self.assertEqual(mashedfmt._kind(tmp / "a.dat"), "mf")
            self.assertIsNone(mashedfmt._kind(tmp / "a.xyz"))


@unittest.skipUnless(list(GAME.glob("*/extracted")), "no extracted game files")
class RealFiles(unittest.TestCase):
    def test_every_rw_file_walks(self):
        """The generic walker accepts every RenderWare stream of the game (a few thousand files)."""
        n = 0
        for p in GAME.glob("*/extracted/**/*"):
            if p.suffix.lower() in (".dff", ".bsp", ".anm", ".spl", ".uva", ".txd") and p.is_file():
                tree = rwchunks.parse(p.read_bytes())
                self.assertEqual(len(tree), 1, p)
                n += 1
        self.assertGreater(n, 1800)

    def test_every_known_file_is_handled_by_the_dispatcher(self):
        seen = set()
        for p in sorted(GAME.glob("*/extracted/**/*")):
            ext = p.suffix.lower()
            if not p.is_file() or ext not in (".led", ".ai", ".mts", ".gsd", ".dat", ".aid", ".rwf", ".rep", ".bum", ".dff", ".bsp",
                                              ".anm", ".spl", ".uva", ".txd", ".cmp"):
                continue
            key = ext
            if ext == ".txd":
                key = "txd-" + p.parent.name if p.parent.name in ("PS2", "XBOX") else "txd-pc"  # PC, PS2 and Xbox dictionaries
            if key in seen:
                continue
            seen.add(key)
            self.assertEqual(mashedfmt.main(["x", "info", str(p)]), 0, p)
        self.assertGreaterEqual(len(seen), 17)


if __name__ == "__main__":
    unittest.main()

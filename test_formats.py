"""Tests for mashed_formats.py. Run: python -m unittest -v
Real file tests use the extracted game files under ../MashedGame/*/extracted (override: MASHED_EXTRACTED_GLOB
is not needed, set PIZ_GAME_DIR like in PIZ.md / test_piz.py) and are skipped when absent."""
import os, struct, unittest
from pathlib import Path

import mashed_formats as mf

GAME = Path(os.environ.get("PIZ_GAME_DIR", Path(__file__).parent.parent / "MashedGame"))


def real(ext):
    return sorted(p for p in GAME.glob(f"*/extracted/**/*") if p.suffix.lower() == ext)


class Synthetic(unittest.TestCase):
    def test_led_roundtrip(self):
        led = mf.Led(0x1803FFFF, [(-1.0, -1.0, -1.0)] * 384)
        led.records[5] = (15.0, 90.0, -1.0)
        data = led.build()
        self.assertEqual(len(data), 12 + 0x1200)
        back = mf.Led.parse(data)
        self.assertEqual(back.used(), [(5, (15.0, 90.0, -1.0))])
        self.assertEqual(back.build(), data)
        with self.assertRaises(ValueError):
            mf.Led.parse(data[:-4])

    def test_ai_roundtrip_and_layout(self):
        grid = [-1] * 16384
        grid[130] = 1
        tiles = bytes([0xFF] * 0x8000)
        raw = [[([(float(i), float(-i)) for i in range(64)], 3) for _ in range(3)] for _ in range(4)]
        tail = bytes(range(80))
        ai = mf.Ai(0x1803FFFF, grid, tiles, 1, None, raw, tail)
        data = ai.build()
        self.assertEqual(len(data), 12 + mf.AI_SIZE)
        back = mf.Ai.parse(data)
        self.assertEqual(back.grid[130], 1)
        self.assertEqual(back.paths[2][1], [(0.0, 0.0), (1.0, -1.0), (2.0, -2.0)])
        self.assertEqual(back.used_tiles(), [1])
        self.assertEqual(back.build(), data)

    def test_mts_roundtrip(self):
        m = mf.Mts(0x1C02000A, [((1, 0, 0), (0, 1, 0), (0, 0, 1), (4.5, 2.0, 127.5), 0x20003)])
        data = m.build()
        self.assertEqual(len(data), 4 + 76)
        self.assertEqual(mf.Mts.parse(data).build(), data)
        with self.assertRaises(ValueError):
            mf.Mts.parse(data + b"x")

    def test_dat_roundtrip(self):
        d = mf.Dat(["English", "Fran\u00e7ais", "\x08 press \x09", ""], [b"", b"\0\0", b"", b"\r\n"])
        data = d.build()
        back = mf.Dat.parse(data)
        self.assertEqual(back.strings, d.strings)
        self.assertEqual(back.gaps, d.gaps)
        self.assertEqual(back.build(), data)

    def test_gsd_aid_rwf(self):
        for n in (9, 12):
            g = mf.Gsd(tuple(range(n)))
            self.assertEqual(mf.Gsd.parse(g.build()).words, g.words)
        with self.assertRaises(ValueError):
            mf.Gsd.parse(b"x" * 40)
        a = mf.Aid([tuple(float(i) for i in range(18))] * 2)
        self.assertEqual(mf.Aid.parse(a.build()).build(), a.build())
        low = [0xFFFF] * 128
        low[65], low[66] = 0, 1
        g = [mf.Glyph(0.0, 0.0, 0.25, 0.5, 0.15, 0), mf.Glyph(0.25, 0.0, 0.5, 0.5, 0.2, 1)]
        r = mf.Rwf(0x1803FFFF, 99, 0x01000001, 0, 33.0, 0.15, (0, 2), 128, low, [1] * 128, g, ["fnt", "fnt2"])
        back = mf.Rwf.parse(r.build())
        self.assertEqual(back.build(), r.build())
        self.assertEqual((back.glyph_for(65).u1, back.glyph_for(66).texture, back.glyph_for(32), back.glyph_for(200).u0), (0.25, 1, None, 0.25))
        self.assertEqual(back.textures, ["fnt", "fnt2"])
        with self.assertRaises(ValueError):
            mf.Rwf.parse(r.build() + b"x")

    def test_cmp_synthetic(self):
        raw = bytes(range(256)) * 100
        self.assertEqual(mf.cmp_decompress(mf.cmp_compress(raw)), raw)

    def test_rep_roundtrip(self):
        def record(track, time, frames):
            hdr = bytearray(mf.Rep.HEADER)
            body = struct.pack("<5I", 1, 1, 0, 0, 0) + b"".join(
                struct.pack("<7fII", 0.0, 0.0, 0.0, 1.0, float(i), 0.0, 2.0 * i, 1000 + 36 * i, 50 if i % 3 == 0 else 0) for i in range(frames))
            struct.pack_into("<IIIf", hdr, 0, 266, len(hdr) + len(body), track, time)
            struct.pack_into("<II", hdr, 92 * 4, frames - 1, frames)
            return mf.RepRecord(bytes(hdr), body)
        rep = mf.Rep([record(30, 30.0, 3), record(3, 40.0, 5)], bytes(100))
        data = rep.build()
        back = mf.Rep.parse(data)
        self.assertEqual(back.build(), data)
        self.assertEqual([(r.id, r.track, r.time, r.frame_count) for r in back.records], [(266, 30, 30.0, 3), (266, 3, 40.0, 5)])
        self.assertEqual(len(back.records[1].frames()), 5)
        self.assertEqual(back.records[1].frames()[2][3], 1.0)       # quaternion w
        self.assertEqual(back.records[1].frames()[2][4:7], (2.0, 0.0, 4.0))
        self.assertEqual(back.records[1].frames()[3][7:], (1000 + 108, 50))
        self.assertEqual((back.records[1].recorded_frames, back.records[1].recorded_seconds), (5, 5 / 7.5))
        self.assertEqual(back.tail, bytes(100))
        with self.assertRaises(ValueError):
            mf.Rep.parse(data[:300])
        with self.assertRaises(ValueError):
            mf.Rep.parse(bytes(1000))

    def test_bum_and_wav(self):
        b = mf.Bum.parse(struct.pack("<4h", 0, 100, -100, 32767))
        self.assertEqual(b.samples, (0, 100, -100, 32767))
        self.assertEqual(mf.Bum.parse(b.build()).build(), b.build())
        wav = b.to_wav(8000)
        self.assertEqual(wav[:4], b"RIFF")
        self.assertEqual(struct.unpack_from("<I", wav, 4)[0], len(wav) - 8)
        self.assertEqual(struct.unpack_from("<I", wav, 16)[0], 16)      # fmt chunk size
        self.assertEqual(struct.unpack_from("<I", wav, 24)[0], 8000)    # sample rate
        self.assertEqual(wav[36:40], b"data")
        with self.assertRaises(ValueError):
            mf.Bum.parse(b"abc")

    def test_rw_stamp(self):
        self.assertEqual(mf.rw_stamp(0x1C02000A), ("3.7.0.2", 10))
        self.assertEqual(mf.rw_stamp(0x1803FFFF), ("3.6.0.3", 0xFFFF))
        self.assertEqual(mf.rw_stamp(0x1400FFFF)[0], "3.5.0.0")


class RealFiles(unittest.TestCase):
    """Every shipped file of these types must parse and be rebuilt byte for byte."""

    def check(self, ext, minimum):
        files = real(ext)
        if not files:
            self.skipTest("no extracted game files")
        self.assertGreaterEqual(len(files), minimum)
        for f in files:
            data = f.read_bytes()
            obj = mf.PARSERS[ext].parse(data)
            self.assertEqual(obj.build(), data, f"{f} differs after rebuild")

    def test_led(self): self.check(".led", 40)
    def test_ai(self): self.check(".ai", 40)
    def test_mts(self): self.check(".mts", 300)
    def test_gsd(self): self.check(".gsd", 8)
    def test_dat(self): self.check(".dat", 14)
    def test_aid(self): self.check(".aid", 5)
    def test_rwf(self): self.check(".rwf", 13)
    def test_rep(self): self.check(".rep", 2)
    def test_bum(self): self.check(".bum", 34)
    def test_led_x(self): self.check(".led_x", 2)

    def test_rep_semantics(self):
        files = real(".rep")
        if not files:
            self.skipTest("no extracted game files")
        for f in files:
            rep = mf.Rep.parse(f.read_bytes())
            self.assertEqual(len(rep.records), 12, f)
            self.assertFalse(any(rep.tail), f)
            for r in rep.records:
                self.assertIn(r.id, (0x10A, 0x10B), f)
                self.assertTrue(10.0 < r.time < 500.0, (f, r.time))
                self.assertEqual(r.size, 412 + 20 + 36 * r.frame_count, f)
                self.assertEqual(r.frame_count, r.time * 7.5, f)
                self.assertEqual(r.body[:20], struct.pack("<5I", 1, 1, 0, 0, 0), f)
                fr = r.frames()
                self.assertEqual(len(fr), r.frame_count)
                used = 0
                for i, x in enumerate(fr):
                    n = sum(c * c for c in x[:4])
                    # every frame is either empty (after the end of the recording) or a unit quaternion + position
                    self.assertTrue(n == 0.0 or abs(n - 1.0) < 0.01, (f, n))
                    self.assertTrue(all(abs(c) < 1000 for c in x[4:7]))
                    if n:
                        used += 1
                        self.assertEqual(x[7] - fr[0][7], 36 * i, (f, i))  # the address field advances by one frame
                        self.assertTrue(x[8] % 50 == 0, (f, x[8]))
                self.assertEqual(used, r.recorded_frames, f)
                self.assertTrue(all(not any(struct.pack("<7f", *x[:7])) for x in fr[r.recorded_frames:]), f)  # (address stays)
                self.assertLess(r.recorded_seconds, r.time, f)

    def test_bum_samples(self):
        files = real(".bum")
        if not files:
            self.skipTest("no extracted game files")
        for f in files:
            b = mf.Bum.parse(f.read_bytes())
            self.assertEqual(len(b.samples), 21504, f)
            self.assertTrue(max(abs(s) for s in b.samples) > 1000, f)

    def test_cmp(self):
        files = real(".cmp")
        if not files:
            self.skipTest("no extracted game files")
        for f in files:
            data = f.read_bytes()
            raw = mf.cmp_decompress(data)
            self.assertEqual(mf.cmp_compress(raw), data)
            self.assertEqual(struct.unpack_from("<I", raw, 0)[0], 0x23)  # a PITexDict (see FORMATS.md)

    def test_rwf_semantics(self):
        files = real(".rwf")
        if not files:
            self.skipTest("no extracted game files")
        for f in files:
            r = mf.Rwf.parse(f.read_bytes())
            self.assertEqual((r.version, r.type), (0x01000001, 0), f)
            self.assertTrue(r.textures, f)
            for g in r.glyphs:
                self.assertTrue(0.0 <= g.u0 < g.u1 <= 1.0 and 0.0 <= g.v0 < g.v1 <= 1.0, (f, g))
                self.assertLess(g.texture, len(r.textures), f)
            for i in r.low_map + r.high_map:
                self.assertTrue(i == 0xFFFF or i < len(r.glyphs), f)
            if f.name == "FGDC20.RWF":  # the ASCII font: 'A'..'Z' exist, the glyph of 'A' is not wider than tall + margin
                for c in range(ord("A"), ord("Z") + 1):
                    self.assertIsNotNone(r.glyph_for(c), c)
                self.assertEqual(r.textures, ["fgdc20"])

    def test_ai_semantics(self):
        files = real(".ai")
        if not files:
            self.skipTest("no extracted game files")
        self.assertEqual(mf.AI_LINE_NAMES[0], "race lines")
        for f in files:
            ai = mf.Ai.parse(f.read_bytes())
            # the cell values of all tiles in use are the editor brush values 0..3 or empty (0xFF)
            for t in ai.used_tiles():
                self.assertTrue(set(ai.tiles[t * 64:(t + 1) * 64]) <= {0, 1, 2, 3, 0xFF}, (f, t))
            # the fallback route (race lines, slot 0) exists in every track
            self.assertGreaterEqual(len(ai.paths[0][0]), 4, f)
            # route points stay within the 1024 x 1024 unit area covered by the grid (+-512)
            for s in ai.paths:
                for slot in s:
                    for x, z in slot:
                        self.assertTrue(-600 < x < 600 and -600 < z < 600, (f, x, z))

    def test_ai_value_ranges(self):
        files = real(".ai")
        if not files:
            self.skipTest("no extracted game files")
        for f in files:
            ai = mf.Ai.parse(f.read_bytes())
            self.assertTrue(0 <= ai.max_tile < mf.AI_TILES, f)
            self.assertTrue(all(-1 <= t < mf.AI_TILES for t in ai.grid), f)
            for rs in ai.path_raw:
                for pts, cnt in rs:
                    self.assertTrue(0 <= cnt <= mf.AI_PATH_POINTS, f)
                    for x, z in pts[:cnt]:
                        self.assertTrue(-1000 < x < 1000 and -1000 < z < 1000, (f, x, z))


if __name__ == "__main__":
    unittest.main()

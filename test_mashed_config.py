"""Tests for mashed_config.py. Run: python -m unittest -v"""
import os, struct, tempfile, unittest
from pathlib import Path

import mashed_config as mc
import mashed_formats as mf

GAME = Path(os.environ.get("PIZ_GAME_DIR", Path(__file__).parent.parent / "MashedGame"))
MFL = GAME / "FullyLoaded/installed/App_Executables/MFL.exe"


class Video(unittest.TestCase):
    def test_fields_roundtrip(self):
        v = mc.VideoCfg(bytes(0x200))
        v.version, v.subsystem, v.mode_name = 5, "Primary Display Driver", "800 x 600 x 32"
        v.mode_index, v.width, v.height, v.depth, v.fullscreen = 3, 800, 600, 32, 1
        v.trilinear = 1
        raw = v.build()
        self.assertEqual(len(raw), 0x200)
        w = mc.VideoCfg.parse(raw)
        self.assertEqual((w.subsystem, w.mode_name, w.width, w.height, w.depth, w.fullscreen, w.trilinear),
                         ("Primary Display Driver", "800 x 600 x 32", 800, 600, 32, 1, 1))
        self.assertEqual(w.build(), raw)
        self.assertIn("800x600x32", mc.summary(w))
        self.assertEqual(struct.unpack_from("<I", raw, 0x188)[0], 3)   # offsets as read from the game

    def test_size_and_text(self):
        with self.assertRaises(ValueError):
            mc.VideoCfg.parse(bytes(10))
        with self.assertRaises(ValueError):
            mc.VideoCfg(bytes(0x200)).__setattr__("subsystem", "x" * 80)


class Controller(unittest.TestCase):
    def test_defaults_and_binding(self):
        k = mc.default_controller(mc.DEVICE_KEYBOARD, "Keyboard")
        self.assertEqual(k.bindings["accelerate"], 0x1F)
        self.assertEqual((k.bindings["left"], k.bindings["down"]), (0xCB, 0xD0))
        k.set_binding("fire", 0x39)
        c = mc.ControllerCfg.parse(k.build())
        self.assertEqual((c.version, c.device, c.name, c.bindings["fire"]), (6, 2, "Keyboard", 0x39))
        g = mc.default_controller(mc.DEVICE_GAMEPAD, "Joy", 1)
        self.assertEqual(list(g.bindings.values())[:9], list(mc.GAMEPAD_DEFAULT))
        self.assertEqual(g.joystick, 1)
        self.assertEqual(len(g.build()), 0x200)


class Save(unittest.TestCase):
    def test_new_save(self):
        s = mc.new_save(b"abc")
        self.assertEqual(len(s.build()), 0x42334)
        t = mc.SaveGame.parse(s.build())
        self.assertEqual(t.replay_buffer[:4], b"abc\0")
        self.assertEqual(len(t.replay_buffer), 0x4178C)
        t.best_minutes = [9] * 45
        t.best_seconds = [59] * 45
        t.best_fraction = [0.99] * 45
        t.mirror_mode = 1
        u = mc.SaveGame.parse(t.build())
        self.assertEqual(u.best_times()[44][:2], (9, 59))
        self.assertAlmostEqual(u.best_times()[0][2], 0.99, 5)
        self.assertEqual(u.mirror_mode, 1)
        self.assertEqual(len(u.progress), 15)
        self.assertEqual(len(u.progress[0]), 18)

    def test_layout(self):
        self.assertEqual(mc.SaveGame.BUFFER + mc.SaveGame.BUFFER_SIZE, mc.SaveGame.STATE)
        self.assertEqual(mc.SaveGame.STATE + mc.SaveGame.STATE_SIZE + mc.SaveGame.TRAILER, mc.SAVE_SIZE)
        self.assertEqual(0x438, 15 * 18 * 4)
        self.assertEqual(0x438 + 15 * 20, 0x564)
        self.assertEqual(0x7BC + 3 * 45 * 4, 0x9D8)
        self.assertEqual(0x9D8 + 15 * 18, 0xAE6)

    def test_rejects_bad(self):
        with self.assertRaises(ValueError):
            mc.SaveGame.parse(bytes(0x42334))   # no magic
        with self.assertRaises(ValueError):
            mc.SaveGame.parse(b"\xef\xbe\xad\xde")

    def test_replay_buffer_is_rep(self):
        """the ghost buffer is a list of REP records (what the game saves with FUN_004957f0)."""
        reps = sorted(GAME.glob("FullyLoaded/extracted/**/BEST266.REP"))
        if not reps:
            self.skipTest("no game files")
        rep = mf.Rep.parse(reps[0].read_bytes())
        data = reps[0].read_bytes()
        s = mc.new_save(data[:mc.SaveGame.BUFFER_SIZE])
        again = mf.Rep.parse(mc.SaveGame.parse(s.build()).replay_buffer)
        self.assertEqual(len(again.records), len(rep.records))


@unittest.skipUnless(MFL.exists(), "no installed game files")
class Executable(unittest.TestCase):
    """the sizes the code passes to fread/fwrite."""
    def test_constants_in_exe(self):
        d = MFL.read_bytes()
        self.assertIn(b"contcfg%d.bin\0", d)
        self.assertIn(b"videocfg.bin\0", d)
        self.assertIn(b"gamesave.bin\0", d)
        self.assertIn(b"\x68\x34\x23\x04\x00", d)   # push 0x42334 (save size)


class Cli(unittest.TestCase):
    def test_dump(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "videocfg.bin"
            p.write_bytes(bytes(0x200))
            self.assertEqual(mc.main(["x", "dump", str(p)]), 0)
            q = Path(tmp) / "gamesave.bin"
            q.write_bytes(mc.new_save().build())
            self.assertEqual(mc.main(["x", "dump", str(q)]), 0)
            self.assertEqual(mc.main(["x", "dump", str(Path(tmp) / "other.bin")]), 1)


if __name__ == "__main__":
    unittest.main()

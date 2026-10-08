"""Tests for lua_api.py. Run: python -m unittest -v"""
import os, struct, tempfile, unittest
from pathlib import Path

import lua_api

GAME = Path(os.environ.get("PIZ_GAME_DIR", Path(__file__).parent.parent / "MashedGame"))
MFL = GAME / "FullyLoaded/installed/App_Executables/MFL.exe"
MASHED = GAME / "DriveToSurvive/Mashed/installed/App_Executables/MASHED.exe"


def fake_exe(names):
    """A minimal PE with a .text section holding the register pattern and a .rdata section with the strings."""
    base = 0x400000
    strings = b"".join(n.encode() + b"\0" for n in names)
    offs, pos = [], 0
    for n in names:
        offs.append(pos)
        pos += len(n) + 1
    rdata_va = 0x2000
    text = bytearray()
    helper_va = base + 0x1000 + 0x400   # an arbitrary helper inside .text
    for i, n in enumerate(names):
        at = base + 0x1000 + len(text)
        text += b"\x68" + struct.pack("<I", base + 0x1000 + 0x500 + 0x10 * i) + b"\x68" + struct.pack("<I", base + rdata_va + offs[i])
        text += b"\xe8" + struct.pack("<i", helper_va - (at + 10 + 5))
    text += b"\x90" * (0x1000 - len(text))
    pe = bytearray(0x400)
    pe[0:2] = b"MZ"
    struct.pack_into("<I", pe, 0x3C, 0x80)
    pe[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", pe, 0x80 + 6, 2)          # sections
    struct.pack_into("<H", pe, 0x80 + 20, 0xE0)      # optional header size
    struct.pack_into("<I", pe, 0x80 + 24 + 28, base)  # image base (optional header offset 28)
    so = 0x80 + 24 + 0xE0
    pe[so:so + 8] = b".text\0\0\0"
    struct.pack_into("<IIII", pe, so + 8, 0x1000, 0x1000, 0x1000, 0x400)
    pe[so + 40:so + 48] = b".rdata\0\0"
    struct.pack_into("<IIII", pe, so + 40 + 8, 0x1000, rdata_va, len(strings), 0x400 + 0x1000)
    return bytes(pe) + bytes(text) + strings


class Synthetic(unittest.TestCase):
    def test_registered(self):
        names = ["Clump_Filename", "Mts_Filename", "Place_Powerup"]
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.exe"
            p.write_bytes(fake_exe(names))
            r, helper = lua_api.registered(p)
            self.assertEqual(sorted(r), sorted(names))
            self.assertEqual(helper, 0x400000 + 0x1000 + 0x400)
            self.assertEqual(r["Mts_Filename"], 0x400000 + 0x1000 + 0x500 + 0x10)
            self.assertEqual(lua_api.main(["x", "list", str(p)]), 0)
            (Path(tmp) / "a.lua").write_text('Clump_Filename(0,"a.dff")\n-- Place_Powerup()\nMts_Filename(1, "b.mts")\nlocal_function(3)\n')
            self.assertEqual(lua_api.main(["x", "check", str(p), str(Path(tmp) / "a.lua")]), 0)
            self.assertEqual(lua_api.main(["x", "doc", str(Path(tmp) / "o.md"), str(p), str(p), str(Path(tmp) / "a.lua")]), 0)
            md = (Path(tmp) / "o.md").read_text()
            self.assertIn("| `Clump_Filename` | 0x401500 |", md)
            self.assertIn("(int, string)", md)
            self.assertIn("| `Place_Powerup` |", md)

    def test_no_registrations(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.exe"
            e = bytearray(fake_exe(["Abcd"]))
            e[0x400:0x400 + 0x1000] = b"\x90" * 0x1000
            p.write_bytes(bytes(e))
            with self.assertRaises(ValueError):
                lua_api.registered(p)

    def test_arguments(self):
        self.assertEqual([lua_api.arg_kind(a) for a in ('"x"', "12", "-1.5", ".3", "OIL")], ["string", "int", "float", "float", "NAME"])
        calls = lua_api.script_calls([])
        self.assertEqual(len(calls), 0)
        self.assertEqual(lua_api.signature([("1, 2.0", "t"), ("3, 4.5", "t"), ('0, "x"', "t")]), "(int, float) or (int, string)")
        self.assertEqual(lua_api.signature([("", "t")]), "()")


@unittest.skipUnless(MFL.exists() and MASHED.exists(), "no installed game files")
class RealFiles(unittest.TestCase):
    def scripts(self):
        return sorted(p for p in GAME.glob("*/extracted/**/*") if p.suffix.lower() == ".lua")

    def test_counts_and_addresses(self):
        rm, fm = lua_api.registered(MFL)
        rd, fd = lua_api.registered(MASHED)
        self.assertEqual((len(rm), len(rd), hex(fm), hex(fd)), (71, 69, "0x48cf50", "0x47b980"))
        for r in (rm, rd):
            self.assertTrue(all(0x401000 <= a < 0x600000 for a in r.values()))   # all inside the code section
        self.assertLessEqual(set(rd) - set(rm), set())          # MASHED has no function that MFL lacks
        self.assertEqual(set(rm) - set(rd), {"ClumpFade", "ExpObj_Clump_Mts_Pair"})   # the only two functions added in Fully Loaded

    def test_every_script_function_is_registered(self):
        rm, _ = lua_api.registered(MFL)
        rd, _ = lua_api.registered(MASHED)
        calls = lua_api.script_calls(self.scripts())
        engine = [n for n in calls if n in rm]
        self.assertEqual(len(engine), 51)
        # every capitalised name called by a script is an engine function (the rest of the code is plain Lua)
        self.assertEqual([n for n in calls if n not in rm and n[0].isupper()], [])
        # scripts of the Drive to Survive tracks only use functions MASHED.exe knows
        for p in self.scripts():
            if "DriveToSurvive" in p.parts:
                for n in lua_api.script_calls([p]):
                    if n in rm:
                        self.assertIn(n, rd, (p, n))

    def test_same_function_for_course_id_and_name(self):
        rm, _ = lua_api.registered(MFL)
        self.assertEqual(rm["Course_Id"], rm["Course_Name"])   # one C function serves both names in MFL.exe


if __name__ == "__main__":
    unittest.main()

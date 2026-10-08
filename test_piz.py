"""Tests for piz.py.  Run: python -m unittest -v   (uses only the standard library)

Real-game tests run when game files are found (env PIZ_GAME_DIR, default ../MashedGame) and are skipped otherwise."""
import json, os, struct, subprocess, sys, tempfile, unittest
from pathlib import Path

import piz

HERE = Path(__file__).parent
GAME_DIR = Path(os.environ.get("PIZ_GAME_DIR", HERE.parent / "MashedGame"))


def cli(*args):
    return subprocess.run([sys.executable, str(HERE / "piz.py"), *map(str, args)], capture_output=True, text=True)


def make_dir(root, files, tail="00000000", version=3):
    """Create an 'extracted' dir with a piz.json manifest. files: {name: bytes}"""
    root.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    (root / piz.MANIFEST).write_text(json.dumps({
        "source": "t.piz", "version": version, "tail": tail,
        "files": [{"name": n, "hash": piz.name_hash(n.encode())} for n in files]}))


class FormatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def fresh(self, files, **kw):
        """A new source dir every call (leftover files would be picked up as extra files)."""
        self.n = getattr(self, "n", 0) + 1
        src = self.tmp / f"src{self.n}"
        make_dir(src, files, **kw)
        return src

    def build(self, files, **kw):
        src = self.fresh(files, **kw)
        out = self.tmp / "t.piz"
        piz.write_piz(src, out, 256)
        return out.read_bytes()

    def test_layout(self):
        files = {"A.TXT": b"x" * 5, "B.BIN": b"y" * 0x800, "C.BIN": b"z" * 0x801}
        d = self.build(files, tail="cccccccc")
        self.assertEqual(d[:4], b"PIZ\0")
        self.assertEqual(struct.unpack_from("<II", d, 4), (3, 3))
        self.assertEqual(d[12:16], b"\xcc" * 4)
        self.assertFalse(any(d[16:0x800]))
        self.assertEqual(len(d) % 0x800, 0)
        (header, entries) = piz.read_piz(d)
        self.assertEqual([(n, s) for n, _, s, _ in entries], [(n, len(v)) for n, v in files.items()])
        expect = 0x1000  # table (3*0x80) rounds up to the next 0x800 boundary after 0x800
        for (name, off, size, h), data in zip(entries, files.values()):
            self.assertEqual(off, expect)
            self.assertEqual(off % 0x800, 0)
            self.assertEqual(d[off:off + size], data)
            self.assertEqual(h, piz.name_hash(name.encode()))
            padded = (size + 0x7FF) & ~0x7FF
            self.assertFalse(any(d[off + size:off + padded]))
            expect = off + padded
        self.assertEqual(len(d), expect)

    def test_first_offset_with_large_table(self):
        files = {f"F{i}": b"d" for i in range(20)}  # table = 0xA00 bytes -> data starts at 0x1800
        _, entries = piz.read_piz(self.build(files))
        self.assertEqual(entries[0][1], 0x1800)

    def test_name_hash(self):
        self.assertEqual(piz.name_hash(b"AI0.AI"), 932)  # value seen in the real AI.piz (lower-case form)
        self.assertEqual(piz.name_hash(b"BOID23.GSD"), 2933)

    def test_empty_file_and_empty_archive(self):
        _, entries = piz.read_piz(self.build({"E.BIN": b""}))
        self.assertEqual(entries[0][2], 0)
        _, entries = piz.read_piz(self.build({}))
        self.assertEqual(entries, [])

    def test_roundtrip_via_cli_keeps_manifest_and_subdirs(self):
        files = {"PS2/X.TXD": b"ps2", "XBOX/X.TXD": b"xbox", "X.TXD": b"pc", "TOASTART/COMMON/Y.DFF": b"y" * 3000}
        orig = self.tmp / "o.piz"
        orig.write_bytes(self.build(files, tail="cccccccc"))
        out = self.tmp / "ex"
        self.assertEqual(cli("-o", out, orig).returncode, 0)
        for n, v in files.items():
            self.assertEqual((out / "o" / n).read_bytes(), v)  # no name clashes between PS2/X.TXD, XBOX/X.TXD, X.TXD
        m = json.loads((out / "o" / piz.MANIFEST).read_text())
        self.assertEqual((m["version"], m["tail"], [f["name"] for f in m["files"]]), (3, "cccccccc", list(files)))
        again = self.tmp / "again.piz"
        self.assertEqual(cli("-r", out / "o", "-o", again).returncode, 0)
        self.assertEqual(again.read_bytes(), orig.read_bytes())

    def test_extra_file_is_appended(self):
        src = self.fresh({"A.BIN": b"a"})
        (src / "NEW.BIN").write_bytes(b"new")
        out = self.tmp / "t.piz"
        piz.write_piz(src, out, 256)
        _, entries = piz.read_piz(out.read_bytes())
        self.assertEqual([e[0] for e in entries], ["A.BIN", "NEW.BIN"])

    def test_limits(self):
        src = self.fresh({f"F{i}": b"d" for i in range(129)})
        out = self.tmp / "t.piz"
        piz.write_piz(src, out, 256)  # fine for Fully Loaded
        with self.assertRaises(ValueError):
            piz.write_piz(src, out, 128)  # too many for Mashed
        self.assertNotEqual(cli("-r", src, "-o", out, "--game", "mashed").returncode, 0)
        src = self.fresh({f"F{i}": b"d" for i in range(257)})
        with self.assertRaises(ValueError):
            piz.write_piz(src, out, 256)

    def test_name_too_long(self):
        src = self.fresh({"A" * 0x74: b"x"})
        with self.assertRaises(ValueError):
            piz.write_piz(src, self.tmp / "t.piz", 256)
        src = self.fresh({"A" * 0x73: b"x"})  # exactly fits (NUL terminated in 0x74 bytes)
        piz.write_piz(src, self.tmp / "t.piz", 256)
        _, entries = piz.read_piz((self.tmp / "t.piz").read_bytes())
        self.assertEqual(entries[0][0], "A" * 0x73)

    def test_missing_listed_file(self):
        src = self.fresh({"A.BIN": b"a"})
        (src / "A.BIN").unlink()
        with self.assertRaises(OSError):
            piz.write_piz(src, self.tmp / "t.piz", 256)

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            piz.read_piz(b"NOPE" + bytes(0x800))
        d = bytearray(self.build({"A.BIN": b"abc"}))
        struct.pack_into("<I", d, 0x800 + 0x78, 10**6)  # size beyond the file
        with self.assertRaises(ValueError):
            piz.read_piz(bytes(d))
        bad = self.tmp / "bad.piz"
        bad.write_bytes(b"junk")
        self.assertEqual(cli(bad).returncode, 1)

    def test_extract_never_leaves_target_dir(self):
        d = bytearray(self.build({"A.BIN": b"abc"}))
        d[0x800:0x800 + 0x74] = b"../../evil.bin".ljust(0x74, b"\0")
        evil = self.tmp / "evil.piz"
        evil.write_bytes(bytes(d))
        out = self.tmp / "ex"
        self.assertEqual(cli("-o", out, evil).returncode, 0)
        self.assertEqual((out / "evil" / "evil.bin").read_bytes(), b"abc")
        self.assertFalse((self.tmp / "evil.bin").exists())
        self.assertEqual(piz.safe_path("a\\b/./c").as_posix(), "a/b/c")
        with self.assertRaises(ValueError):
            piz.safe_path("..")

    def test_preview_writes_nothing_and_lists_files(self):
        p = self.tmp / "p.piz"
        p.write_bytes(self.build({"A.BIN": b"abc", "DIR/B.BIN": b"d"}))
        before = set(self.tmp.iterdir())
        r = cli(p)
        self.assertEqual(r.returncode, 0)
        self.assertIn("2 files", r.stdout)
        self.assertIn("A.BIN", r.stdout)
        self.assertIn("DIR/B.BIN", r.stdout)
        self.assertEqual(set(self.tmp.iterdir()), before)


@unittest.skipUnless(list(GAME_DIR.rglob("*.piz")) if GAME_DIR.is_dir() else False, f"no game .piz files under {GAME_DIR}")
class RealArchiveTests(unittest.TestCase):
    """Every shipped archive must survive extract -> repack byte for byte."""

    def test_all_archives_roundtrip(self):
        archives = sorted(p for p in GAME_DIR.rglob("*") if p.suffix.lower() == ".piz" and "extracted" not in p.parts)
        self.assertTrue(archives)
        with tempfile.TemporaryDirectory() as tmp:
            for i, a in enumerate(archives):
                out = Path(tmp) / str(i)
                r = cli("-o", out, a)
                self.assertEqual(r.returncode, 0, f"{a}: {r.stderr}")
                again = Path(tmp) / f"{i}.piz"
                r = cli("-r", out / a.stem, "-o", again)
                self.assertEqual(r.returncode, 0, f"{a}: {r.stderr}")
                self.assertTrue(again.read_bytes() == a.read_bytes(), f"{a} differs after repack")

    def test_every_entry_matches_the_format_rules(self):
        for a in GAME_DIR.rglob("*"):
            if a.suffix.lower() != ".piz" or "extracted" in a.parts:
                continue
            d = a.read_bytes()
            _, entries = piz.read_piz(d)
            self.assertEqual(len(d) % 0x800, 0, a)
            pos = (0x800 + len(entries) * 0x80 + 0x7FF) & ~0x7FF
            for name, off, size, h in entries:
                self.assertEqual(off, pos, f"{a} {name}")
                pos = (off + size + 0x7FF) & ~0x7FF
            self.assertEqual(pos, len(d), a)


if __name__ == "__main__":
    unittest.main()

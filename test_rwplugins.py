"""Tests for the plugin decoders of rwscene.py (user data, MatFX, collision tree, morph, HAnim, UV animation reference).
Run: python -m unittest -v"""
import os, struct, unittest
from pathlib import Path

import rwscene as rs

GAME = Path(os.environ.get("PIZ_GAME_DIR", Path(__file__).parent.parent / "MashedGame"))
ST = 0x1C02000A


def chunk(t, payload, stamp=ST):
    return struct.pack("<III", t, len(payload), stamp) + payload


def user_data(arrays):
    out = struct.pack("<I", len(arrays))
    for name, vals in arrays:
        nm = name.encode() + b"\0"
        nm += b"\0" * (-len(nm) % 4)
        out += struct.pack("<I", len(nm)) + nm
        if isinstance(vals[0], str):
            out += struct.pack("<II", rs.STRING_DATA, len(vals))
            for v in vals:
                b = v.encode() + b"\0"
                b += b"\0" * (-len(b) % 4)
                out += struct.pack("<I", len(b)) + b
        elif isinstance(vals[0], float):
            out += struct.pack("<II", rs.FLOAT_DATA, len(vals)) + struct.pack(f"<{len(vals)}f", *vals)
        else:
            out += struct.pack("<II", rs.INT_DATA, len(vals)) + struct.pack(f"<{len(vals)}i", *vals)
    return out


def texture(name):
    nm = name.encode() + b"\0" * (4 - len(name) % 4)
    return chunk(rs.TEXTURE, chunk(rs.STRUCT, struct.pack("<I", 0x1106)) + chunk(rs.STRING, nm) + chunk(rs.STRING, b"\0\0\0\0") + chunk(rs.EXTENSION, b""))


class Plugins(unittest.TestCase):
    def test_user_data(self):
        p = user_data([("0.tv_part_id", [91, 91, 80]), ("JaCFX.baseTexture", ["ReflectedSky"]), ("scale", [1.5, 2.0])])
        d = rs.read_user_data(p)
        self.assertEqual(d, {"0.tv_part_id": [91, 91, 80], "JaCFX.baseTexture": ["ReflectedSky"], "scale": [1.5, 2.0]})
        with self.assertRaises(ValueError):
            rs.read_user_data(p + b"\0\0\0\0")
        bad = bytearray(p)
        struct.pack_into("<I", bad, 4 + 4 + 16, 9)   # format of the first array
        with self.assertRaises(ValueError):
            rs.read_user_data(bytes(bad))

    def test_matfx(self):
        # dual texture effect with a texture, second slot empty
        p = struct.pack("<I", 4) + struct.pack("<I", 4) + struct.pack("<III", 5, 6, 1) + texture("lightmap") + struct.pack("<I", 0)
        first, eff = rs.read_matfx(p)
        self.assertEqual((first, eff[0].type, eff[0].src_blend, eff[0].dst_blend, eff[0].textures, eff[1].type), (4, 4, 5, 6, ("lightmap",), 0))
        # environment map
        p = struct.pack("<II", 2, 2) + struct.pack("<fI", 0.5, 1) + struct.pack("<I", 1) + texture("sky") + struct.pack("<I", 0)
        first, eff = rs.read_matfx(p)
        self.assertEqual((eff[0].coefficient, eff[0].frame_buffer_alpha, eff[0].textures), (0.5, 1, ("sky",)))
        # uv transform: nothing follows the type
        first, eff = rs.read_matfx(struct.pack("<III", 5, 5, 0))
        self.assertEqual((first, eff[0].type, eff[1].type), (5, 5, 0))
        # bump + env: bump first (coefficient, bump texture, texture), then environment map
        p = (struct.pack("<II", 3, 1) + struct.pack("<f", 2.0) + struct.pack("<I", 0) + struct.pack("<I", 0)
             + struct.pack("<I", 2) + struct.pack("<fI", 1.0, 0) + struct.pack("<I", 0))
        first, eff = rs.read_matfx(p)
        self.assertEqual((eff[0].type, eff[0].coefficient, eff[1].type), (1, 2.0, 2))
        with self.assertRaises(ValueError):
            rs.read_matfx(struct.pack("<III", 5, 5, 0) + b"\0")
        self.assertEqual(rs.MATFX_NAMES[4], "dual texture")

    def test_colltree(self):
        ent, splits = 5, 2
        s = struct.pack("<I6fII", 1, -1, -2, -3, 1, 2, 3, ent, splits)
        s += struct.pack("<BBHf", 0x09, 0xFF, 1, 0.5) + struct.pack("<BBHf", 0x08, 0xFF, 2, 0.25)
        s += struct.pack("<BBHf", 0x00, 3, 0, 0.0) + struct.pack("<BBHf", 0x01, 2, 3, 0.0)
        s += struct.pack("<5H", 4, 3, 2, 1, 0)
        payload = struct.pack("<I", 0x37002) + chunk(0x2C, chunk(rs.STRUCT, s))
        t = rs.read_colltree(payload)
        self.assertEqual((t.flags, t.num_entries, len(t.splits), t.map), (1, 5, 2, [4, 3, 2, 1, 0]))
        self.assertEqual(t.splits[0][0], (9, 255, 1, 0.5))
        self.assertEqual(t.splits[1][1], (1, 2, 3, 0.0))
        self.assertEqual(t.bbox, (-1.0, -2.0, -3.0, 1.0, 2.0, 3.0))
        with self.assertRaises(ValueError):
            rs.read_colltree(struct.pack("<I", 0x1234) + chunk(0x2C, chunk(rs.STRUCT, s)))
        with self.assertRaises(ValueError):
            rs.read_colltree(payload[:-2] + b"\0\0\0\0")

    def test_morph_hanim_uvanim(self):
        m = rs.read_morph(struct.pack("<I", 1) + struct.pack("<Iiiff", 0, 0, 1, 0.5333, 0.0))
        self.assertEqual(m[0][:3], (0, 0, 1))
        self.assertAlmostEqual(m[0][3], 0.5333, places=4)
        self.assertEqual(rs.read_morph(struct.pack("<I", 0)), [])
        with self.assertRaises(ValueError):
            rs.read_morph(struct.pack("<I", 2) + bytes(20))
        self.assertEqual(rs.read_hanim_frame(struct.pack("<IiI", 0x100, -1, 0)), (-1, 0, b""))
        self.assertEqual(rs.read_hanim_frame(struct.pack("<IiI", 0x100, 3, 0))[0], 3)
        with self.assertRaises(ValueError):
            rs.read_hanim_frame(struct.pack("<IiI", 0x200, -1, 0))
        ref = chunk(rs.STRUCT, struct.pack("<I", 1) + b"BMP_Arrows_M".ljust(32, b"\0"))
        self.assertEqual(rs.read_uvanim_ref(ref), "BMP_Arrows_M")

    def test_material_and_geometry_get_plugins(self):
        mat_ext = chunk(rs.EXTENSION, chunk(rs.USERDATA, user_data([("JaCFX.cfxType", [4])]))
                        + chunk(rs.MATFX, struct.pack("<III", 5, 5, 0)) + chunk(0x135, chunk(rs.STRUCT, struct.pack("<I", 1) + b"ANIM".ljust(32, b"\0"))))
        s = struct.pack("<I4sIi3f", 0, bytes((1, 2, 3, 4)), 0, 0, 1.0, 1.0, 1.0)
        ml = chunk(rs.MATLIST, chunk(rs.STRUCT, struct.pack("<ii", 1, -1)) + chunk(rs.MATERIAL, chunk(rs.STRUCT, s) + mat_ext))
        mats = rs.read_material_list(rs.Tree.parse(ml))
        self.assertEqual(mats[0].user, {"JaCFX.cfxType": [4]})
        self.assertEqual((mats[0].matfx[0], mats[0].uv_anim), (5, "ANIM"))


def real(ext):
    return sorted(p for p in GAME.glob("*/extracted/**/*") if p.suffix.lower() == ext and p.is_file())


@unittest.skipUnless(list(GAME.glob("*/extracted")), "no extracted game files")
class RealFiles(unittest.TestCase):
    def test_dff_plugins(self):
        keys, fx, nuv = set(), set(), 0
        for f in real(".dff"):
            d = rs.Dff.parse(f.read_bytes())
            for g in d.geometries:
                keys |= {("geo", k) for k in g.user}
                for k, v in g.user.items():
                    if k in ("0.tv_part_id", "0.Part_ID"):
                        self.assertIn(len(v), (1, len(g.triangles)), (f, k))  # one id for the geometry or one per triangle
                for it in g.morph:
                    self.assertTrue(0 <= it[1] < 2 and it[3] > 0, f)
                for m in g.materials:
                    keys |= {("mat", k) for k in m.user}
                    if m.matfx:
                        fx.add(rs.MATFX_NAMES[m.matfx[0]])
                    nuv += bool(m.uv_anim)
                    if "JaCFX.cfxType" in m.user:
                        self.assertIn(m.user["JaCFX.cfxType"][0], (1, 2, 3, 4), f)
                        self.assertIn("JaCFX.baseTexture", m.user, f)
            for fr in d.frames:
                if fr.hanim:
                    self.assertTrue(fr.hanim[0] >= -1 and fr.hanim[1] >= 0, f)
        self.assertEqual(keys, {("geo", "0.tv_part_id"), ("geo", "FVF.UserData"), ("geo", "0.Part_ID"), ("mat", "JaCFX.baseTexture"),
                                ("mat", "JaCFX.cfxType"), ("mat", "JaCFX.cfxBlend")} | ({("geo", "FVF.UserData")}))
        self.assertTrue({"dual texture", "uv transform", "environment map", "bump + environment map"} <= fx, fx)
        self.assertGreater(nuv, 100)

    def test_uv_references_exist_in_uva_files(self):
        """Every material's UV animation name is the name of an animation in a UVA file of the same folder."""
        checked, missing = 0, set()
        for f in real(".dff") + real(".bsp"):
            obj = rs.PARSERS[f.suffix.lower()].parse(f.read_bytes())
            mats = [m for g in obj.geometries for m in g.materials] if isinstance(obj, rs.Dff) else obj.materials
            names = {m.uv_anim for m in mats if m.uv_anim}
            if not names:
                continue
            known = set()
            for u in f.parent.glob("*"):
                if u.suffix.lower() == ".uva":
                    known |= {a.name.lower() for a in rs.UvAnimDict.parse(u.read_bytes()).animations}
            known |= {a.name.lower() for u in real(".uva") for a in rs.UvAnimDict.parse(u.read_bytes()).animations}
            for n in names:
                checked += 1
                if n.lower() not in known:
                    missing.add((f.name, n))
        self.assertGreater(checked, 30)
        # all references resolve except two that dangle in the shipped data (animations that are in no .UVA file)
        self.assertEqual(missing, {("RIVER.DFF", "bmp_river_M"), ("SKYDOME02.DFF", "skyDome3_M")})

    def test_collision_trees(self):
        n = 0
        for f in real(".bsp"):
            b = rs.Bsp.parse(f.read_bytes())
            for s in b.sectors:
                t = s.colltree
                if t is None:
                    continue
                n += 1
                self.assertEqual(t.num_entries, s.num_triangles, f)
                self.assertEqual(t.flags, 1, f)
                self.assertEqual(sorted(t.map), list(range(t.num_entries)), f)   # the map is a permutation of the triangles
                self.assertEqual(len(t.map), t.num_entries)
                for left, right in t.splits:
                    for typ, contents, index, value in (left, right):
                        self.assertEqual(typ & ~0x0D, 0, f)             # only the negative flag (1) and the axis bits (0, 4, 8)
                        self.assertIn(typ & 0x0C, (0, 4, 8), f)
                        if contents != 0xFF:
                            self.assertLessEqual(index + contents, t.num_entries, f)  # a leaf names a slice of the entries
                        else:
                            self.assertLess(index, len(t.splits) + 1, f)
        self.assertEqual(n, 217)

    def test_sector_user_data(self):
        n = 0
        for f in real(".bsp"):
            for s in rs.Bsp.parse(f.read_bytes()).sectors:
                if s.user:
                    n += 1
                    self.assertEqual(s.user, {"FVF.UserData": [12291]}, f)
        self.assertEqual(n, 9)


if __name__ == "__main__":
    unittest.main()

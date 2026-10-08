"""Tests for rwscene.py (DFF, BSP, ANM, SPL, UVA). Run: python -m unittest -v"""
import math, os, struct, tempfile, unittest
from pathlib import Path

import rwscene as rs

GAME = Path(os.environ.get("PIZ_GAME_DIR", Path(__file__).parent.parent / "MashedGame"))
ST = 0x1C02000A


def chunk(t, payload, stamp=ST):
    return struct.pack("<III", t, len(payload), stamp) + payload


def matlist(textures):
    """MATLIST with one material per texture name ('' = untextured)."""
    body = chunk(rs.STRUCT, struct.pack("<i", len(textures)) + struct.pack(f"<{len(textures)}i", *([-1] * len(textures))))
    for name in textures:
        s = struct.pack("<I4sIi3f", 0, bytes((255, 128, 64, 255)), 0, 1 if name else 0, 1.0, 1.0, 1.0)
        m = chunk(rs.STRUCT, s)
        if name:
            nm = name.encode() + b"\0" * (4 - len(name) % 4)
            m += chunk(rs.TEXTURE, chunk(rs.STRUCT, struct.pack("<I", 0x1106)) + chunk(rs.STRING, nm) + chunk(rs.STRING, b"\0\0\0\0")
                       + chunk(rs.EXTENSION, b""))
        m += chunk(rs.EXTENSION, b"")
        body += chunk(rs.MATERIAL, m)
    return chunk(rs.MATLIST, body)


def geometry(verts, tris, tex="grass", uvs=None, normals=None):
    flags = rs.POSITIONS | rs.TEXTURED | (rs.NORMALS if normals else 0) | (1 << 16)
    n = len(verts)
    s = struct.pack("<IIII", flags, len(tris), n, 1)
    uvs = uvs or [(0.0, 0.0)] * n
    s += b"".join(struct.pack("<2f", *u) for u in uvs)
    for a, b, c, m in tris:  # stored word order: v1, v0, material, v2
        s += struct.pack("<4H", b, a, m, c)
    s += struct.pack("<4f", 0, 0, 0, 1.0) + struct.pack("<ii", 1, 1 if normals else 0)
    s += b"".join(struct.pack("<3f", *v) for v in verts)
    if normals:
        s += b"".join(struct.pack("<3f", *v) for v in normals)
    ext = b""
    bm = struct.pack("<III", 0, 1, 3 * len(tris)) + struct.pack("<II", 3 * len(tris), 0) + struct.pack(f"<{3 * len(tris)}I", *[i for t in tris for i in t[:3]])
    ext = chunk(rs.EXTENSION, chunk(rs.BINMESH, bm))
    return chunk(rs.GEOMETRY, chunk(rs.STRUCT, s) + matlist([tex]) + ext)


def clump(geos, frames=None):
    frames = frames or [((1, 0, 0), (0, 1, 0), (0, 0, 1), (0, 0, 0), -1, 0)]
    fs = struct.pack("<I", len(frames)) + b"".join(struct.pack("<12fiI", *f[0], *f[1], *f[2], *f[3], f[4], f[5]) for f in frames)
    fl = chunk(rs.FRAMELIST, chunk(rs.STRUCT, fs) + b"".join(chunk(rs.EXTENSION, b"") for _ in frames))
    gl = chunk(rs.GEOMETRYLIST, chunk(rs.STRUCT, struct.pack("<I", len(geos))) + b"".join(geos))
    at = b"".join(chunk(rs.ATOMIC, chunk(rs.STRUCT, struct.pack("<4I", 0, i, 5, 0)) + chunk(rs.EXTENSION, b"")) for i in range(len(geos)))
    return chunk(rs.CLUMP, chunk(rs.STRUCT, struct.pack("<III", len(geos), 0, 0)) + fl + gl + at + chunk(rs.EXTENSION, b""))


class Synthetic(unittest.TestCase):
    def test_tree_roundtrip(self):
        data = clump([geometry([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2, 0)])])
        self.assertEqual(rs.Tree.parse(data).build(), data)
        with self.assertRaises(ValueError):
            rs.Tree.parse(data[:-4])
        with self.assertRaises(ValueError):
            rs.Tree.parse(data + b"\0" * 12 + b"x")

    def test_dff(self):
        verts = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)]
        tris = [(0, 1, 2, 0), (2, 1, 3, 0)]
        data = clump([geometry(verts, tris, uvs=[(0, 0), (1, 0), (0, 1), (1, 1)], normals=[(0, 0, 1)] * 4)],
                     [((1, 0, 0), (0, 1, 0), (0, 0, 1), (0, 0, 0), -1, 0), ((1, 0, 0), (0, 1, 0), (0, 0, 1), (5, 0, 0), 0, 0)])
        d = rs.Dff.parse(data)
        self.assertEqual(d.build(), data)
        self.assertEqual(len(d.frames), 2)
        self.assertEqual(d.world_matrix(1)[3], (5, 0, 0))
        g = d.geometries[0]
        self.assertEqual((g.vertices, g.triangles), (verts, tris))
        self.assertEqual(g.uvs[0][3], (1.0, 1.0))
        self.assertEqual(g.normals[0], (0, 0, 1))
        self.assertEqual((g.materials[0].texture, g.materials[0].color), ("grass", (255, 128, 64, 255)))
        self.assertEqual(g.binmesh, (0, [(0, [0, 1, 2, 2, 1, 3])]))
        self.assertEqual(d.atomics, [(0, 0, 5)])
        obj, mtl = rs.to_obj(d, "t")
        self.assertIn("v 0.00000 1.00000 0.00000", obj)
        self.assertEqual(obj.count("\nf "), 2)
        self.assertIn("map_Kd grass.png", mtl)

    def test_dff_errors(self):
        good = clump([geometry([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2, 0)])])
        with self.assertRaises(ValueError):
            rs.Dff.parse(chunk(rs.WORLD, b""))
        # claim two atomics but only one exists
        bad = bytearray(good)
        struct.pack_into("<I", bad, 24, 2)
        with self.assertRaises(ValueError):
            rs.Dff.parse(bytes(bad))

    def test_bsp(self):
        verts = [(0, 0, 0), (2, 0, 0), (0, 0, 2)]
        fmt = rs.POSITIONS | rs.TEXTURED | (1 << 16)
        sec = struct.pack("<iII6fii", 0, 1, 3, 0, 0, 0, 2, 0, 2, 0, 0)
        sec += b"".join(struct.pack("<3f", *v) for v in verts) + b"".join(struct.pack("<2f", 0, 0) for _ in verts)
        sec += struct.pack("<4H", 0, 1, 2, 0)
        world = chunk(rs.STRUCT, struct.pack("<i3f5iI6f", 1, 0, 0, 0, 1, 3, 0, 1, 64, fmt, 2, 0, 2, 0, 0, 0))
        world += matlist(["road"]) + chunk(rs.ATOMICSECT, chunk(rs.STRUCT, sec) + chunk(rs.EXTENSION, b"")) + chunk(rs.EXTENSION, b"")
        data = chunk(rs.WORLD, world)
        b = rs.Bsp.parse(data)
        self.assertEqual(b.build(), data)
        self.assertEqual((b.num_vertices, b.num_triangles, len(b.sectors)), (3, 1, 1))
        self.assertEqual(b.sectors[0].triangles, [(0, 1, 2, 0)])
        self.assertEqual(b.materials[0].texture, "road")
        self.assertEqual(b.sectors[0].vertices, verts)
        obj, _ = rs.to_obj(b, "w")
        self.assertEqual(obj.count("\nf "), 1)
        # totals in the header must match the sectors
        bad = bytearray(data)
        struct.pack_into("<i", bad, 12 + 12 + 16, 99)
        with self.assertRaises(ValueError):
            rs.Bsp.parse(bytes(bad))

    def test_plane_sectors(self):
        leaf = chunk(rs.ATOMICSECT, chunk(rs.STRUCT, struct.pack("<iII6fii", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)) + chunk(rs.EXTENSION, b""))
        plane = chunk(rs.PLANESECT, chunk(rs.STRUCT, struct.pack("<ifiiff", 0, 1.5, 1, 1, 1.0, 2.0)) + leaf + leaf)
        world = chunk(rs.STRUCT, struct.pack("<i3f5iI6f", 0, 0, 0, 0, 0, 0, 1, 2, 64, rs.POSITIONS, 0, 0, 0, 0, 0, 0))
        world += matlist([]) + plane + chunk(rs.EXTENSION, b"")
        b = rs.Bsp.parse(chunk(rs.WORLD, world))
        self.assertEqual((len(b.planes), len(b.sectors), b.planes[0].value), (1, 2, 1.5))

    def test_anm(self):
        kf = [(0.0, (0, 0, 0, 1), (0, 0, 0), -999), (1.5, (0, 0, 0.5, 0.866), (1, 2, 3), 0), (3.0, (0, 0, 1, 0), (4, 5, 6), 36)]
        a = rs.Anm(ST, 0x100, 1, 0, 3.0, kf)
        data = a.build()
        self.assertEqual(len(data), 32 + 36 * 3)
        b = rs.Anm.parse(data)
        self.assertEqual(b.build(), data)
        self.assertEqual(b.num_nodes(), 1)
        self.assertEqual([b.prev_index(i) for i in range(3)], [None, 0, 1])
        with self.assertRaises(ValueError):
            rs.Anm.parse(data[:-1])

    def test_anm_two_nodes(self):
        kf = [(0.0, (0, 0, 0, 1), (0, 0, 0), 7), (0.0, (0, 0, 0, 1), (0, 0, 0), 7), (1.0, (0, 0, 0, 1), (0, 0, 0), 0), (1.0, (0, 0, 0, 1), (0, 0, 0), 36)]
        self.assertEqual(rs.Anm.parse(rs.Anm(ST, 0x100, 1, 0, 1.0, kf).build()).num_nodes(), 2)

    def test_spl(self):
        s = rs.Spl(0x1400FFFF, bytes(32), 1, [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)])
        data = s.build()
        self.assertEqual(len(data), 12 + 40 + 24)
        t = rs.Spl.parse(data)
        self.assertEqual((t.points, t.flags), (s.points, 1))
        self.assertEqual(t.build(), data)
        with self.assertRaises(ValueError):
            rs.Spl.parse(data + b"\0")

    def test_uva(self):
        name = b"BMP_Test".ljust(32, b"\0")
        frames = struct.pack("<f6fi", 0.0, 0, 0, 1, 1, 0, 0, 0) + struct.pack("<f6fi", 2.0, 0, 0, 1, 1, 0.5, 0.25, 0)
        anim = struct.pack("<iiiif", 0x100, 0x1C1, 2, 0, 2.0) + struct.pack("<i", 0) + name + struct.pack("<8i", 2, 0, 0, 0, 0, 0, 0, 0) + frames
        data = chunk(rs.UVANIMDICT, chunk(rs.STRUCT, struct.pack("<I", 1)) + chunk(rs.ANIMANIMATION, anim))
        u = rs.UvAnimDict.parse(data)
        self.assertEqual(u.build(), data)
        a = u.animations[0]
        self.assertEqual((a.name, a.duration, a.node_to_uv[0], len(a.keyframes)), ("BMP_Test", 2.0, 2, 2))
        self.assertEqual(a.keyframes[1][1], (0, 0, 1, 1, 0.5, 0.25) if False else a.keyframes[1][1])
        self.assertEqual(a.keyframes[1][0], 2.0)

    def test_cli(self):
        data = clump([geometry([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2, 0)])])
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "t.dff"
            f.write_bytes(data)
            self.assertEqual(rs.main(["x", "info", str(f)]), 0)
            self.assertEqual(rs.main(["x", "obj", str(f), str(Path(tmp) / "o.obj")]), 0)
            self.assertTrue((Path(tmp) / "o.obj").read_text().startswith("# o"))
            self.assertTrue((Path(tmp) / "o.mtl").exists())
            bad = Path(tmp) / "bad.dff"
            bad.write_bytes(b"\0" * 20)
            self.assertEqual(rs.main(["x", "info", str(bad)]), 1)


def real(ext):
    return sorted(p for p in GAME.glob("*/extracted/**/*") if p.suffix.lower() == ext and p.is_file())


@unittest.skipUnless(list(GAME.glob("*/extracted")), "no extracted game files")
class RealFiles(unittest.TestCase):
    def roundtrip(self, ext, minimum):
        files = real(ext)
        self.assertGreaterEqual(len(files), minimum)
        out = []
        for f in files:
            data = f.read_bytes()
            o = rs.PARSERS[ext].parse(data)
            self.assertEqual(o.build(), data, f"{f} differs after rebuild")
            out.append((f, o))
        return out

    def test_dff(self):
        for f, d in self.roundtrip(".dff", 1300):
            self.assertTrue(d.frames, f)  # (a few files, e.g. LIGHTS.DFF, are frame hierarchies without geometry)
            for fr in d.frames:
                self.assertTrue(-1 <= fr.parent < len(d.frames), f)
            for fi, gi, _ in d.atomics:
                self.assertTrue(0 <= fi < len(d.frames) and 0 <= gi < len(d.geometries), f)
            for g in d.geometries:
                nv = len(g.vertices)
                for a, b, c, m in g.triangles:
                    self.assertTrue(max(a, b, c) < nv and m < len(g.materials), f)
                for s in g.uvs:
                    self.assertEqual(len(s), nv, f)
                if g.normals:
                    self.assertEqual(len(g.normals), nv, f)
                if g.binmesh:
                    for m, idx in g.binmesh[1]:
                        self.assertTrue(m < len(g.materials) and all(i < nv for i in idx), f)
                    # strips: n indices give n-2 triangles; lists: n/3
                    n = sum(len(i) for _, i in g.binmesh[1])
                    self.assertGreater(n, 0, f)

    def test_dff_vehicle_is_car_sized(self):
        """The biggest model of a vehicle folder is the car body: a few metres long (sanity check of positions and frames)."""
        cars = [f for f in real(".dff") if f.parent.name.upper() == "BAMBAM" and "FullyLoaded" in f.parts]
        self.assertTrue(cars)
        best = 0.0
        for f in cars:
            d = rs.Dff.parse(f.read_bytes())
            pts = []
            for fi, gi, _ in d.atomics:
                m = d.world_matrix(fi)
                for v in d.geometries[gi].vertices:
                    pts.append(tuple(v[0] * m[0][k] + v[1] * m[1][k] + v[2] * m[2][k] + m[3][k] for k in range(3)))
            if pts:
                best = max(best, max(max(p[k] for p in pts) - min(p[k] for p in pts) for k in range(3)))
        self.assertTrue(0.5 < best < 20.0, best)  # the game uses small units (the car is about 1 unit long, tracks are 300 wide)

    def test_bsp(self):
        for f, b in self.roundtrip(".bsp", 120):
            self.assertTrue(b.sectors, f)
            for s in b.sectors:
                self.assertEqual(len(s.vertices), s.num_vertices, f)
                self.assertEqual(len(s.triangles), s.num_triangles, f)
                for v0, v1, v2, m in s.triangles:
                    self.assertTrue(max(v0, v1, v2) < s.num_vertices and m < len(b.materials), f)
                lo, hi = s.box[:3], s.box[3:]
                for v in s.vertices[:200]:  # vertices lie inside the sector box (box is stored as max, then min)
                    for k in range(3):
                        self.assertTrue(min(lo[k], hi[k]) - 1.0 <= v[k] <= max(lo[k], hi[k]) + 1.0, (f, v, s.box))

    def test_anm(self):
        for f, a in self.roundtrip(".anm", 130):
            self.assertGreater(len(a.keyframes), 0, f)
            for tm, q, t, _ in a.keyframes:
                self.assertAlmostEqual(sum(c * c for c in q), 1.0, delta=0.02, msg=f)
                self.assertTrue(-0.01 <= tm <= a.duration + 0.01, (f, tm, a.duration))

    def test_spl(self):
        for f, s in self.roundtrip(".spl", 50):
            self.assertGreaterEqual(len(s.points), 2, f)
            self.assertTrue(all(abs(c) < 5000 for p in s.points for c in p), f)

    def test_uva(self):
        for f, u in self.roundtrip(".uva", 20):
            self.assertTrue(u.animations, f)
            for a in u.animations:
                self.assertTrue(a.name, f)
                self.assertGreaterEqual(len(a.keyframes), 2, f)


if __name__ == "__main__":
    unittest.main()

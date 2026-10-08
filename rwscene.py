#!/usr/bin/env python3
"""Standard RenderWare 3.x scene files used by the game: DFF (clump), BSP (world), ANM (HAnim animation),
SPL (spline), UVA (UV animation dictionary).

  rwscene.py info FILE...           one line per file
  rwscene.py obj FILE OUT.obj       export geometry of a .DFF/.BSP as Wavefront OBJ (+ OUT.mtl)

Two layers:
  * `Tree` is a lossless chunk tree: `Tree.parse(data).build() == data` for every shipped file, so unknown plugin
    data is never lost.
  * typed read-only views (`Dff`, `Bsp`, `Anm`, `Spl`, `UvAnimDict`) decode the chunks the game uses.
Layouts follow the RenderWare 3.7 stream format (checked against librw and the SDK headers, see ../references).
Standard library only."""
import struct, sys
from dataclasses import dataclass, field
from pathlib import Path

# chunk types
STRUCT, STRING, EXTENSION, TEXTURE, MATERIAL, MATLIST, ATOMICSECT, PLANESECT, WORLD, SPLINE = 1, 2, 3, 6, 7, 8, 9, 0xA, 0xB, 0xC
FRAMELIST, GEOMETRY, CLUMP, ATOMIC, ANIMANIMATION, GEOMETRYLIST, UVANIMDICT = 0xE, 0xF, 0x10, 0x14, 0x1B, 0x1A, 0x2B
BINMESH = 0x50E

CONTAINERS = {EXTENSION, TEXTURE, MATERIAL, MATLIST, ATOMICSECT, PLANESECT, WORLD, FRAMELIST, GEOMETRY, CLUMP, ATOMIC,
              GEOMETRYLIST, UVANIMDICT, 0x2C}   # 0x2C = COLLTREE (only met inside the collision plugin payload)

# geometry / world format flags
TRISTRIP, POSITIONS, TEXTURED, PRELIT, NORMALS, LIGHT, MODULATE, TEXTURED2, NATIVE = 1, 2, 4, 8, 0x10, 0x20, 0x40, 0x80, 0x01000000


# ---------------------------------------------------------------- lossless tree
@dataclass
class Node:
    type: int
    stamp: int
    payload: bytes = b""
    children: list = field(default_factory=list)
    is_container: bool = False

    def build(self):
        body = b"".join(c.build() for c in self.children) if self.is_container else self.payload
        return struct.pack("<III", self.type, len(body), self.stamp) + body

    def find(self, type_):
        return [c for c in self.children if c.type == type_]

    def first(self, type_):
        for c in self.children:
            if c.type == type_:
                return c
        return None


class Tree:
    @staticmethod
    def parse_nodes(data, start=0, end=None):
        end = len(data) if end is None else end
        out, pos = [], start
        while pos < end:
            if pos + 12 > end:
                raise ValueError(f"{end - pos} trailing bytes at {pos:#x}")
            t, s, st = struct.unpack_from("<III", data, pos)
            if pos + 12 + s > end:
                raise ValueError(f"chunk 0x{t:X} at {pos:#x} (size {s}) runs past its parent")
            if t in CONTAINERS:
                out.append(Node(t, st, children=Tree.parse_nodes(data, pos + 12, pos + 12 + s), is_container=True))
            else:
                out.append(Node(t, st, payload=data[pos + 12:pos + 12 + s]))
            pos += 12 + s
        return out

    @classmethod
    def parse(cls, data):
        nodes = cls.parse_nodes(data)
        if len(nodes) != 1:
            raise ValueError("expected exactly one top level chunk")
        return nodes[0]


# ---------------------------------------------------------------- helpers
def _str(payload):
    return payload.split(b"\0")[0].decode("latin-1")


# ---------------------------------------------------------------- plugin data (EXTENSION chunks)
USERDATA, MATFX = 0x11F, 0x120
INT_DATA, FLOAT_DATA, STRING_DATA = 1, 2, 3
MATFX_NAMES = {0: "none", 1: "bump map", 2: "environment map", 3: "bump + environment map", 4: "dual texture", 5: "uv transform", 6: "dual uv transform"}


def read_user_data(payload):
    """RpUserData plugin (0x11F): u32 numArrays, then per array u32 nameLength, char name[nameLength], u32 format
    (1 int, 2 float, 3 string), u32 count, the values (strings are u32 length + chars). Returns {name: list of values}.
    The game's exporter puts: '0.tv_part_id' / '0.Part_ID' (int per geometry or per triangle: vehicle part ids),
    'FVF.UserData' (int 12291 = 0x3003, a Direct3D vertex format) and for reflective materials 'JaCFX.baseTexture' (string),
    'JaCFX.cfxType' (1..4) and 'JaCFX.cfxBlend' (0 or 2)."""
    n = struct.unpack_from("<I", payload, 0)[0]
    pos, out = 4, {}
    for _ in range(n):
        ln = struct.unpack_from("<I", payload, pos)[0]
        pos += 4
        name = _str(payload[pos:pos + ln])
        pos += ln
        fmt, cnt = struct.unpack_from("<II", payload, pos)
        pos += 8
        if fmt == STRING_DATA:
            vals = []
            for _ in range(cnt):
                ln = struct.unpack_from("<I", payload, pos)[0]
                pos += 4
                vals.append(_str(payload[pos:pos + ln]))
                pos += ln
        elif fmt in (INT_DATA, FLOAT_DATA):
            vals = list(struct.unpack_from(f"<{cnt}{'i' if fmt == INT_DATA else 'f'}", payload, pos))
            pos += 4 * cnt
        else:
            raise ValueError(f"unknown user data format {fmt}")
        out[name] = vals
    if pos != len(payload):
        raise ValueError(f"user data has {len(payload) - pos} unread bytes")
    return out


@dataclass
class MatFxEffect:
    type: int                 # see MATFX_NAMES
    coefficient: float = 0.0  # bump / environment map strength
    frame_buffer_alpha: int = 0
    src_blend: int = 0
    dst_blend: int = 0
    textures: tuple = ()      # texture names (bump map, environment map or second texture)


def read_matfx(payload):
    """RpMatFX material plugin (0x120): u32 effect type of the material, then two effect slots: u32 type and
    bump map {f32 coefficient, u32 hasTexture [+ TEXTURE chunk (bump), u32 hasTexture [+ TEXTURE chunk]}, environment map
    {f32 coefficient, u32 frameBufferAlpha, u32 hasTexture [+ TEXTURE]}, dual texture {u32 srcBlend, dstBlend, hasTexture
    [+ TEXTURE]}; UV transform effects carry nothing. Returns (material effect type, [MatFxEffect, MatFxEffect])."""
    pos = 4
    first = struct.unpack_from("<I", payload, 0)[0]

    def texture():
        nonlocal pos
        has = struct.unpack_from("<I", payload, pos)[0]
        pos += 4
        if not has:
            return None
        t, size, st = struct.unpack_from("<III", payload, pos)
        node = Tree.parse_nodes(payload, pos, pos + 12 + size)[0]
        pos += 12 + size
        return _str(node.find(STRING)[0].payload)

    effects = []
    for _ in range(2):
        t = struct.unpack_from("<I", payload, pos)[0]
        pos += 4
        e = MatFxEffect(t)
        names = []
        if t in (1, 3):  # bump map (3 = bump + env: bump first, then environment map)
            e.coefficient = struct.unpack_from("<f", payload, pos)[0]
            pos += 4
            names += [texture(), texture()]
        elif t == 2:
            e.coefficient, e.frame_buffer_alpha = struct.unpack_from("<fI", payload, pos)
            pos += 8
            names.append(texture())
        elif t == 4:
            e.src_blend, e.dst_blend = struct.unpack_from("<II", payload, pos)
            pos += 8
            names.append(texture())
        e.textures = tuple(n for n in names if n)
        effects.append(e)
    if pos != len(payload):
        raise ValueError(f"matfx has {len(payload) - pos} unread bytes")
    return first, effects


@dataclass
class CollTree:
    """RpCollision plugin (0x11D) of a BSP sector: u32 library version (0x37002), then chunk COLLTREE (0x2C) { STRUCT {
    u32 flags (1 = has entry map), f32 bbox[6], u32 numEntries, u32 numSplits, numSplits x split {sector left, sector right},
    [u16 map[numEntries]] } }. A sector is {u8 type (axis 0 x / 4 y / 8 z, |1 = negative), u8 contents (0xFF = split, else
    entry count), u16 index, f32 value}. The entries are the sector's triangles (the map reorders them)."""
    flags: int
    bbox: tuple
    num_entries: int
    splits: list     # ((type, contents, index, value), (type, contents, index, value))
    map: list


def read_colltree(payload):
    ver = struct.unpack_from("<I", payload, 0)[0]
    nodes = Tree.parse_nodes(payload, 4, len(payload))
    if ver != 0x37002 or len(nodes) != 1 or nodes[0].type != 0x2C:
        raise ValueError("unexpected collision plugin layout")
    s = nodes[0].first(STRUCT).payload
    flags = struct.unpack_from("<I", s, 0)[0]
    bbox = struct.unpack_from("<6f", s, 4)
    nent, nsplit = struct.unpack_from("<II", s, 28)   # u32 in the stream
    pos = 36
    splits = []
    for _ in range(nsplit):
        splits.append((struct.unpack_from("<BBHf", s, pos), struct.unpack_from("<BBHf", s, pos + 8)))
        pos += 16
    cmap = []
    if flags & 1:
        cmap = list(struct.unpack_from(f"<{nent}H", s, pos))
        pos += 2 * nent
    if pos != len(s):
        raise ValueError(f"collision tree has {len(s) - pos} unread bytes")
    return CollTree(flags, bbox, nent, splits, cmap)


def read_morph(payload):
    """RpMorph plugin (0x105) of a geometry: u32 numInterpolators, then per interpolator {u32 flags, i32 startMorphTarget,
    i32 endMorphTarget, f32 time, f32 recipTime (0, filled in at run time)}. Returns [(flags, start, end, time)]."""
    n = struct.unpack_from("<I", payload, 0)[0]
    if len(payload) != 4 + 20 * n:
        raise ValueError("bad morph plugin size")
    return [struct.unpack_from("<Iiif", payload, 4 + 20 * i) for i in range(n)]


def read_hanim_frame(payload):
    """RpHAnim plugin (0x11E) of a frame: u32 version (0x100), i32 node id (-1 = none), u32 numNodes; with numNodes > 0 a
    hierarchy follows (u32 flags, u32 key frame size, numNodes x {id, index, flags}). Returns (id, numNodes, raw rest)."""
    ver, node_id, n = struct.unpack_from("<IiI", payload, 0)
    if ver != 0x100:
        raise ValueError("bad HAnim version")
    return node_id, n, payload[12:]


def read_uvanim_ref(payload):
    """UV animation material plugin (0x135): chunk STRUCT { u32 1, char animationName[32] } - the name of the UVA animation
    that drives the material (see UvAnimDict)."""
    nodes = Tree.parse_nodes(payload, 0, len(payload))
    s = nodes[0].payload
    return _str(s[4:36])


def _plugins(node):
    """{plugin id: payload} of the EXTENSION child of a chunk."""
    ext = node.first(EXTENSION)
    return {c.type: c.payload for c in ext.children} if ext is not None else {}


# ---------------------------------------------------------------- materials / geometry
@dataclass
class Material:
    flags: int
    color: tuple          # r, g, b, a
    textured: bool
    ambient: float
    specular: float
    diffuse: float
    texture: str = ""
    mask: str = ""
    filter_addressing: int = 0
    user: dict = field(default_factory=dict)     # user data arrays (read_user_data)
    matfx: tuple = None                          # (type, [effects]) (read_matfx)
    uv_anim: str = ""                            # name of the UVA animation driving the material (0x135)


def read_material_list(node):
    """MATLIST chunk -> list of Material (instanced entries are copies of the earlier material)."""
    st = node.first(STRUCT).payload
    n = struct.unpack_from("<i", st, 0)[0]
    idx = struct.unpack_from(f"<{n}i", st, 4)
    mats = [c for c in node.children if c.type == MATERIAL]
    out, k = [], 0
    for i in idx:
        if i >= 0:
            out.append(out[i])
            continue
        m = mats[k]
        k += 1
        s = m.first(STRUCT).payload
        flags, color, _unused, textured = struct.unpack_from("<I4sIi", s, 0)
        amb, spec, diff = struct.unpack_from("<3f", s, 16) if len(s) >= 28 else (1.0, 1.0, 1.0)
        mat = Material(flags, tuple(color), bool(textured), amb, spec, diff)
        t = m.first(TEXTURE)
        if t is not None:
            mat.filter_addressing = struct.unpack_from("<I", t.first(STRUCT).payload, 0)[0]
            strs = t.find(STRING)
            mat.texture, mat.mask = _str(strs[0].payload), _str(strs[1].payload)
        plug = _plugins(m)
        if USERDATA in plug:
            mat.user = read_user_data(plug[USERDATA])
        if MATFX in plug:
            mat.matfx = read_matfx(plug[MATFX])
        if 0x135 in plug:
            mat.uv_anim = read_uvanim_ref(plug[0x135])
        out.append(mat)
    return out


@dataclass
class Geometry:
    flags: int
    tex_sets: int
    vertices: list         # (x, y, z) of morph target 0
    normals: list
    colors: list           # (r, g, b, a)
    uvs: list              # [set][(u, v)]
    triangles: list        # (v0, v1, v2, material)
    bsphere: tuple
    materials: list
    binmesh: tuple = None  # (flags, [(material, [indices])])
    user: dict = field(default_factory=dict)       # user data arrays, e.g. '0.tv_part_id'
    morph: list = field(default_factory=list)      # morph interpolators (0x105): (flags, start, end, time)


def _tex_sets(flags):
    n = (flags & 0xFF0000) >> 16
    return n if n else (2 if flags & TEXTURED2 else 1 if flags & TEXTURED else 0)


def read_geometry(node):
    st = node.first(STRUCT)
    p = st.payload
    version_new = st.stamp  # library stamp of the struct chunk
    flags, ntri, nvert, nmorph = struct.unpack_from("<IIII", p, 0)
    pos = 16
    # surface properties (ambient, specular, diffuse) are inside the struct before library version 3.4
    lib = ((version_new >> 14) & 0x3FF00) + 0x30000 | ((version_new >> 16) & 0x3F) if version_new & 0xFFFF0000 else 0
    if lib and lib < 0x34000:
        pos += 12
    nsets = _tex_sets(flags)
    colors, uvs, tris = [], [], []
    if not flags & NATIVE:
        if flags & PRELIT:
            colors = [tuple(p[pos + i * 4:pos + i * 4 + 4]) for i in range(nvert)]
            pos += 4 * nvert
        for _ in range(nsets):
            uvs.append(list(struct.iter_unpack("<2f", p[pos:pos + 8 * nvert])))
            pos += 8 * nvert
        for i in range(ntri):
            a, b, mat, c = struct.unpack_from("<4H", p, pos)  # stored as v1, v0, material, v2 (RenderWare's word order)
            tris.append((b, a, c, mat))
            pos += 8
    verts, normals, bsphere = [], [], (0, 0, 0, 0)
    for m in range(nmorph):
        sph = struct.unpack_from("<4f", p, pos)
        hasv, hasn = struct.unpack_from("<ii", p, pos + 16)
        pos += 24
        v = n = []
        if hasv:
            v = list(struct.iter_unpack("<3f", p[pos:pos + 12 * nvert]))
            pos += 12 * nvert
        if hasn:
            n = list(struct.iter_unpack("<3f", p[pos:pos + 12 * nvert]))
            pos += 12 * nvert
        if m == 0:
            verts, normals, bsphere = v, n, sph
    if pos != len(p):
        raise ValueError(f"geometry struct has {len(p) - pos} unread bytes")
    mats = read_material_list(node.first(MATLIST))
    g = Geometry(flags, nsets, verts, normals, colors, uvs, tris, bsphere, mats)
    gp = _plugins(node)
    if USERDATA in gp:
        g.user = read_user_data(gp[USERDATA])
    if 0x105 in gp:
        g.morph = read_morph(gp[0x105])
    ext = node.first(EXTENSION)
    if ext is not None:
        for c in ext.children:
            if c.type == BINMESH:
                bp = c.payload
                bflags, nmesh, total = struct.unpack_from("<III", bp, 0)
                q, meshes = 12, []
                for _ in range(nmesh):
                    cnt, mat = struct.unpack_from("<II", bp, q)
                    meshes.append((mat, list(struct.unpack_from(f"<{cnt}I", bp, q + 8))))
                    q += 8 + 4 * cnt
                if q != len(bp) or sum(len(i) for _, i in meshes) != total:
                    raise ValueError("bad BINMESH")
                g.binmesh = (bflags, meshes)
    return g


# ---------------------------------------------------------------- DFF
@dataclass
class Frame:
    right: tuple
    up: tuple
    at: tuple
    pos: tuple
    parent: int
    flags: int
    hanim: tuple = None    # (node id, numNodes, raw hierarchy) of the HAnim plugin (0x11E)


@dataclass
class Dff:
    """DFF = chunk CLUMP: STRUCT (numAtomics, numLights, numCameras), FRAMELIST, GEOMETRYLIST, ATOMIC*, EXTENSION."""
    stamp: int
    frames: list
    geometries: list
    atomics: list          # (frame index, geometry index, flags)
    tree: Node

    @classmethod
    def parse(cls, data):
        tree = Tree.parse(data)
        if tree.type != CLUMP:
            raise ValueError("not a DFF (clump)")
        struct_ = tree.first(STRUCT).payload
        natomics = struct.unpack_from("<I", struct_, 0)[0]
        fl = tree.first(FRAMELIST)
        fs = fl.first(STRUCT).payload
        nf = struct.unpack_from("<I", fs, 0)[0]
        if len(fs) != 4 + 56 * nf:
            raise ValueError("bad frame list")
        frames = []
        for i in range(nf):
            v = struct.unpack_from("<12fiI", fs, 4 + 56 * i)
            frames.append(Frame(v[0:3], v[3:6], v[6:9], v[9:12], v[12], v[13]))
        for fr, ext in zip(frames, fl.find(EXTENSION)):
            for p in ext.children:
                if p.type == 0x11E:
                    fr.hanim = read_hanim_frame(p.payload)
        gl = tree.first(GEOMETRYLIST)
        ngeo = struct.unpack_from("<I", gl.first(STRUCT).payload, 0)[0]
        geos = [read_geometry(g) for g in gl.find(GEOMETRY)]
        if len(geos) != ngeo:
            raise ValueError("geometry count mismatch")
        atomics = []
        for a in tree.find(ATOMIC):
            fi, gi, flags, _ = struct.unpack("<4I", a.first(STRUCT).payload[:16])
            atomics.append((fi, gi, flags))
        if len(atomics) != natomics:
            raise ValueError("atomic count mismatch")
        return cls(tree.stamp, frames, geos, atomics, tree)

    def build(self):
        return self.tree.build()

    def world_matrix(self, i):
        """Frame i as 4x3 matrix applied up the parent chain: returns (right, up, at, pos)."""
        f = self.frames[i]
        m = (f.right, f.up, f.at, f.pos)
        if f.parent >= 0 and f.parent != i:
            p = self.world_matrix(f.parent)

            def tr(v):  # row vector * parent matrix
                return tuple(v[0] * p[0][k] + v[1] * p[1][k] + v[2] * p[2][k] for k in range(3))
            pos = tuple(a + b for a, b in zip(tr(f.pos), p[3]))
            return tr(f.right), tr(f.up), tr(f.at), pos
        return m


# ---------------------------------------------------------------- BSP world
@dataclass
class Sector:
    num_triangles: int
    num_vertices: int
    box: tuple             # inf (x,y,z), sup (x,y,z)
    vertices: list
    normals: list
    colors: list
    uvs: list
    triangles: list        # (v0, v1, v2, material)
    mat_base: int
    user: dict = field(default_factory=dict)       # user data arrays
    plugins: dict = field(default_factory=dict)    # plugin id -> raw payload (BINMESH 0x50E, MatFX flag 0x120, 0x11D, ...)
    colltree: CollTree = None                      # collision tree (plugin 0x11D)


@dataclass
class Plane:
    axis: int              # 0 x, 4 y, 8 z (RenderWare plane type)
    value: float
    left_value: float
    right_value: float


@dataclass
class Bsp:
    """BSP = chunk WORLD: STRUCT(64), MATLIST, root sector (ATOMICSECT or PLANESECT tree), EXTENSION."""
    stamp: int
    root_is_world_sector: bool
    inv_origin: tuple
    num_triangles: int
    num_vertices: int
    num_plane_sectors: int
    num_world_sectors: int
    col_sector_size: int
    format: int
    box: tuple
    materials: list
    sectors: list
    planes: list
    tree: Node

    @classmethod
    def parse(cls, data):
        tree = Tree.parse(data)
        if tree.type != WORLD:
            raise ValueError("not a BSP (world)")
        w = struct.unpack("<i3f5iI6f", tree.first(STRUCT).payload)
        fmt = w[9]
        mats = read_material_list(tree.first(MATLIST))
        sectors, planes = [], []

        def walk(n):
            if n.type == ATOMICSECT:
                sectors.append(read_sector(n, fmt, tree.stamp))
            elif n.type == PLANESECT:
                t, val, lw, rw, lv, rv = struct.unpack("<ifiiff", n.first(STRUCT).payload)
                planes.append(Plane(t, val, lv, rv))
                for c in n.children:
                    if c.type in (ATOMICSECT, PLANESECT):
                        walk(c)
        for c in tree.children:
            if c.type in (ATOMICSECT, PLANESECT):
                walk(c)
        b = cls(tree.stamp, bool(w[0]), tuple(w[1:4]), w[4], w[5], w[6], w[7], w[8], fmt, tuple(w[10:16]), mats, sectors, planes, tree)
        if len(sectors) != b.num_world_sectors or len(planes) != b.num_plane_sectors:
            raise ValueError("sector count mismatch")
        if sum(s.num_vertices for s in sectors) != b.num_vertices or sum(s.num_triangles for s in sectors) != b.num_triangles:
            raise ValueError("world totals mismatch")
        return b

    def build(self):
        return self.tree.build()


def read_sector(node, fmt, stamp):
    p = node.first(STRUCT).payload
    base, ntri, nvert = struct.unpack_from("<iII", p, 0)
    box = struct.unpack_from("<6f", p, 12)
    pos = 12 + 24 + 8  # + collSectorPresent, unused
    verts = normals = colors = uvs = []
    tris = []
    if not fmt & NATIVE:
        verts = list(struct.iter_unpack("<3f", p[pos:pos + 12 * nvert]))
        pos += 12 * nvert
        if fmt & NORMALS:
            normals = [struct.unpack_from("<4b", p, pos + 4 * i)[:3] for i in range(nvert)]
            pos += 4 * nvert
        if fmt & PRELIT:
            colors = [tuple(p[pos + 4 * i:pos + 4 * i + 4]) for i in range(nvert)]
            pos += 4 * nvert
        uvs = []
        for _ in range(_tex_sets(fmt)):
            uvs.append(list(struct.iter_unpack("<2f", p[pos:pos + 8 * nvert])))
            pos += 8 * nvert
        for i in range(ntri):
            v0, v1, v2, mat = struct.unpack_from("<4H", p, pos)  # RpTriangle: vertIndex[3], matIndex (not the geometry order)
            tris.append((v0, v1, v2, mat))
            pos += 8
    if pos != len(p):
        raise ValueError(f"sector struct has {len(p) - pos} unread bytes")
    plug = _plugins(node)
    user = read_user_data(plug[USERDATA]) if USERDATA in plug else {}
    tree = read_colltree(plug[0x11D]) if 0x11D in plug else None
    return Sector(ntri, nvert, box, verts, normals, colors, uvs, tris, base, user, plug, tree)


# ---------------------------------------------------------------- ANM / SPL / UVA
@dataclass
class Anm:
    """ANM = chunk ANIMANIMATION: u32 0x100, u32 interpolator type (1 = HAnim), u32 numFrames, u32 flags, f32 duration,
    then numFrames x { f32 time, f32 quat[4], f32 translation[3], i32 previous key frame (byte offset, 0x24 per frame) }.
    The first key frame of every node holds a meaningless 'previous' value (left over pointer), so the raw offset is kept."""
    stamp: int
    version: int
    type: int
    flags: int
    duration: float
    keyframes: list   # (time, quat, translation, raw previous offset)

    @classmethod
    def parse(cls, data):
        t, size, stamp = struct.unpack_from("<III", data, 0)
        if t != ANIMANIMATION or size + 12 != len(data):
            raise ValueError("not an ANM")
        ver, typ, n, flags, dur = struct.unpack_from("<iiiif", data, 12)
        if ver != 0x100 or typ != 1 or len(data) != 32 + 36 * n:
            raise ValueError("unsupported animation layout")
        kf = []
        for i in range(n):
            v = struct.unpack_from("<f4f3fi", data, 32 + 36 * i)
            kf.append((v[0], tuple(v[1:5]), tuple(v[5:8]), v[8]))
        return cls(stamp, ver, typ, flags, dur, kf)

    def build(self):
        body = struct.pack("<iiiif", self.version, self.type, len(self.keyframes), self.flags, self.duration)
        for tm, q, tr, prev in self.keyframes:
            body += struct.pack("<f4f3fi", tm, *q, *tr, prev)
        return struct.pack("<III", ANIMANIMATION, len(body), self.stamp) + body

    def prev_index(self, i):
        """index of the previous key frame of the same node (None for the first key frame of a node)."""
        raw = self.keyframes[i][3]
        return raw // 36 if raw >= 0 and raw % 36 == 0 and raw // 36 < i else None

    def num_nodes(self):
        """number of animated nodes: RenderWare counts key frames until one links back to key frame 0."""
        for i in range(1, len(self.keyframes)):
            if self.keyframes[i][3] == 0:
                return i
        return len(self.keyframes)


@dataclass
class Spl:
    """SPL = chunk SPLINE (0x0C): 8 zero words (runtime pointers), u32 numControlPoints, u32 type/flags (1), then
    numControlPoints x (x, y, z) floats."""
    stamp: int
    reserved: bytes
    flags: int
    points: list

    @classmethod
    def parse(cls, data):
        t, size, stamp = struct.unpack_from("<III", data, 0)
        if t != SPLINE or size + 12 != len(data):
            raise ValueError("not an SPL")
        n, flags = struct.unpack_from("<II", data, 12 + 32)
        if len(data) != 12 + 40 + 12 * n:
            raise ValueError("bad spline size")
        return cls(stamp, data[12:12 + 32], flags, list(struct.iter_unpack("<3f", data[12 + 40:])))

    def build(self):
        body = self.reserved + struct.pack("<II", len(self.points), self.flags) + b"".join(struct.pack("<3f", *p) for p in self.points)
        return struct.pack("<III", SPLINE, len(body), self.stamp) + body


@dataclass
class UvAnim:
    stamp: int
    version: int
    type: int
    flags: int
    duration: float
    name: str
    node_to_uv: tuple
    keyframes: list   # (time, (u0, v0, u1, v1, du, dv), prev index)
    custom_unknown: int


@dataclass
class UvAnimDict:
    """UVA = chunk UVANIMDICT: STRUCT(u32 count), then count x ANIMANIMATION chunks of interpolator type 0x1C1:
    u32 0x100, u32 type, u32 numFrames, u32 flags, f32 duration, i32 0, char name[32], i32 nodeToUVChannel[8],
    then numFrames x { f32 time, f32 uv[6], i32 previous frame index }."""
    stamp: int
    animations: list
    tree: Node

    @classmethod
    def parse(cls, data):
        tree = Tree.parse(data)
        if tree.type != UVANIMDICT:
            raise ValueError("not a UVA")
        n = struct.unpack("<I", tree.first(STRUCT).payload)[0]
        anims = []
        for c in tree.children:
            if c.type != ANIMANIMATION:
                continue
            p = c.payload
            ver, typ, nf, flags, dur = struct.unpack_from("<iiiif", p, 0)
            unk = struct.unpack_from("<i", p, 20)[0]
            name = _str(p[24:56])
            nodes = struct.unpack_from("<8i", p, 56)
            if len(p) != 88 + 32 * nf:
                raise ValueError("bad UV animation size")
            kf = []
            for i in range(nf):
                v = struct.unpack_from("<f6fi", p, 88 + 32 * i)
                kf.append((v[0], v[1:7], v[7]))
            anims.append(UvAnim(c.stamp, ver, typ, flags, dur, name, nodes, kf, unk))
        if len(anims) != n:
            raise ValueError("animation count mismatch")
        return cls(tree.stamp, anims, tree)

    def build(self):
        return self.tree.build()


PARSERS = {".dff": Dff, ".bsp": Bsp, ".anm": Anm, ".spl": Spl, ".uva": UvAnimDict}


def load(path):
    path = Path(path)
    return PARSERS[path.suffix.lower()].parse(path.read_bytes())


# ---------------------------------------------------------------- OBJ export
def _obj(geos, name):
    """geos: [(vertices, uvs, normals, triangles, materials, matrix or None)] -> (obj text, mtl text)"""
    lines = [f"# {name}", f"mtllib {name}.mtl"]
    mtl, seen = [], {}
    vbase = 1
    for gi, (verts, uvs, normals, tris, mats, mat) in enumerate(geos):
        for v in verts:
            if mat is not None:
                v = tuple(v[0] * mat[0][k] + v[1] * mat[1][k] + v[2] * mat[2][k] + mat[3][k] for k in range(3))
            lines.append("v %.5f %.5f %.5f" % v)
        for t in uvs:
            lines.append("vt %.5f %.5f" % (t[0], 1.0 - t[1]))
        for n in normals:
            lines.append("vn %.5f %.5f %.5f" % tuple(n))
        lines.append(f"o geometry{gi}")
        cur = None
        for v0, v1, v2, m in tris:
            if m != cur:
                cur = m
                mname = f"mat{gi}_{m}"
                if mname not in seen and m < len(mats):
                    seen[mname] = 1
                    c = mats[m].color
                    mtl += [f"newmtl {mname}", "Kd %.3f %.3f %.3f" % (c[0] / 255, c[1] / 255, c[2] / 255)]
                    if mats[m].texture:
                        mtl.append(f"map_Kd {mats[m].texture}.png")
                lines.append(f"usemtl {mname}")
            idx = []
            for v in (v0, v1, v2):
                i = vbase + v
                idx.append(f"{i}/{i if uvs else ''}/{i if normals else ''}".rstrip("/").replace("//", "//"))
            lines.append("f " + " ".join(idx))
        vbase += len(verts)
    return "\n".join(lines) + "\n", "\n".join(mtl) + "\n"


def to_obj(obj, name):
    if isinstance(obj, Dff):
        geos = []
        for fi, gi, _ in obj.atomics:
            g = obj.geometries[gi]
            geos.append((g.vertices, g.uvs[0] if g.uvs else [], g.normals, g.triangles, g.materials, obj.world_matrix(fi)))
        return _obj(geos, name)
    if isinstance(obj, Bsp):
        geos = [(s.vertices, s.uvs[0] if s.uvs else [], [], s.triangles, obj.materials, None) for s in obj.sectors]
        return _obj(geos, name)
    raise ValueError("OBJ export only for DFF and BSP")


def summary(o):
    if isinstance(o, Dff):
        nt = sum(len(g.triangles) for g in o.geometries)
        nv = sum(len(g.vertices) for g in o.geometries)
        tex = sorted({m.texture for g in o.geometries for m in g.materials if m.texture})
        return f"DFF {len(o.frames)} frames, {len(o.geometries)} geometries, {len(o.atomics)} atomics, {nv} vertices, {nt} triangles, textures {tex}"
    if isinstance(o, Bsp):
        return (f"BSP {o.num_world_sectors} sectors, {o.num_plane_sectors} planes, {o.num_vertices} vertices, {o.num_triangles} triangles, "
                f"{len(o.materials)} materials, bbox {tuple(round(x) for x in o.box)}")
    if isinstance(o, Anm):
        return f"ANM {len(o.keyframes)} key frames, {o.num_nodes()} nodes, duration {o.duration:.3f}s"
    if isinstance(o, Spl):
        return f"SPL {len(o.points)} control points, flags {o.flags}"
    if isinstance(o, UvAnimDict):
        return "UVA " + ", ".join(f"{a.name}({len(a.keyframes)} keys, {a.duration:.2f}s)" for a in o.animations)
    return repr(o)


def main(argv):
    if len(argv) < 3 or argv[1] not in ("info", "obj"):
        print(__doc__)
        return 2
    if argv[1] == "info":
        rc = 0
        for f in argv[2:]:
            try:
                print(f"{f}: {summary(load(f))}")
            except (ValueError, struct.error) as e:
                print(f"{f}: {e}", file=sys.stderr)
                rc = 1
        return rc
    out = Path(argv[3])
    obj, mtl = to_obj(load(argv[2]), out.stem)
    out.write_text(obj)
    out.with_suffix(".mtl").write_text(mtl)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

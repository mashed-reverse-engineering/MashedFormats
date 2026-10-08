#!/usr/bin/env python3
"""Parsers/writers for the game specific file formats of Mashed / Mashed Fully Loaded.

  mashed_formats.py info FILE...     one line per file (type is taken from the extension)
  mashed_formats.py dump FILE        full text dump

Every parser keeps what it needs to write the file back byte for byte (see FORMATS.md and test_formats.py).
Only the standard library is used."""
import struct, sys
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------- RenderWare chunk helpers
def rw_stamp(stamp):
    """RenderWare library stamp -> (version 'a.b.c.d', build). Old stamps (<0x10000) have no build."""
    if stamp & 0xFFFF0000 == 0:
        return f"{stamp >> 8:x}", 0
    v = ((stamp >> 14) & 0x3FF00) + 0x30000 | ((stamp >> 16) & 0x3F)
    return f"{v >> 16 & 0xF}.{v >> 12 & 0xF}.{v >> 8 & 0xF}.{v & 0xFF}", stamp & 0xFFFF


def read_chunk_header(data, off=0):
    """(type, size, stamp) of the 12 byte RenderWare chunk header at off."""
    return struct.unpack_from("<III", data, off)


# ---------------------------------------------------------------- LED: camera angle table
LED_CHUNK, LED_RECORDS = 0x13269901, 384


@dataclass
class Led:
    """LE<n>.LED, one per track: 384 records of three floats, -1.0 = unset (the game clears the table to -1.0).
    Used by the camera code (FUN_0044a530 in MFL.exe): the record for a track segment overrides the default
    camera direction; field0 is subtracted from a base angle (pitch), field1 is added to another base angle (yaw),
    field2 is returned as an extra value (camera tilt/roll)."""
    stamp: int
    records: list = field(default_factory=list)  # [(f0, f1, f2)] * 384

    @classmethod
    def parse(cls, data):
        t, size, stamp = read_chunk_header(data)
        if t != LED_CHUNK or size != LED_RECORDS * 12 or len(data) != 12 + size:
            raise ValueError("not a LED file")
        return cls(stamp, list(struct.iter_unpack("<3f", data[12:])))

    def build(self):
        return struct.pack("<III", LED_CHUNK, LED_RECORDS * 12, self.stamp) + b"".join(struct.pack("<3f", *r) for r in self.records)

    def used(self):
        return [(i, r) for i, r in enumerate(self.records) if r != (-1.0, -1.0, -1.0)]


# ---------------------------------------------------------------- AI: bot driving data of a track
AI_CHUNK, AI_SIZE = 0x13269902, 0x11884
AI_GRID, AI_TILES, AI_PATH_SETS, AI_PATH_SLOTS, AI_PATH_POINTS = 128, 512, 4, 3, 64
# names of the four line types from the game's built-in AI editor menu (FUN_0044e4d0) and of the tile cell values
AI_LINE_NAMES = ("race lines", "inside lines", "slow lines", "cheat lines")
AI_CELL_VALUES = (0, 1, 2, 3)   # "danger data" brush (editor paints value & 3); 0xFF = empty cell


@dataclass
class Ai:
    """AI<n>.AI: the dump of the game's AI editor state (one RenderWare chunk 0x13269902, 0x11884 bytes).
      0x00000  int16[128][128]   grid of 8x8 world unit cells (coordinate + 512) >> 3 -> tile index, -1 = no tile
      0x08000  uint8[512][8][8]  tile pool ("danger data"), 0xFF = empty cell, otherwise the painted cell type 0..3
      0x10000  int32             highest tile index in use ("memory=%d" in the editor)
      0x10004  4 sets x 3 slots x (64 x (float x, float z), int32 count)   0x204 bytes each: route points. Sets are the
               line types AI_LINE_NAMES = race lines, inside lines, slow lines, cheat lines; a bot uses slot 0..2 of its
               set and falls back to slot 0 of the race lines when the slot has fewer than 4 points (FUN_0041b4e0)
      0x11838  4 x 5 words       per bot runtime state that was saved by accident (last record lacks its 5th word)"""
    stamp: int
    grid: list
    tiles: bytes
    max_tile: int
    paths: list       # [set][slot] -> list of (x, z) points, trimmed to count
    path_raw: list    # [set][slot] -> the full 64 points + count, for a lossless rewrite
    tail: bytes

    @classmethod
    def parse(cls, data):
        t, size, stamp = read_chunk_header(data)
        if t != AI_CHUNK or size != AI_SIZE or len(data) != 12 + size:
            raise ValueError("not an AI file")
        b = data[12:]
        grid = list(struct.unpack_from("<16384h", b, 0))
        tiles = b[0x8000:0x10000]
        max_tile = struct.unpack_from("<i", b, 0x10000)[0]
        paths, raw = [], []
        for s in range(AI_PATH_SETS):
            ps, rs = [], []
            for k in range(AI_PATH_SLOTS):
                o = 0x10004 + (s * AI_PATH_SLOTS + k) * 0x204
                pts = list(struct.iter_unpack("<2f", b[o:o + 0x200]))
                cnt = struct.unpack_from("<i", b, o + 0x200)[0]
                rs.append((pts, cnt))
                ps.append(pts[:max(0, min(cnt, AI_PATH_POINTS))])
            paths.append(ps)
            raw.append(rs)
        tail_start = 0x10004 + AI_PATH_SETS * AI_PATH_SLOTS * 0x204
        return cls(stamp, grid, tiles, max_tile, paths, raw, b[tail_start:])

    def build(self):
        out = bytearray(struct.pack("<III", AI_CHUNK, AI_SIZE, self.stamp))
        out += struct.pack("<16384h", *self.grid) + self.tiles + struct.pack("<i", self.max_tile)
        for rs in self.path_raw:
            for pts, cnt in rs:
                out += b"".join(struct.pack("<2f", *p) for p in pts) + struct.pack("<i", cnt)
        return bytes(out + self.tail)

    def used_tiles(self):
        return sorted({t for t in self.grid if t > 0})


# ---------------------------------------------------------------- MTS: list of matrices
@dataclass
class Mts:
    """<name>.MTS: u32 count, then count RenderWare matrix chunks (type 0x0D, size 0x40) each holding a struct
    chunk (type 1, size 0x34): right[3], up[3], at[3], pos[3] floats and a u32 flags (RwMatrixStreamWrite).
    Referenced from COURSE.LUA by Mts_Filename(); the game allows at most 0x30 matrices per file (Too many matrices)."""
    stamp: int
    matrices: list  # [(right, up, at, pos, flags)]

    @classmethod
    def parse(cls, data):
        n = struct.unpack_from("<I", data, 0)[0]
        if len(data) != 4 + 76 * n:
            raise ValueError("not an MTS file")
        ms, stamp = [], None
        for i in range(n):
            o = 4 + 76 * i
            t, s, st = read_chunk_header(data, o)
            t2, s2, st2 = read_chunk_header(data, o + 12)
            if (t, s, t2, s2) != (0x0D, 0x40, 1, 0x34) or st != st2:
                raise ValueError(f"bad matrix chunk {i}")
            stamp = st
            f = struct.unpack_from("<12fI", data, o + 24)
            ms.append((f[0:3], f[3:6], f[6:9], f[9:12], f[12]))
        return cls(stamp if stamp is not None else 0x1C02000A, ms)

    def build(self):
        out = bytearray(struct.pack("<I", len(self.matrices)))
        for r, u, a, p, fl in self.matrices:
            out += struct.pack("<III", 0x0D, 0x40, self.stamp) + struct.pack("<III", 1, 0x34, self.stamp)
            out += struct.pack("<12fI", *r, *u, *a, *p, fl)
        return bytes(out)


# ---------------------------------------------------------------- GSD: small parameter blocks
@dataclass
class Gsd:
    """<NAME><n>.GSD (Common/Perm): raw array of little endian 32 bit words. SNOW files have 12 words, BOID files 9.
    Only MFL.exe has a loader (FUN_0044a040, called by the track setup FUN_0042b450): it reads "Boid<track>.gsd" into a
    0x30 byte global at 0x8C7E80, with defaults (400, 128, ?, 100, 128, 128, 128, 0, ?, 1, 0, 0) when the file is missing.
    **No other code reads that block** (checked: no reference to the global anywhere else) and MASHED.exe has no loader at
    all, so the feature (a flock / snow particle effect, w4..w6 look like an RGB colour) is dead in the shipped game; the
    SNOW<n>.GSD files are never opened (the name does not match "Boid%d.gsd"). The words are kept raw."""
    words: tuple

    @classmethod
    def parse(cls, data):
        if len(data) not in (36, 48):
            raise ValueError("not a GSD file")
        return cls(struct.unpack(f"<{len(data) // 4}I", data))

    def build(self):
        return struct.pack(f"<{len(self.words)}I", *self.words)


# ---------------------------------------------------------------- DAT: language string tables
@dataclass
class Dat:
    """<LANGUAGE>.DAT (Font36 folder): u32 offset table (its size is the first offset, 834 entries), then
    for each string a u16 length in UTF-16 code units followed by that many UTF-16LE units. Control characters
    below 0x20 (e.g. 0x08, 0x09) are button icons. The offsets are authoritative: the Drive to Survive files have two
    zero bytes after string 73, so the bytes between a string and the next offset are kept in `gaps`
    (the last one is the file tail, 'CR LF' in all shipped files)."""
    strings: list
    gaps: list = None

    def __post_init__(self):
        if self.gaps is None:
            self.gaps = [b""] * (len(self.strings) - 1) + [b"\r\n"]

    @classmethod
    def parse(cls, data):
        first = struct.unpack_from("<I", data, 0)[0]
        n = first // 4
        offs = struct.unpack_from(f"<{n}I", data, 0)
        strings, gaps = [], []
        for i, o in enumerate(offs):
            ln = struct.unpack_from("<H", data, o)[0]
            end = o + 2 + 2 * ln
            nxt = offs[i + 1] if i + 1 < n else len(data)
            if end > nxt or nxt < o:
                raise ValueError("bad string table")
            strings.append(data[o + 2:end].decode("utf-16-le"))
            gaps.append(data[end:nxt])
        return cls(strings, gaps)

    def build(self):
        n = len(self.strings)
        table, body, pos = bytearray(), bytearray(), n * 4
        for s, gap in zip(self.strings, self.gaps):
            table += struct.pack("<I", pos + len(body))
            enc = s.encode("utf-16-le")
            body += struct.pack("<H", len(enc) // 2) + enc + gap
        return bytes(table + body)


# ---------------------------------------------------------------- AID: AI animation/route data
@dataclass
class Aid:
    """AI1.AID (City, sands, SandSnow): u32 count, then count records of 18 floats (72 bytes) = six 3-vectors. In most
    records every vector is a unit direction (norm 1.0), the others look like points of the track (x -40..1, y 0..8,
    z -26..77). Neither executable contains the string ".AID" and nothing references such a file, so it is an unused
    tool leftover; the meaning of the six vectors is not known."""
    records: list

    @classmethod
    def parse(cls, data):
        n = struct.unpack_from("<I", data, 0)[0]
        if len(data) != 4 + 72 * n:
            raise ValueError("not an AID file")
        return cls(list(struct.iter_unpack("<18f", data[4:])))

    def build(self):
        return struct.pack("<I", len(self.records)) + b"".join(struct.pack("<18f", *r) for r in self.records)


# ---------------------------------------------------------------- RWF: bitmap font (custom RenderWare chunk 0x199)
@dataclass
class Glyph:
    u0: float
    v0: float
    u1: float
    v1: float          # texture rectangle in 0..1
    advance: float     # 5th float of the record (glyph width scale, ~0.15)
    texture: int       # index into Rwf.textures


@dataclass
class Rwf:
    """*.RWF = RenderWare Rt2d font (chunk type 0x199 = rwID_2DFONT), written by Rt2dFontStreamWrite and read by
    Rt2dFontStreamRead (layout taken from the decompiled reader in MFL.exe, `Rt2dFontStreamRead` at 0x564E40):
      u32 version (0x01000001), i32 type (0 = texture font)
      f32 height, f32 scale, i32 unknown (0), i32 unknown (2)
      i32 numLowMap (128), i32 numGlyphs, i32 numHighMap (128)
      u16 high_map[numHighMap]      glyph index of the characters 128..255
      u16 low_map[128]              glyph index of the characters 0..127, 0xFFFF = no glyph
      numGlyphs x { f32 u0, v0, u1, v1; f32 advance; u8 texture }          21 bytes each
      i32 numTextures, numTextures x char name[32]
    The size field of the chunk header is larger than the data (by 192/194/120 bytes in the shipped files) and is kept raw."""
    stamp: int
    size_field: int
    version: int
    type: int
    height: float
    scale: float
    unknown: tuple         # the two int32 after the scale (0, 2)
    num_low_map: int
    low_map: list
    high_map: list
    glyphs: list
    textures: list

    @classmethod
    def parse(cls, data):
        t, size, stamp = read_chunk_header(data)
        if t != 0x199:
            raise ValueError("not an RWF file")
        p = data[12:]
        ver, typ, height, scale, u1, u2, nlow, nglyph, nhigh = struct.unpack_from("<IiffiiIII", p, 0)
        if typ != 0:
            raise ValueError("only texture fonts are supported")
        pos = 36
        high = list(struct.unpack_from(f"<{nhigh}H", p, pos))
        pos += 2 * nhigh
        low = list(struct.unpack_from("<128H", p, pos))
        pos += 256
        glyphs = []
        for _ in range(nglyph):
            u0, v0, u1_, v1, adv = struct.unpack_from("<5f", p, pos)
            glyphs.append(Glyph(u0, v0, u1_, v1, adv, p[pos + 20]))
            pos += 21
        ntex = struct.unpack_from("<i", p, pos)[0]
        pos += 4
        texs = [p[pos + 32 * i:pos + 32 * i + 32].split(b"\0")[0].decode("latin-1") for i in range(ntex)]
        if pos + 32 * ntex != len(p):
            raise ValueError("font data does not match the stream layout")
        return cls(stamp, size, ver, typ, height, scale, (u1, u2), nlow, low, high, glyphs, texs)

    def build(self):
        p = struct.pack("<IiffiiIII", self.version, self.type, self.height, self.scale, *self.unknown, self.num_low_map,
                        len(self.glyphs), len(self.high_map))
        p += struct.pack(f"<{len(self.high_map)}H", *self.high_map) + struct.pack("<128H", *self.low_map)
        for g in self.glyphs:
            p += struct.pack("<5fB", g.u0, g.v0, g.u1, g.v1, g.advance, g.texture)
        p += struct.pack("<i", len(self.textures)) + b"".join(t.encode("latin-1").ljust(32, b"\0") for t in self.textures)
        return struct.pack("<III", 0x199, self.size_field, self.stamp) + p

    def glyph_for(self, code):
        """Glyph of a character code 0..255 (None if the font has none)."""
        i = self.low_map[code] if code < 128 else self.high_map[code - 128]
        return self.glyphs[i] if i != 0xFFFF and i < len(self.glyphs) else None


# ---------------------------------------------------------------- REP: time attack ghost replays
@dataclass
class RepRecord:
    header: bytes   # 0x19C bytes: copy of the game's replay object (id, size, track, time, ... runtime pointers)
    body: bytes     # size - 0x19C bytes: 20 bytes (1, 1, 0, 0, 0), then frame_count x 36 bytes (size is exactly 412 + 20 + 36 * frames)

    @property
    def id(self):
        return struct.unpack_from("<I", self.header, 0)[0]

    @property
    def size(self):
        return len(self.header) + len(self.body)

    @property
    def track(self):
        return struct.unpack_from("<I", self.header, 8)[0]

    @property
    def time(self):
        """best (target) time in seconds."""
        return struct.unpack_from("<f", self.header, 12)[0]

    @property
    def frame_count(self):
        """capacity of the recording: target time * 7.5 frames (the game samples the car 7.5 times per second)."""
        return struct.unpack_from("<I", self.header, 93 * 4)[0]

    @property
    def recorded_frames(self):
        """number of frames really recorded (header word 92 holds the index of the last one); the rest is zero."""
        return struct.unpack_from("<I", self.header, 92 * 4)[0] + 1

    def frames(self):
        """[(qx, qy, qz, qw, x, y, z, address, stamp)] per frame: unit quaternion, position, the memory address the frame had
        while recording (+36 per frame, meaningless in the file) and a time stamp in ms (multiples of 50, otherwise 0).
        Frames after the end of the recording are all zero."""
        return list(struct.iter_unpack("<7fII", self.body[20:20 + 36 * self.frame_count]))

    @property
    def recorded_seconds(self):
        return self.recorded_frames / 7.5


@dataclass
class Rep:
    """BEST<n>.REP / Replay<n>.REP: the game's ghost buffer (`FUN_004957f0`): a list of records, each
        u32 id (0x10A/0x10B), u32 size (whole record), u32 track, f32 target time, u32 8, 4 runtime pointers, ... (0x19C
        byte header = memory copy of the replay object), then the frame data. The list ends with a record whose
        id and track are 0; the rest of the file up to the reserved buffer size is zero padding (kept in `tail`).
    Frame (36 bytes) = quaternion (x, y, z, w), position (x, y, z), u32 runtime address, u32 time stamp in ms. Checked on all
    12 records of both copies: record size is exactly 412 + 20 + 36 * frames, frames = target time * 7.5, header word 92 + 1 =
    number of non zero frames, unit quaternions, smooth positions."""
    records: list
    tail: bytes

    HEADER = 0x19C

    @classmethod
    def parse(cls, data):
        pos, recs = 0, []
        while pos + cls.HEADER <= len(data):
            rid, size, track = struct.unpack_from("<III", data, pos)
            if size == 0 and rid == 0 and track == 0:
                break
            if size < cls.HEADER or pos + size > len(data):
                raise ValueError(f"bad replay record at {pos:#x}")
            recs.append(RepRecord(data[pos:pos + cls.HEADER], data[pos + cls.HEADER:pos + size]))
            pos += size
        if not recs:
            raise ValueError("not a replay file")
        return cls(recs, data[pos:])

    def build(self):
        return b"".join(r.header + r.body for r in self.records) + self.tail


# ---------------------------------------------------------------- BUM: unreferenced sample data
@dataclass
class Bum:
    """N.BUM (SFX folder, 43008 bytes): headerless array of 21504 signed 16 bit values. Neither executable contains a
    reference to the extension, so this is probably unused sample data (the values are 16 bit audio like: smooth
    around zero, +-8000). `to_wav` wraps them as mono 16 bit PCM; the sample rate (22050 Hz default) is a guess."""
    samples: tuple

    @classmethod
    def parse(cls, data):
        if len(data) % 2:
            raise ValueError("odd BUM size")
        return cls(struct.unpack(f"<{len(data) // 2}h", data))

    def build(self):
        return struct.pack(f"<{len(self.samples)}h", *self.samples)

    def to_wav(self, rate=22050):
        pcm = self.build()
        return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
                + b"data" + struct.pack("<I", len(pcm)) + pcm)


# ---------------------------------------------------------------- CMP: zlib compressed file
def cmp_decompress(data):
    """*.CMP (only TRACKIMAGES.TXD.CMP): a plain zlib stream (header 78 DA) of the original file."""
    import zlib
    return zlib.decompress(data)


def cmp_compress(data):
    """zlib level 9 reproduces the shipped TRACKIMAGES.TXD.CMP byte for byte."""
    import zlib
    return zlib.compress(data, 9)


PARSERS = {".led": Led, ".led_x": Led, ".ai": Ai, ".mts": Mts, ".gsd": Gsd, ".dat": Dat, ".aid": Aid, ".rwf": Rwf, ".rep": Rep, ".bum": Bum}


def load(path):
    path = Path(path)
    cls = PARSERS.get(path.suffix.lower())
    if cls is None:
        raise ValueError(f"no parser for {path.suffix}")
    return cls.parse(path.read_bytes())


def summary(obj):
    if isinstance(obj, Led):
        return f"LED stamp {rw_stamp(obj.stamp)[0]}, {len(obj.used())} of {LED_RECORDS} records set"
    if isinstance(obj, Ai):
        return (f"AI stamp {rw_stamp(obj.stamp)[0]}, {len(obj.used_tiles())} tiles, max tile {obj.max_tile}, "
                f"routes " + " ".join("/".join(str(len(p)) for p in s) for s in obj.paths))
    if isinstance(obj, Mts):
        return f"MTS {len(obj.matrices)} matrices"
    if isinstance(obj, Gsd):
        return f"GSD {len(obj.words)} words {obj.words}"
    if isinstance(obj, Dat):
        return f"DAT {len(obj.strings)} strings, first '{obj.strings[0]}'"
    if isinstance(obj, Aid):
        return f"AID {len(obj.records)} records"
    if isinstance(obj, Rep):
        return f"REP {len(obj.records)} ghost records, tracks {[r.track for r in obj.records]}, times {[round(r.time, 1) for r in obj.records]}"
    if isinstance(obj, Bum):
        return f"BUM {len(obj.samples)} samples, range {min(obj.samples)}..{max(obj.samples)}"
    if isinstance(obj, Rwf):
        return f"RWF font, height {obj.height}, {len(obj.glyphs)} glyphs, textures {obj.textures}"
    return repr(obj)


def main(argv):
    if len(argv) < 3 or argv[1] not in ("info", "dump"):
        print(__doc__)
        return 2
    rc = 0
    for f in argv[2:]:
        try:
            o = load(f)
        except (ValueError, struct.error) as e:
            print(f"{f}: {e}", file=sys.stderr); rc = 1; continue
        if argv[1] == "info":
            print(f"{f}: {summary(o)}")
        elif isinstance(o, Dat):
            for i, s in enumerate(o.strings):
                print(f"{i}\t{s!r}")
        elif isinstance(o, Mts):
            for i, m in enumerate(o.matrices):
                print(i, *[tuple(round(x, 4) for x in v) for v in m[:4]], m[4])
        elif isinstance(o, Led):
            for i, r in o.used():
                print(i, r)
        elif isinstance(o, Ai):
            for s, ps in enumerate(o.paths):
                for k, p in enumerate(ps):
                    if p:
                        print(f"route set {s} slot {k}: {len(p)} points", p[:4], "...")
            print("tiles in use:", o.used_tiles()[:20], "...")
        else:
            print(o)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))

#!/usr/bin/env python3
"""RenderWare Audio (RWA 3.7) containers of the game: toastaudio/pc/audio/pcdics/**/*.rws

  rws.py info FILE...         one line per file
  rws.py export FILE OUTDIR   write every sound / voice clip as 16 bit mono WAV (22050 Hz)

Two kinds of files (chunk type of the top level chunk):
  0x809  wave dictionary (sound effects per track, PERMDICT.RWS, KEYS.RWS, ...):
         0x80A (68 bytes: ..., char name[16] at +0x34), 0x80C { u32 count, count x 0x802 { 0x803 wave header, 0x804 data } }
         wave header (160 or 176 bytes): u32 15, u32 sample rate (22050), u32 runtime, u32 data size, u32 flags/loop (low 16 bits
         0x0110), ..., GUIDs, char name[] at +0x70. Data = signed 16 bit little endian mono PCM (size = header size field).
  0x80D  voice stream (english/red.rws, ... the spoken lines of the six characters, also the track music):
         0x80E header payload: ..., u32 numClips at word 8, char stream name at +0x50, then
             numClips x 32 bytes {4 words runtime pointers/0, u32 size, u32 offset}      at +96   (size/offset into the 0x80F data)
             numClips x u32 exact byte size of the ADPCM data                         (multiple of 36)
             numClips x 16 byte GUID, numClips x char name[16] (e.g. "1_1_1_RD_1")
         0x80F data: the clips back to back, every clip a multiple of 10240 byte packets; a packet holds 284 blocks of 36 bytes
         (Xbox style IMA ADPCM: int16 predictor, u8 step index, u8 0, 32 bytes of nibbles = 64 samples, low nibble first)
         followed by 16 bytes of padding.
The sample rate of the voice streams is not stored in a field that was found; 22050 Hz (that of every wave in the
dictionaries) is assumed. Standard library only."""
import struct, sys
from dataclasses import dataclass, field
from pathlib import Path

WAVEDICT, WAVEDICT_HDR, WAVELIST, WAVE, WAVE_HDR, WAVE_DATA = 0x809, 0x80A, 0x80C, 0x802, 0x803, 0x804
STREAM, STREAM_HDR, STREAM_DATA = 0x80D, 0x80E, 0x80F
BLOCK, BLOCKS_PER_PACKET, PACKET = 36, 284, 10240
RATE = 22050

STEP = [7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118,
        130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166,
        1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845,
        8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767]
INDEX = [-1, -1, -1, -1, 2, 4, 6, 8]


def _chunk(t, stamp, payload):
    return struct.pack("<III", t, len(payload), stamp) + payload


def _chunks(d, start, end):
    out, pos = [], start
    while pos + 12 <= end:
        t, s, st = struct.unpack_from("<III", d, pos)
        if pos + 12 + s > end:
            raise ValueError(f"chunk 0x{t:X} at {pos:#x} runs past its parent")
        out.append((t, st, d[pos + 12:pos + 12 + s]))
        pos += 12 + s
    if pos != end:
        raise ValueError(f"{end - pos} trailing bytes")
    return out


# ---------------------------------------------------------------- Xbox / IMA ADPCM
def _step(pred, idx, n):
    step = STEP[idx]
    diff = step >> 3
    if n & 1:
        diff += step >> 2
    if n & 2:
        diff += step >> 1
    if n & 4:
        diff += step
    pred += -diff if n & 8 else diff
    return max(-32768, min(32767, pred)), max(0, min(88, idx + INDEX[n & 7]))


def decode_block(block):
    """36 bytes -> 64 samples: the header predictor is the first sample, then 63 nibbles (the last nibble is not used)."""
    pred, idx = struct.unpack_from("<hB", block, 0)
    if idx > 88:
        raise ValueError("bad ADPCM step index")
    out = [pred]
    for byte in block[4:]:
        for n in (byte & 15, byte >> 4):
            if len(out) == 64:
                return out
            pred, idx = _step(pred, idx, n)
            out.append(pred)
    return out


def encode_block(samples, state=(0, 0)):
    """64 PCM samples -> 36 byte block (inverse of decode_block up to quantisation). Returns (block, (pred, idx))."""
    pred, idx = samples[0], state[1]
    out = bytearray(struct.pack("<hBB", pred, idx, 0))
    nibs = []
    for s in samples[1:]:
        step = STEP[idx]
        diff = s - pred
        n = 8 if diff < 0 else 0
        diff = abs(diff)
        if diff >= step:
            n |= 4
            diff -= step
        if diff >= step >> 1:
            n |= 2
            diff -= step >> 1
        if diff >= step >> 2:
            n |= 1
        pred, idx = _step(pred, idx, n)
        nibs.append(n)
    nibs.append(0)
    for i in range(0, 64, 2):
        out.append(nibs[i] | nibs[i + 1] << 4)
    return bytes(out), (pred, idx)


def wav_bytes(pcm, rate=RATE):
    """PCM16 mono bytes -> WAV file bytes."""
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)


# ---------------------------------------------------------------- wave dictionary
@dataclass
class Wave:
    header: bytes    # raw 0x803 payload (160 or 176 bytes)
    pcm: bytes       # raw 0x804 payload: signed 16 bit LE mono samples
    stamp: int

    @property
    def name(self):
        return self.header[0x70:].split(b"\0")[0].decode("latin-1")

    @property
    def rate(self):
        return struct.unpack_from("<I", self.header, 4)[0]

    @property
    def size(self):
        return struct.unpack_from("<I", self.header, 12)[0]

    @property
    def flags(self):
        """word 4: low 16 bits are 0x0110 in all files, the high 16 bits vary (0xFFFF in most; probably a loop marker)."""
        return struct.unpack_from("<I", self.header, 0x10)[0]

    def samples(self):
        return struct.unpack(f"<{len(self.pcm) // 2}h", self.pcm)

    def wav(self):
        return wav_bytes(self.pcm, self.rate)


@dataclass
class WaveDict:
    stamp: int
    header: bytes     # raw 0x80A payload (68 bytes)
    waves: list
    list_stamp: int

    @property
    def name(self):
        return self.header[0x34:0x44].split(b"\0")[0].decode("latin-1")

    @classmethod
    def parse(cls, data):
        top = _chunks(data, 0, len(data))
        if len(top) != 1 or top[0][0] != WAVEDICT:
            raise ValueError("not a wave dictionary")
        t, stamp, payload = top[0]
        kids = _chunks(payload, 0, len(payload))
        if [k[0] for k in kids] != [WAVEDICT_HDR, WAVELIST]:
            raise ValueError("unexpected dictionary layout")
        n = struct.unpack_from("<I", kids[1][2], 0)[0]
        waves = []
        for wt, wst, wp in _chunks(kids[1][2], 4, len(kids[1][2])):
            parts = _chunks(wp, 0, len(wp))
            if wt != WAVE or [p[0] for p in parts] != [WAVE_HDR, WAVE_DATA]:
                raise ValueError("unexpected wave layout")
            w = Wave(parts[0][2], parts[1][2], wst)
            if w.size != len(w.pcm):
                raise ValueError(f"wave {w.name}: size field does not match the data")
            waves.append(w)
        if len(waves) != n:
            raise ValueError("wave count mismatch")
        return cls(stamp, kids[0][2], waves, kids[1][1])

    def build(self):
        lst = struct.pack("<I", len(self.waves)) + b"".join(
            _chunk(WAVE, w.stamp, _chunk(WAVE_HDR, w.stamp, w.header) + _chunk(WAVE_DATA, w.stamp, w.pcm)) for w in self.waves)
        return _chunk(WAVEDICT, self.stamp, _chunk(WAVEDICT_HDR, self.stamp, self.header) + _chunk(WAVELIST, self.list_stamp, lst))


# ---------------------------------------------------------------- voice stream
@dataclass
class Clip:
    name: str
    size: int      # bytes reserved in the data (multiple of PACKET)
    offset: int
    real_size: int  # bytes of ADPCM (multiple of 36)


@dataclass
class VoiceStream:
    stamp: int
    header: bytes     # raw 0x80E payload
    data: bytes       # raw 0x80F payload
    clips: list
    data_stamp: int

    @property
    def name(self):
        return self.header[0x50:0x60].split(b"\0")[0].decode("latin-1")

    @classmethod
    def parse(cls, data):
        top = _chunks(data, 0, len(data))
        if len(top) != 1 or top[0][0] != STREAM:
            raise ValueError("not a voice stream")
        t, stamp, payload = top[0]
        kids = _chunks(payload, 0, len(payload))
        if [k[0] for k in kids] != [STREAM_HDR, STREAM_DATA]:
            raise ValueError("unexpected stream layout")
        h, blob = kids[0][2], kids[1][2]
        n = struct.unpack_from("<I", h, 32)[0]
        pos_sizes = 96 + 32 * n
        pos_guid = pos_sizes + 4 * n
        pos_names = pos_guid + 16 * n
        if pos_names + 16 * n > len(h):
            raise ValueError("clip table does not fit the header")
        clips = []
        for i in range(n):
            size, off = struct.unpack_from("<II", h, 96 + 32 * i + 24)
            real = struct.unpack_from("<I", h, pos_sizes + 4 * i)[0]
            name = h[pos_names + 16 * i:pos_names + 16 * i + 16].split(b"\0")[0].decode("latin-1")
            if off + size > len(blob) or real > size or real % BLOCK or size % PACKET:
                raise ValueError(f"bad clip {i} ({name})")
            clips.append(Clip(name, size, off, real))
        if sum(c.size for c in clips) != len(blob):
            raise ValueError("clips do not cover the data")
        return cls(stamp, h, blob, clips, kids[1][1])

    def build(self):
        return _chunk(STREAM, self.stamp, _chunk(STREAM_HDR, self.stamp, self.header) + _chunk(STREAM_DATA, self.data_stamp, self.data))

    def clip_blocks(self, clip):
        base = clip.offset
        out = []
        for k in range(clip.real_size // BLOCK):
            pkt, i = divmod(k, BLOCKS_PER_PACKET)
            o = base + pkt * PACKET + i * BLOCK
            out.append(self.data[o:o + BLOCK])
        return out

    def decode(self, clip):
        """PCM16 mono bytes of a clip."""
        samples = []
        for b in self.clip_blocks(clip):
            samples += decode_block(b)
        return struct.pack(f"<{len(samples)}h", *samples)

    def wav(self, clip):
        return wav_bytes(self.decode(clip))


def load(path):
    data = Path(path).read_bytes()
    t = struct.unpack_from("<I", data, 0)[0]
    return (WaveDict if t == WAVEDICT else VoiceStream if t == STREAM else None).parse(data) if t in (WAVEDICT, STREAM) else _bad(t)


def _bad(t):
    raise ValueError(f"unknown RWS container type 0x{t:X}")


def summary(o):
    if isinstance(o, WaveDict):
        secs = sum(len(w.pcm) / 2 / w.rate for w in o.waves)
        return f"wave dictionary '{o.name}': {len(o.waves)} waves, {secs:.1f} s: " + ", ".join(w.name for w in o.waves[:6]) + (" ..." if len(o.waves) > 6 else "")
    secs = sum(c.real_size // BLOCK * 64 / RATE for c in o.clips)
    return f"voice stream '{o.name}': {len(o.clips)} clips, {secs:.1f} s at {RATE} Hz: " + ", ".join(c.name for c in o.clips[:4]) + " ..."


def main(argv):
    if len(argv) < 3 or argv[1] not in ("info", "export"):
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
    o = load(argv[2])
    out = Path(argv[3])
    out.mkdir(parents=True, exist_ok=True)
    items = [(w.name, w.wav()) for w in o.waves] if isinstance(o, WaveDict) else [(c.name, o.wav(c)) for c in o.clips]
    for i, (name, wav) in enumerate(items):
        safe = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in name) or f"clip{i}"
        (out / f"{i:03d}_{safe}.wav").write_bytes(wav)
    print(f"{len(items)} files -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

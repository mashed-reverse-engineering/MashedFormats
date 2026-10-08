"""Tests for rws.py (RenderWare Audio containers). Run: python -m unittest -v"""
import math, os, statistics, struct, tempfile, unittest
from pathlib import Path

import rws

GAME = Path(os.environ.get("PIZ_GAME_DIR", Path(__file__).parent.parent / "MashedGame"))
ST = 0x1C020018


def wave(name, pcm, rate=22050):
    h = bytearray(160)
    struct.pack_into("<IIII", h, 0, 15, rate, 0x01350C15, len(pcm))
    struct.pack_into("<I", h, 0x10, 0xFFFF0110)
    h[0x70:0x70 + len(name) + 1] = name.encode() + b"\0"
    return rws.Wave(bytes(h), pcm, ST)


def sine(n, amp=8000, period=50):
    return struct.pack(f"<{n}h", *[int(amp * math.sin(2 * math.pi * i / period)) for i in range(n)])


def voice_stream(clips):
    """clips: [(name, [block bytes])] -> VoiceStream built like the game's files."""
    n = len(clips)
    data = bytearray()
    entries, sizes, names = bytearray(), bytearray(), bytearray()
    for name, blocks in clips:
        offset = len(data)
        for k, b in enumerate(blocks):
            if k % rws.BLOCKS_PER_PACKET == 0 and k:
                data += bytes(rws.PACKET - rws.BLOCKS_PER_PACKET * rws.BLOCK)
            data += b
        data += bytes(-len(data) % rws.PACKET)
        size = len(data) - offset
        entries += struct.pack("<6I", 0, 0, 0, 0, 0, 0) + struct.pack("<II", size, offset)
        sizes += struct.pack("<I", len(blocks) * rws.BLOCK)
        names += name.encode().ljust(16, b"\0")
    h = bytearray(96)
    struct.pack_into("<I", h, 32, n)
    h[0x50:0x54] = b"tst\0"
    header = bytes(h) + bytes(entries) + bytes(sizes) + bytes(16 * n) + bytes(names) + bytes(100)
    clipobjs = []
    for (name, blocks), i in zip(clips, range(n)):
        e = struct.unpack_from("<II", header, 96 + 32 * i + 24)
        clipobjs.append(rws.Clip(name, e[0], e[1], len(blocks) * rws.BLOCK))
    return rws.VoiceStream(ST, header, bytes(data), clipobjs, ST)


class Adpcm(unittest.TestCase):
    def test_decode_known(self):
        # header predictor 100, index 0, all nibbles 0: every step adds step>>3 = 0 -> flat signal
        b = struct.pack("<hBB", 100, 0, 0) + bytes(32)
        self.assertEqual(rws.decode_block(b), [100] * 64)
        # nibble 7 at step index 0 (step 7): diff = 0 + 1 + 3 + 7 = 11, the index jumps by 8 (step 16), the next nibble 0 adds 16 >> 3 = 2
        b = struct.pack("<hBB", 0, 0, 0) + bytes([0x07]) + bytes(31)
        out = rws.decode_block(b)
        self.assertEqual(out[:3], [0, 11, 13])
        # sign nibble 15 goes negative
        b = struct.pack("<hBB", 0, 0, 0) + bytes([0x0F]) + bytes(31)
        self.assertEqual(rws.decode_block(b)[1], -11)
        # clamping
        b = struct.pack("<hBB", 32760, 88, 0) + bytes([0x77]) + bytes(31)
        self.assertEqual(rws.decode_block(b)[1], 32767)

    def test_bad_index(self):
        with self.assertRaises(ValueError):
            rws.decode_block(struct.pack("<hBB", 0, 200, 0) + bytes(32))

    def test_encode_decode_roundtrip_is_close(self):
        pcm = struct.unpack("<64h", sine(64, 6000, 32))
        block, _ = rws.encode_block(pcm)
        self.assertEqual(len(block), 36)
        back = rws.decode_block(block)
        self.assertEqual(back[0], pcm[0])
        err = statistics.mean(abs(a - b) for a, b in zip(pcm, back))
        self.assertLess(err, 700)  # 4 bit ADPCM of a quiet sine: a few percent of the amplitude

    def test_wav_header(self):
        w = rws.wav_bytes(b"\0\0" * 10, 8000)
        self.assertEqual(w[:4] + w[8:12], b"RIFFWAVE")
        self.assertEqual(struct.unpack_from("<I", w, 4)[0], len(w) - 8)
        self.assertEqual(struct.unpack_from("<IHHIIHH", w, 16), (16, 1, 1, 8000, 16000, 2, 16))
        self.assertEqual((w[36:40], struct.unpack_from("<I", w, 40)[0]), (b"data", 20))


class Synthetic(unittest.TestCase):
    def test_wave_dict_roundtrip(self):
        h = bytearray(68)
        h[0x34:0x38] = b"NT1\0"
        d = rws.WaveDict(ST, bytes(h), [wave("tyre on tarmac", sine(1000)), wave("beep", sine(64))], ST)
        data = d.build()
        back = rws.WaveDict.parse(data)
        self.assertEqual(back.build(), data)
        self.assertEqual((back.name, [w.name for w in back.waves], back.waves[0].rate, len(back.waves[0].samples())), ("NT1", ["tyre on tarmac", "beep"], 22050, 1000))
        self.assertEqual(back.waves[1].wav()[:4], b"RIFF")
        self.assertIn("2 waves", rws.summary(back))

    def test_wave_dict_errors(self):
        d = rws.WaveDict(ST, bytes(68), [wave("a", sine(10))], ST)
        data = bytearray(d.build())
        with self.assertRaises(ValueError):
            rws.WaveDict.parse(bytes(data[:-3]))
        data[12 + 12 + 68 + 12 + 4 + 12 + 12 + 12] = 99  # size field of the wave header
        with self.assertRaises(ValueError):
            rws.WaveDict.parse(bytes(data))
        with self.assertRaises(ValueError):
            rws.WaveDict.parse(b"\x0d\x08\0\0" + bytes(8))

    def test_voice_stream_roundtrip_and_decode(self):
        blocks = []
        pcm = struct.unpack("<%dh" % (64 * 300), sine(64 * 300, 5000, 40))
        pred_state = (0, 0)
        for i in range(300):
            b, pred_state = rws.encode_block(pcm[i * 64:(i + 1) * 64], pred_state)
            blocks.append(b)
        s = voice_stream([("1_1_1_RD_1", blocks), ("1_1_1_RD_2", blocks[:10])])
        data = s.build()
        back = rws.VoiceStream.parse(data)
        self.assertEqual(back.build(), data)
        self.assertEqual((back.name, [c.name for c in back.clips]), ("tst", ["1_1_1_RD_1", "1_1_1_RD_2"]))
        self.assertEqual([c.size for c in back.clips], [2 * rws.PACKET, rws.PACKET])  # 300 blocks need two packets (284 + 16)
        out = struct.unpack("<%dh" % (64 * 300), back.decode(back.clips[0]))
        self.assertEqual(len(out), 64 * 300)
        err = statistics.mean(abs(a - b) for a, b in zip(pcm, out))
        self.assertLess(err, 1200)
        self.assertEqual(len(struct.unpack("<640h", back.decode(back.clips[1]))), 640)
        self.assertEqual(back.wav(back.clips[1])[:4], b"RIFF")
        self.assertIn("2 clips", rws.summary(back))

    def test_voice_stream_errors(self):
        s = voice_stream([("a", [bytes(36)] * 3)])
        data = bytearray(s.build())
        with self.assertRaises(ValueError):
            rws.VoiceStream.parse(bytes(data[:-1]))
        bad = rws.VoiceStream(ST, s.header, s.data + bytes(rws.PACKET), s.clips, ST)  # clips no longer cover the data
        with self.assertRaises(ValueError):
            rws.VoiceStream.parse(bad.build())

    def test_cli(self):
        h = bytearray(68)
        h[0x34:0x37] = b"abc"
        d = rws.WaveDict(ST, bytes(h), [wave("w1", sine(100)), wave("w/2", sine(100))], ST)
        v = voice_stream([("c1", [bytes(36)] * 4)])
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "d.rws").write_bytes(d.build())
            (tmp / "v.rws").write_bytes(v.build())
            self.assertEqual(rws.main(["x", "info", str(tmp / "d.rws"), str(tmp / "v.rws")]), 0)
            self.assertEqual(rws.main(["x", "export", str(tmp / "d.rws"), str(tmp / "o1")]), 0)
            self.assertEqual(sorted(p.name for p in (tmp / "o1").iterdir()), ["000_w1.wav", "001_w_2.wav"])
            self.assertEqual(rws.main(["x", "export", str(tmp / "v.rws"), str(tmp / "o2")]), 0)
            self.assertEqual(len(list((tmp / "o2").iterdir())), 1)
            (tmp / "bad.rws").write_bytes(b"\x01\0\0\0" + bytes(20))
            self.assertEqual(rws.main(["x", "info", str(tmp / "bad.rws")]), 1)


AUDIO = GAME / "FullyLoaded" / "installed" / "App_Executables" / "toastaudio"


@unittest.skipUnless(AUDIO.is_dir(), "no installed game files")
class RealFiles(unittest.TestCase):
    def files(self):
        return sorted(AUDIO.rglob("*.rws"))

    def test_all_roundtrip(self):
        fs = self.files()
        self.assertEqual(len(fs), 59)
        kinds = {"WaveDict": 0, "VoiceStream": 0}
        for f in fs:
            data = f.read_bytes()
            o = rws.load(f)
            self.assertEqual(o.build(), data, f"{f} differs after rebuild")
            kinds[type(o).__name__] += 1
        self.assertEqual(kinds, {"WaveDict": 29, "VoiceStream": 30})

    def test_waves(self):
        n = 0
        for f in self.files():
            o = rws.load(f)
            if not isinstance(o, rws.WaveDict):
                continue
            self.assertTrue(o.name, f)
            for w in o.waves:
                n += 1
                self.assertEqual(w.rate, 22050, (f, w.name))
                self.assertEqual(w.size, len(w.pcm), (f, w.name))
                self.assertTrue(w.name, f)
                self.assertEqual(w.flags & 0xFFFF, 0x0110, (f, w.name))
                self.assertEqual(len(w.pcm) % 2, 0)
        self.assertEqual(n, 422)

    def test_wave_data_is_audio(self):
        """Most waves are smooth 16 bit signals: the sample to sample change is far below the amplitude."""
        smooth = total = 0
        for f in self.files():
            o = rws.load(f)
            if not isinstance(o, rws.WaveDict):
                continue
            for w in o.waves:
                s = w.samples()
                if len(s) < 200:
                    continue
                ma = statistics.mean(abs(x) for x in s)
                if ma < 50:
                    continue
                md = statistics.mean(abs(s[i + 1] - s[i]) for i in range(len(s) - 1))
                total += 1
                smooth += md / ma < 0.9
        self.assertGreater(smooth * 100 // total, 95)  # the rest are noise like rain, birds and cicadas

    def test_voice_streams(self):
        clips = 0
        for f in self.files():
            o = rws.load(f)
            if not isinstance(o, rws.VoiceStream):
                continue
            self.assertEqual(sum(c.size for c in o.clips), len(o.data), f)
            for c in o.clips:
                clips += 1
                self.assertTrue(c.name and c.real_size > 0, f)
                self.assertLessEqual(c.real_size, c.size, f)
            # decoded audio is continuous across block borders (a wrong block layout or sample order breaks this)
            c = max(o.clips, key=lambda c: c.real_size)
            blocks = [rws.decode_block(b) for b in o.clip_blocks(c)]
            jumps = statistics.mean(abs(blocks[i + 1][0] - blocks[i][-1]) for i in range(len(blocks) - 1))
            inner = statistics.mean(abs(b[j + 1] - b[j]) for b in blocks for j in range(63))
            self.assertLess(jumps, 2.0 * inner + 50, f)
        self.assertGreater(clips, 1500)

    def test_voice_lines_named_like_the_script(self):
        red = [rws.load(f) for f in self.files() if f.name == "red.rws" and f.parent.name == "english"][0]
        self.assertEqual(red.name, "red")
        self.assertEqual((len(red.clips), red.clips[0].name, red.clips[-1].name), (98, "1_1_1_RD_1", "1_4_1_RD_35"))
        sec = sum(c.real_size // 36 * 64 for c in red.clips) / 22050
        self.assertTrue(60 < sec < 200, sec)


if __name__ == "__main__":
    unittest.main()

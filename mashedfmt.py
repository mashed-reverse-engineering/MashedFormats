#!/usr/bin/env python3
"""One entry point for all Mashed file format tools (picks the parser by file extension).

  mashedfmt.py info FILE...       one line per file
  mashedfmt.py dump FILE          detailed dump (game formats) / chunk tree (RenderWare files)
  mashedfmt.py export FILE OUT    textures (also .CMP) -> PNG folder, DFF/BSP -> OBJ, BUM -> WAV, RWS -> WAV folder

Modules: mashed_formats (game formats), pitexd (PC texture dictionaries), nativetxd (PS2/Xbox texture dictionaries),
rwscene (DFF, BSP, ANM, SPL, UVA), rws (audio), rwchunks (generic chunk tree), lua_api (script functions).
Standard library only."""
import struct, sys, zlib
from pathlib import Path

import mashed_formats as mf
import nativetxd
import pitexd
import rwchunks
import rws
import rwscene


def _txd(path):
    data = Path(path).read_bytes()
    if data[:2] == b"\x78\xda":
        data = zlib.decompress(data)
    return data


def _kind(path):
    ext = Path(path).suffix.lower()
    if ext in (".txd", ".cmp"):
        t = struct.unpack_from("<I", _txd(path), 0)[0]
        return "pitexd" if t == 0x23 else "nativetxd" if t == 0x16 else None
    if ext in rwscene.PARSERS:
        return "rwscene"
    if ext == ".rws":
        return "rws"
    if ext in mf.PARSERS:
        return "mf"
    return None


def main(argv):
    if len(argv) < 3 or argv[1] not in ("info", "dump", "export"):
        print(__doc__)
        return 2
    cmd, files = argv[1], argv[2:]
    rc = 0
    if cmd == "export":
        src, out = Path(files[0]), Path(files[1])
        kind = _kind(src)
        if kind == "pitexd":
            return pitexd.main(["x", "export", str(src), str(out)])
        if kind == "nativetxd":
            return nativetxd.main(["x", "export", str(src), str(out)])
        if kind == "rwscene":
            return rwscene.main(["x", "obj", str(src), str(out)])
        if kind == "rws":
            return rws.main(["x", "export", str(src), str(out)])
        if src.suffix.lower() == ".bum":
            out.write_bytes(mf.Bum.parse(src.read_bytes()).to_wav())
            return 0
        if src.suffix.lower() == ".cmp":
            out.write_bytes(mf.cmp_decompress(src.read_bytes()))
            return 0
        print(f"{src}: nothing to export", file=sys.stderr)
        return 1
    for f in files:
        try:
            kind = _kind(f)
            if kind == "pitexd":
                d = pitexd.load(f)
                print(f"{f}: PI texture dictionary, {len(d.textures)} textures: " + ", ".join(t.name for t in d.textures[:8]) + (" ..." if len(d.textures) > 8 else ""))
            elif kind == "nativetxd":
                d = nativetxd.load(f)
                print(f"{f}: {d.textures[0].platform if d.textures else '?'} texture dictionary, {len(d.textures)} textures: " + ", ".join(t.name for t in d.textures[:8]))
            elif kind == "rwscene":
                print(f"{f}: {rwscene.summary(rwscene.load(f))}")
            elif kind == "rws":
                print(f"{f}: {rws.summary(rws.load(f))}")
            elif kind == "mf":
                print(f"{f}: {mf.summary(mf.load(f))}")
            elif Path(f).suffix.lower() in (".txd", ".cmp"):
                raise ValueError("unknown texture dictionary type")
            else:
                raise ValueError(f"no parser for {Path(f).suffix}")
            if cmd == "dump":
                data = _txd(f) if Path(f).suffix.lower() in (".txd", ".cmp") else Path(f).read_bytes()
                if kind in ("pitexd", "nativetxd", "rwscene"):
                    print("\n".join(rwchunks.dump(rwchunks.parse(data), 4)))
        except (ValueError, struct.error, OSError) as e:
            print(f"{f}: {e}", file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))

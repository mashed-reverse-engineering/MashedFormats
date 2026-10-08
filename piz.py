#!/usr/bin/env python3
"""Preview or extract Mashed .piz archives.

  piz.py FILE_OR_DIR...            preview: list the files inside each archive
  piz.py -r EXTRACTED_DIR -o OUT.piz [--game mashed|mfl]
                                   repack a directory made by -o (needs its piz.json)
  piz.py -o OUTDIR FILE_OR_DIR...  extract (OUTDIR/<archive path>/<files> + piz.json for repacking)
"""
import argparse, json, struct, sys
from pathlib import Path, PurePosixPath

TABLE, ENTRY = 0x800, 0x80  # file table at 0x800, 0x80 bytes per entry
MAX_FILES = {"Mashed (Drive to Survive)": 128, "Mashed Fully Loaded": 256}  # engine table buffer limits
MAX_FILES_BY_KEY = {"mashed": 128, "mfl": 256}
MANIFEST = "piz.json"  # written next to the extracted files; keeps what a repack needs


def name_hash(name):
    # Last entry field: sum(index * byte) over the file name. The case used is the
    # build machine's original one (stored names are always upper case), so this
    # only matches the stored name for upper-case originals.
    return sum(i * b for i, b in enumerate(name))


def safe_path(name):
    """Entry names may contain sub dirs (e.g. PS2/X.TXD). Keep them, but never leave the target dir."""
    parts = [p for p in PurePosixPath(name.replace("\\", "/")).parts if p not in ("", ".", "..", "/")]
    if not parts:
        raise ValueError(f"bad file name {name!r}")
    return Path(*parts)


def read_piz(data):
    """Returns (header, entries). header = (version, tail bytes at 0x0C), entries = (name, offset, size, hash)."""
    if data[:3] != b"PIZ":
        raise ValueError("not a PIZ file")
    header = (struct.unpack_from("<I", data, 4)[0], data[12:16])
    entries = []
    for i in range(data[8]):  # file count
        e = data[TABLE + i * ENTRY : TABLE + (i + 1) * ENTRY]
        raw = e[:0x74].split(b"\0")[0]
        offset, size, hash_ = struct.unpack_from("<III", e, 0x74)
        if offset + size > len(data):
            raise ValueError(f"{raw.decode('latin-1')}: data outside file")
        entries.append((raw.decode("latin-1"), offset, size, hash_))
    return header, entries


def write_piz(src, out, limit):
    """Build a .piz from an extracted dir. Files listed in piz.json keep their order and hash;
    other files found in the dir are appended (hash of the upper-case name, the game ignores it)."""
    m = json.loads((src / MANIFEST).read_text())
    listed = [(f["name"], f["hash"]) for f in m["files"]]
    names = {n for n, _ in listed}
    for f in sorted(src.rglob("*")):
        rel = f.relative_to(src).as_posix()
        if f.is_file() and f.name != MANIFEST and rel not in names:
            listed.append((rel, name_hash(rel.upper().encode("latin-1"))))
    if len(listed) > limit:
        raise ValueError(f"{len(listed)} files, the engine table holds only {limit}")
    align = lambda n: (n + 0x7FF) & ~0x7FF
    table, body, pos = bytearray(), bytearray(), align(TABLE + len(listed) * ENTRY)
    for name, h in listed:
        raw = name.encode("latin-1")
        if len(raw) > 0x73:
            raise ValueError(f"{name}: name longer than 0x73 bytes")
        data = (src / safe_path(name)).read_bytes()
        table += raw.ljust(0x74, b"\0") + struct.pack("<III", pos + len(body), len(data), h)
        body += data + b"\0" * (align(len(data)) - len(data))
    head = b"PIZ\0" + struct.pack("<II", m["version"], len(listed)) + bytes.fromhex(m["tail"])
    out.write_bytes(head.ljust(TABLE, b"\0") + table.ljust(pos - TABLE, b"\0") + body)
    return len(listed)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", type=Path)
    ap.add_argument("-o", "--out", type=Path, help="extract into this dir (default: preview only); with -r: the .piz to write")
    ap.add_argument("-r", "--repack", type=Path, metavar="DIR", help="build a .piz from an extracted DIR")
    ap.add_argument("--game", choices=MAX_FILES_BY_KEY, default="mfl", help="table limit to enforce when repacking (mashed=128, mfl=256)")
    a = ap.parse_args()
    if a.repack:
        if not a.out:
            ap.error("-r needs -o OUT.piz")
        try:
            n = write_piz(a.repack, a.out, MAX_FILES_BY_KEY[a.game])
        except (ValueError, OSError) as ex:
            print(f"{a.repack}: {ex}", file=sys.stderr); return 1
        print(f"{a.out}: {n} files")
        return 0
    if not a.paths:
        ap.error("give .piz files/dirs, or -r DIR -o OUT.piz")

    files = []  # (file, destination subdir)
    for p in a.paths:
        if p.is_dir():
            files += [(f, f.relative_to(p).with_suffix("")) for f in sorted(p.rglob("*")) if f.suffix.lower() == ".piz"]
        else:
            files.append((p, Path(p.stem)))
    rc = 0
    for f, sub in files:
        data = f.read_bytes()
        try:
            (version, tail), entries = read_piz(data)
        except ValueError as ex:
            print(f"{f}: {ex}", file=sys.stderr); rc = 1; continue
        ok = [g for g, m in MAX_FILES.items() if len(entries) <= m]
        print(f"{f}: {len(entries)} files, {sum(e[2] for e in entries)} bytes, "
              f"v{version}, tail {tail.hex()}, fits: {', '.join(ok) or 'neither game (too many files)'}")
        dest = a.out / sub if a.out else None
        if dest:
            dest.mkdir(parents=True, exist_ok=True)
        for name, off, size, h in entries:
            if dest is None:
                print(f"  {name:<40}{size:>10}  @0x{off:X}")
                continue
            target = dest / safe_path(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data[off : off + size])
        if dest:
            manifest = {"source": f.name, "version": version, "tail": tail.hex(),
                        "files": [{"name": n, "hash": h} for n, _, _, h in entries]}
            (dest / MANIFEST).write_text(json.dumps(manifest, indent=1))
    return rc


if __name__ == "__main__":
    sys.exit(main())

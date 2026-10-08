#!/usr/bin/env python3
"""The Lua functions the game engine registers for its course / powerup scripts (Lua 4).

  lua_api.py list EXE            registered names and the address of their C functions
  lua_api.py check EXE SCRIPT... report script calls that are not registered functions
  lua_api.py doc OUT.md EXE_MFL EXE_MASHED SCRIPT...   write the LUA_API.md table

The engine registers every function with the same helper: `push <function>; push "<name>"; call <register>` (found by
scanning the code for that byte pattern: 68 <function> 68 <string> E8 <call>; the helper is the most called target).
Standard library only."""
import collections, os, re, struct, sys
from pathlib import Path

PAT = re.compile(rb"\x68(....)\x68(....)\xe8(....)", re.S)
NAME = re.compile(rb"[A-Za-z_][A-Za-z_0-9]{3,}")


def _sections(d):
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    ns = struct.unpack_from("<H", d, pe + 6)[0]
    so = pe + 24 + struct.unpack_from("<H", d, pe + 20)[0]
    base = struct.unpack_from("<I", d, pe + 52)[0]
    return base, [struct.unpack_from("<8sIIII", d, so + i * 40) for i in range(ns)]


def registered(path):
    """({lua name: C function address}, address of the register helper)."""
    d = Path(path).read_bytes()
    base, secs = _sections(d)

    def to_off(va):
        for _, vs, v, rs, ro in secs:
            if base + v <= va < base + v + max(vs, rs):
                return ro + va - base - v
        return None

    def to_va(off):
        for _, vs, v, rs, ro in secs:
            if ro <= off < ro + rs:
                return base + v + off - ro
        return None

    text_end = max(base + v + vs for n, vs, v, rs, ro in secs if n.startswith(b".text"))
    cands, calls = [], collections.Counter()
    for m in PAT.finditer(d):
        func, name, rel = struct.unpack("<III", m.group(1) + m.group(2) + m.group(3))
        o = to_off(name)
        if o is None or to_off(func) is None or not (base + 0x1000 <= func < text_end):
            continue
        s = d[o:o + 64].split(b"\0")[0]
        if not NAME.fullmatch(s):
            continue
        va = to_va(m.start() + 10)
        target = (va + 5 + struct.unpack("<i", struct.pack("<I", rel))[0]) & 0xFFFFFFFF
        cands.append((s.decode(), func, target))
        calls[target] += 1
    if not calls:
        raise ValueError("no Lua registrations found")
    helper = calls.most_common(1)[0][0]
    return {n: f for n, f, t in cands if t == helper}, helper


def script_calls(paths):
    """{function name: [argument strings]} over the given Lua scripts (comments removed)."""
    out = collections.defaultdict(list)
    for p in paths:
        s = re.sub(r"--[^\n]*", "", Path(p).read_bytes().decode("latin-1"))
        for m in re.finditer(r"\b([A-Za-z_][A-Za-z_0-9]*)\s*\(([^()]*)\)", s):
            out[m.group(1)].append((m.group(2).strip(), Path(p).parent.name))
    return out


def arg_kind(a):
    a = a.strip()
    if a.startswith('"'):
        return "string"
    if re.fullmatch(r"[-+]?\d+", a):
        return "int"
    if re.fullmatch(r"[-+]?\d*\.?\d+", a):
        return "float"
    return "NAME"


def signature(calls):
    shapes = collections.Counter()
    for a, _ in calls:
        parts = [p for p in a.split(",") if p.strip()] if a else []
        shapes[tuple(arg_kind(p) for p in parts)] += 1
    return " or ".join("(" + ", ".join(s) + ")" for s, _ in shapes.most_common(2))


def table(mfl, mashed, scripts):
    rm, fm = registered(mfl)
    rd, fd = registered(mashed)
    calls = script_calls(scripts)
    rows = []
    for n in sorted(set(rm) | set(rd)):
        c = calls.get(n, [])
        rows.append((n, f"0x{rm[n]:X}" if n in rm else "-", f"0x{rd[n]:X}" if n in rd else "-", len(c), signature(c),
                     f"{n}({c[0][0]})" if c else ""))
    return rows, fm, fd, len(rm), len(rd)


def main(argv):
    if len(argv) < 3 or argv[1] not in ("list", "check", "doc"):
        print(__doc__)
        return 2
    if argv[1] == "list":
        r, helper = registered(argv[2])
        print(f"register helper at 0x{helper:X}, {len(r)} functions")
        for n, a in sorted(r.items(), key=lambda x: x[1]):
            print(f"0x{a:X}\t{n}")
        return 0
    if argv[1] == "check":
        r, _ = registered(argv[2])
        calls = script_calls(argv[3:])
        missing = sorted(n for n in calls if n not in r and n[0].isupper())
        print(f"{len(calls)} called names, {sum(n in r for n in calls)} registered; unregistered capitalised names: {missing}")
        return 0
    rows, fm, fd, nm, nd = table(argv[3], argv[4], argv[5:])
    with open(argv[2], "w") as f:
        f.write(f"| Lua function | MFL.exe | MASHED.exe | calls | argument kinds | example |\n|---|---|---|---|---|---|\n")
        for n, a, b, c, s, e in rows:
            f.write(f"| `{n}` | {a} | {b} | {c} | {s} | `{e}` |\n")
    print(f"{len(rows)} functions -> {argv[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

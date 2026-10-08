# Mashed PIZ archives (piz.py)

Preview and extract the `.piz` archives of Mashed / Mashed Fully Loaded. Runs on Linux, macOS and Windows.

## Usage
```
python piz.py PATH...             # preview the files in each archive (dirs are searched recursively)
python piz.py -o OUT PATH...      # extract to OUT/<archive path>/, plus piz.json (see below)
python piz.py -r DIR -o NEW.piz   # repack an extracted DIR (add --game mashed to enforce the 128 file limit, default mfl = 256)
```
Repack keeps the order, hash, version and tail bytes from `piz.json`; files in DIR that are not listed there are appended. It refuses too many files and names over 0x73 bytes.
All 86 shipped archives extract and repack **byte for byte identical**.

## Tests
```
python -m unittest -v
```
Synthetic tests cover the layout, alignment/padding, hash, sub folders, limits, name length, bad input, path traversal and the CLI. Real-archive tests extract and repack every `.piz` under `../MashedGame` (override with `PIZ_GAME_DIR`) and compare byte for byte; they are skipped when no game files are present.

## Format (as far as understood)
All 86 archives (2699 files) of both games follow this exactly.

| Offset | Meaning |
|---|---|
| 0x00 | `PIZ\0`, u32 version = 3 |
| 0x08 | u32 file count |
| 0x0C | 4 bytes, either `00 00 00 00` or `CC CC CC CC` (uninitialised padding, ignored) |
| 0x10..0x800 | zeros |
| 0x800 + n*0x80 | file table entry: 0x74-byte NUL padded upper-case name, u32 offset, u32 size, u32 hash |
| first offset | first 0x800 boundary after the table |

File data is stored back to back, each file starting on a 0x800 boundary, zero padded; the archive ends on a 0x800 boundary.

The hash is `sum(i * byte[i])` over the file name, `i` starting at 0, using the original case of the file name on the build machine.
Stored names are always upper case. This is why the hash matches the stored name for only 96 files, while 1709 match its lower-case form and the rest a mixed-case form.
**Confirmed by disassembling MFL.exe** (PIZ open `0x4C6AC0`, lookup `0x4C6A70`, read `0x4C6E50`):
- The hash is **never read** by the game. Lookup is a linear scan over the table calling `stricmp(entry.name, requested)`, so names are case-insensitive. It returns `entry.size`; reads use `entry.offset`.
- The hash is a build-tool artefact (the original-case file name hash, e.g. the game's own strings `english.dat`, `USA.dat`). A rebuilt archive can use any value; the upper-case hash is fine.
- The signature `PIZ`, the version and the 4 bytes at 0x0C are **not checked**, only copied.
- The table is read into a fixed 0x8000-byte buffer: **at most 256 files per archive**.
- The file is opened with `FILE_FLAG_NO_BUFFERING | FILE_FLAG_OVERLAPPED`, which is why offsets and the table are 0x800-aligned and the file ends on a 0x800 boundary: reads must be sector aligned.
- Hash-casing is therefore not a rebuild problem. Names longer than 0x73 bytes would break the layout.

## MASHED.exe (Drive to Survive) differences
Same code as MFL.exe (open `0x4B6940`, lookup `0x4B6BA0`/`0x4B6C00`, read `0x4B6CD0`, seek `0x4B6ED0`, eof `0x4B6F90`), with one limit changed:
- Mashed's table buffer is 0x4000 bytes: **at most 128 files per archive** (MFL: 256). The count is not checked, so more would overflow the buffer. Largest real archive: 86 files (Drive to Survive), 84 (Fully Loaded).
- Entry fields used by the engine: +0x74 offset, +0x78 size. +0x7C is never read.
- The engine caches reads in 0x20000-byte windows; seek supports set/current/end, eof is `position >= size`.
- Platform switch at startup: PC (platform 6, also 1 and 5) uses unbuffered overlapped `CreateFile` reads (alignment matters). Other platforms (2, 3, 4, 7) use stdio `fseek/fread`.

## Names, sub folders and the manifest
Entry names may contain sub folders (`PS2/BADGES.TXD`, `XBOX/BADGES.TXD`, `TOASTART/COMMON/PERM/COPTERS/COPTER.DFF`, 50 entries in total). They are extracted as real sub folders (an earlier flat version lost 31 files to name clashes: all 2699 entries are extracted now).
Each extracted archive gets a `piz.json` with what a repack needs and the files themselves do not carry: file order, original hash per file, format version and the 4 tail bytes at 0x0C (`00000000` in 57 archives, `CCCCCCCC` in 29; no pattern by game, probably uninitialised build memory).
The preview line also shows which game's table limit the archive fits (Drive to Survive 128, Fully Loaded 256).

## How the engine finds files (loose files vs. archives)
Decompiled from MFL.exe (RenderWare `RtFS` file system manager, `0x560980`-`0x561300`); MASHED.exe is the same code.
- There is a list of registered file systems and **one default file system**. A path `name:rest` selects the file system `name:`; any other path goes to the default one. **There is no fallback chain.**
- At startup (`0x4A4720`) a native disk file system rooted at the current directory (plus one per drive letter) is registered and becomes the default. Loose files (e.g. `TOASTART\PC\PC.TXD`) are read through it.
- Opening a PIZ (`0x4C6AC0`) saves the previous default file system and makes **the PIZ the default**; closing it restores the saved one. While a PIZ is open only its entries resolve; a loose file with the same name is ignored.
- Content lookup (`0x42F6E0`, log text `Searching piz for %s`) tries, in this order, `pc/NAME`, `NAME`, `xbox/NAME`, `ps2/NAME`, `gamecube/NAME`, and logs `Unable to find %s in piz` if none exists. So a PIZ may contain a `PC/NAME` entry that wins over `NAME` on PC; the shipped PIZ files only contain `XBOX/` and `PS2/` variants.
- If a PIZ can not be opened (missing file, `FAILED` in the log), the default stays the disk file system, so the same lookups run against loose files relative to the game folder. **Inference from the code, not tested in the game**: deleting a `.piz` and providing its files loose (e.g. `pc/COURSE.LUA` or `COURSE.LUA` next to the exe) would be a way to override content without repacking.
- Piz paths are normalised by `0x403450` (`FileName is too long (%dchars max)`): drive letters are dropped, upper-cased, directory and file names at most **8 letters**, whole path at most **49 characters**. That is why the hard-coded `d:\toastart\common\font36.piz` still opens relative to the working directory.

## RenderWare version
Both executables statically link **RenderWare Graphics 3.7.0.2** (the last 3.x release; library stamp `0x37002`, chunk stamp `0x1C02000A`):
- Both contain a function that is just `mov eax, 0x37002; ret` (MFL `0x4D2F10`, MASHED `0x4C2D80`), i.e. the `RwEngineGetVersion`-style current-version getter. The constant is used ~60 times (MFL and MASHED), e.g. `push 0x37002` when writing chunk headers for the custom `0x132699xx` chunks (the `.LED` and `.AI` file headers start with `01 99 26 13` / `02 99 26 13`).
- Asset chunk stamps in the extracted data agree: most DFF/TXD/BSP/ANM files are stamped 3.7.0.2 (`0x1C02000A`); older files are 3.6.0.3 (`0x1803FFFF`), 3.5.0.0 (`0x1400FFFF`), 3.4.0.3/3.4.0.5. The engine loads older stamps too.
- The file system layer is RenderWare's `RtFS` ("RtFSManagerRegister/Open" log strings). Other engine pieces present: `RenderwareAttachPlugins`, Lua (`Copyright (C) 1994-2000 TeCGraf, PUC-Rio`, a Lua from around 2000, likely 4.0), zlib 1.1.x.
- Search terms: "RenderWare Graphics 3.7", "RtFS", "rwID", "RwStreamFindChunk", and the RW chunk header format (type, size, version stamp: `version = ((stamp >> 14) & 0x3FF00) + 0x30000 | ((stamp >> 16) & 0x3F)`, build = low 16 bits).

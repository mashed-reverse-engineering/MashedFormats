# Mashed file formats (extracted from the PIZ archives)

Tools (Python 3.8+, standard library only, nothing to install; `python mashedfmt.py info|dump|export ...` picks the right one):
| Module | Formats |
|---|---|
| [mashed_formats.py](mashed_formats.py) | LED, AI, AID, MTS, GSD, DAT, RWF, REP, BUM, CMP (game specific) |
| [pitexd.py](pitexd.py) | PC texture dictionaries (chunk 0x23), PNG export |
| [nativetxd.py](nativetxd.py) | PS2 and Xbox texture dictionaries (chunk 0x16), PNG export |
| [rwscene.py](rwscene.py) | DFF (clump), BSP (world), ANM, SPL, UVA; OBJ export of DFF/BSP; lossless chunk tree |
| [rws.py](rws.py) | RenderWare Audio containers (`.rws`): wave dictionaries and voice streams, WAV export |
| [lua_api.py](lua_api.py) | the Lua functions registered by the executables ([LUA_API.md](LUA_API.md)) |
| [rwchunks.py](rwchunks.py) | generic RenderWare chunk tree dump |
| [mashedfmt.py](mashedfmt.py) | dispatcher over all of the above |

Tests: `python -m unittest -v` (67 tests: synthetic ones for every format, plus every shipped file is parsed and rebuilt
**byte for byte** and checked for plausibility; real file tests need the extracted files under `../MashedGame/*/extracted`,
see [PIZ.md](PIZ.md), and are skipped otherwise).
How things were found: loader functions in MFL.exe / MASHED.exe (Ghidra, see `../ghidra`), then checked against all shipped files.

All numbers little endian. "Stamp" = RenderWare library stamp (`0x1C02000A` = 3.7.0.2 build 10, `0x1803FFFF` = 3.6.0.3).
RenderWare chunk header = `u32 type, u32 size, u32 stamp`, followed by `size` bytes.

## Overview
| Extension | Count | What | Status |
|---|---|---|---|
| `.TXD` (type 0x23) | 100 | RenderWare platform independent texture dictionary | decoded, PNG export |
| `.CMP` | 1 | `TRACKIMAGES.TXD.CMP` = zlib (level 9) compressed PI texture dictionary | decoded |
| `.LED` | 46 | per track camera angle table | decoded (meaning of fields from the code) |
| `.AI` | 41 | per track bot driving data (AI editor state) | decoded |
| `.AID` | 5 | per track list of 72 byte records | structure decoded, meaning open |
| `.MTS` | 317 | list of matrices for placing clumps | decoded |
| `.GSD` | 9 | small parameter blocks (SNOW 48 bytes, BOID 36 bytes) | words decoded, meaning mostly open |
| `.DAT` | 14 | language string tables (UTF-16) | decoded |
| `.RWF` | 13 | RenderWare Rt2d font (chunk 0x199) | decoded |
| `.BUM` | 34 | 43008 bytes = 21504 signed 16 bit values, no header (in `SFX`) | raw 16 bit samples, **unreferenced by both executables**; WAV export (rate guessed) |
| `.REP` | 2 | time attack ghost replays (`BEST<n>.REP`, 256716 bytes) | decoded (records, quaternion + position frames) |
| `.TXD` (type 0x16) | 36 | PS2 (32 files) and Xbox (4 files) texture dictionaries | decoded, PNG export |
| `.DFF` | 1389 | RenderWare clump (frames, geometries, atomics) | decoded, OBJ export |
| `.BSP` | 126 | RenderWare world (plane / atomic sectors) | decoded, OBJ export |
| `.ANM` `.SPL` `.UVA` | 135/57/24 | HAnim animation, spline, UV animation dictionary | decoded |
| `.RWS` | 59 | RenderWare Audio: 29 wave dictionaries (sound effects) and 30 voice streams (speech, 221 MB) | decoded, WAV export |
| `.LUA` | 105 | course / powerup scripts (Lua 4 text) | the 71 engine functions are listed in [LUA_API.md](LUA_API.md) |
| `.MPG` | 7 | intro / menu videos (MPEG-1 program streams, `00 00 01 BA`), 123 MB | ordinary MPEG-1, play with any player |
| `.LST` | 4 | plain text lists of texture names, some lines with a leading number (`2 crosshair`) | readable |
| `videocfg.bin`, `contcfg<n>.bin`, `gamesave.bin` | runtime | created by the game: raw memory dumps (0x200, 0x200, 0x42334 bytes) of settings / save structs | `mashed_config.py` |
| `.LUA .TXT` | 105/147 | plain text (course description, powerups, defines, ...) | readable |
| `.PNG .BMP .DDS .ICO .LOG` | | ordinary files | |
| `.SCC` | 2 | Visual SourceSafe version file left in the build, not used by the game | |
| `.LED_X` | 2 | LED copies (same format) | |

## PI texture dictionary (`.TXD` type 0x23, `.CMP`)
```
chunk 0x23 (rwID_PITEXDICTIONARY)
  u16 numTextures, u16 deviceId (always 1)
  numTextures times:
    u32 numImages                       mip levels, each half the size of the previous (checked on all files)
    numImages x chunk 0x18 (IMAGE)
      chunk 0x01 (STRUCT, 16 bytes): u32 width, height, depth (4, 8 or 32), stride
      palette: (4 << depth) bytes = 2^depth RGBA entries (depth 4 or 8 only)
      pixels: height * stride bytes (4 bit: two pixels per byte, low nibble = left pixel)
    chunk 0x06 (TEXTURE)
      chunk 0x01: u32 filtering | addressing (low byte filter mode, bits 8-11 U and 12-15 V addressing; 0x1104 typical)
      chunk 0x02 texture name, chunk 0x02 mask name (NUL padded to 4 bytes)
      chunk 0x03 extension (empty in all files)
```
Fonts (and some UI images) have an all zero palette: the pixel values are intensity, drawn as white with that alpha
(`pitexd.Image.rgba` does this; the font atlas `FGDC20` renders correctly with it). Over all 100 files there are 9752 images with
8 bit, 2759 with 4 bit and 1195 with 32 bit (RGBA bytes) pixels. The statically linked RenderWare toolkit `rtpitexd`
(`RtPITexDictionaryStreamRead`, found with the FID database) is what reads these in the game; the layout above matches it.

## LED (`LE<n>.LED`, `Common/LED`)
RenderWare chunk type `0x13269901` (custom), size `0x1200`: **384 records of 3 floats**. `-1.0` = unset (the game clears the whole
table to -1.0 before loading, `FUN_0040ac10`). Consumer `FUN_0044a530` (camera): for the record of the current track segment,
`field0` is subtracted from a base pitch angle and `field1` is added to a base yaw angle to build the camera direction from the
vector (0,-1,0); `field2` is handed back as an extra value (tilt). Typical values: `field0` 15.0, `field1` 60/90/180, `field2` -1.0.
The in-game editor (cases 0xC-0x10 in `FUN_0040ad10`) sets index, the three fields and saves the file. The meaning of the name LED
("line exception" is what the log says when the table is cleared) is not certain.

## AI (`AI<n>.AI`, `Common/AI`)
RenderWare chunk type `0x13269902`, size `0x11884`; the dump of the game's AI editor state:
```
0x00000  int16[128][128]      grid, cell = ((coordinate + 512) >> 3) in x and z (8 world units per cell); tile index, -1 = no tile
0x08000  uint8[512][8][8]     tile pool (tile 0 unused) = "danger data": 0xFF = empty, otherwise the brush value 0..3 the
                              editor painted (value 2 is tested by FUN_00417c30); only 0, 1, 2, 3, 0xFF occur
0x10000  int32                highest tile index in use ("memory=%d" in the editor)
0x10004  4 sets x 3 slots x { float x,z [64]; int32 count }      0x204 bytes each
0x11834  80 bytes             runtime state of the 4 bots saved by accident (5 words each, last one cut off)
```
The four route sets are the line types of the built-in AI editor menu (`FUN_0044e4d0`, strings found in the code):
**set 0 "race lines", 1 "inside lines", 2 "slow lines", 3 "cheat lines"**; the other menu entries are "danger data" (the tile map),
"save data", "clear data", "race options" (reset car, start/stop race, single step, lap frames) and per route "Edit Spline n /
Edit Point n / Insert Point / Delete Point". A route is a polyline of at most 64 (x, z) points; each set holds 3 variants (slots).
A bot uses the slot selected for it in its line type and falls back to slot 0 of the race lines when that route has fewer than
4 points (`FUN_0041b4e0`). In the shipped files race-line slot 0 always exists, slot 1 often, slot 2 rarely, the cheat lines
only in 10 tracks.
Loaded from `ai.piz`; editor functions: `FUN_004271a0` (clear), `FUN_004271e0` (editor), `FUN_0041a090/0041a170`
(paint/erase a cell), `FUN_0044d2a0` (nearest point on a route). All 41 files satisfy: counts <= 64, tile indices < 512,
route coordinates within +-600, cell values as above.

## AID (`AI1.AID`: City, sands, SandSnow)
`u32 count`, then `count` records of 18 floats (72 bytes) = six 3-vectors; most vectors are unit directions, some look like
track positions (x -40..1, y 0..8, z -26..77). **Unused**: neither executable contains ".AID" and no code opens such a file.
Meaning of the six vectors unknown.

## MTS
`u32 count` (the game rejects more than 0x30, "Too many matrices"), then `count` times a RenderWare matrix chunk:
`chunk 0x0D (size 0x40) { chunk 0x01 (size 0x34) { float right[3], up[3], at[3], pos[3]; u32 flags } }`
(= `RwMatrixStreamWrite`). Used by `Mts_Filename(n, "x.mts")` in `COURSE.LUA`: placement of clumps (trees, fences, ...),
read by the course loader (`FUN_004893e0`, `FUN_00493040`) together with physics shapes (box/cylinder).

## GSD
Raw `u32` array (`Common/Perm`). Loader: `FUN_0044a040` in **MFL.exe only** (called by the track setup `FUN_0042b450`) reads
`Boid<track>.gsd` into a 0x30 byte global at `0x8C7E80` (defaults `400,128,?,100,128,128,128,0,?,1,0,0` if the file is missing).
**Nothing else reads that global** (no other reference to it in the executable) and MASHED.exe has no loader, so the effect
(flock / snow particles) is dead code in the shipped game. `SNOW<n>.GSD` (12 words, e.g. `(116, 30, 149, 100, 255, 255, 255, 216,
255, 1, 1, 0)`, tracks 1, 5, 13, 20, 23, 25, 39) does not match the loaded name and is never opened; `BOID<n>.GSD` (9 words,
`(983, 115, 299, 316, 216, 1000, 216, 932, 532)`, tracks 23 and 34) would be. Words 4-6 of the SNOW layout look like an RGB colour;
the rest is not decoded and cannot be tested because nothing uses it.

## DAT (`<LANGUAGE>.DAT`, `Common/Font36`)
`u32 offsets[N]` (N = first offset / 4 = 834 in Fully Loaded, 677 in Drive to Survive), then per string `u16 length` (UTF-16 units)
and the UTF-16LE text. Control characters 0x08, 0x09 are button icons. The offsets are authoritative: the Drive to Survive files
have 2 zero bytes after string 73, the last string is followed by `0D 0A`. The game picks the file with `FUN_0042ba20`
(`english.dat`, `french.dat`, ... `:USA.dat`).

## RWF (RenderWare Rt2d font)
Chunk type `0x199` = `rwID_2DFONT`, read by the statically linked `Rt2dFontStreamRead` (decompiled in MFL.exe at `0x564E40`).
The chunk size field is larger than the data (by 192/194/120 bytes), so it is kept raw and not used.
```
u32 version 0x01000001, i32 type (0 = texture font), f32 height (34.0, 33.0 for FGDC), f32 scale (~0.15), i32 0, i32 2
i32 numLowMap (128), i32 numGlyphs (224/225/61), i32 numHighMap (128)
u16 high_map[numHighMap]      glyph index of the characters 128..255
u16 low_map[128]              glyph index of the characters 0..127, 0xFFFF = none
numGlyphs x { f32 u0, v0, u1, v1; f32 advance; u8 texture index }       texture rectangle in 0..1
i32 numTextures, numTextures x char name[32]       (e.g. "fgdc20" = the font atlas in FGDC20.TXD)
```
The sizes add up exactly in all 13 files. `Rwf.glyph_for(code)` gives the glyph of a character.

## CMP
`TRACKIMAGES.TXD.CMP`: plain zlib stream (`78 DA`), level 9 gives the identical bytes. Inside: a PI texture dictionary
(the track preview images).

## PS2 and Xbox texture dictionaries (`.TXD` type 0x16, `PS2/` and `XBOX/` folders)
```
chunk 0x16 TEXDICTIONARY: STRUCT(u16 numTextures, u16 deviceId: 6 = PS2, 8 = Xbox), numTextures x TEXTURENATIVE (0x15), EXTENSION
PS2 TEXTURENATIVE: STRUCT(u32 'PS2\0', u32 filterAddressing), STRING name, STRING mask,
                   STRUCT { STRUCT(64 byte raster header), STRUCT(pixels + palette) }, EXTENSION (SkyMipmap plugin, raw)
  raster header: u32 width, height, depth, u16 rasterFormat, i16 version (2), u64 tex0, u32 paletteOffset, u32 tex1low,
                 u64 miptbp1, miptbp2, u32 pixelSize, paletteSize, totalSize, mipmapVal
  rasterFormat 0x2504 = 8 bit palette, 0xB504 = palette + mipmaps, 0xD504 = 4 bit palette + mipmaps, 0x9504 = 32 bit
  pixels: per mip level a 0x50 byte GIF packet, then the level data (16 byte aligned, width >= 16 / height >= 4 for 8 bit,
          32 / 4 for 4 bit): 8/4 bit images are stored swizzled ("PSMT8 as PSMCT32", `ps2_unswizzle8/4`)
  palette: 0x50 byte GIF packet + 256 (or 16) RGBA entries; for 256 entries the entries i with (i & 0x18) == 0x10 are swapped
           with i ^ 0x18 (CSM1); alpha 0..0x80 -> 0..255
Xbox TEXTURENATIVE: STRUCT(u32 5, u32 filterAddressing, char name[32], char mask[32], i32 rasterFormat, u16 hasAlpha, u16 0,
                    u16 width, height, u8 depth (bits per stored pixel), numLevels, type, compression (0 here), i32 totalSize,
                    palette (16 entries are stored as 32 for PAL4, 256 for PAL8, BGRA), pixel data), EXTENSION
  pixel data: all mip levels back to back (only the whole block padded to 4 bytes), each swizzled in Morton order
Both layouts follow librw (src/ps2/ps2raster.cpp, src/d3d/xbox.cpp).
```
Checks: the PS2 and Xbox atlases decode to the same kind of picture (PS2FGDC20/XboxFGDC20 show the full ASCII font plus pad
buttons and controllers), 32 bit textures are pixel identical on both platforms, 24 and 4 bit textures agree, and the PS2 level
sizes add up to `pixelSize` for all 175 textures. Compressed Xbox formats (DXT) do not occur in the shipped files.

## DFF, BSP, ANM, SPL, UVA (standard RenderWare 3.7 streams)
`rwscene.Tree` is a lossless chunk tree (rebuild is byte identical for all 1731 files); the typed views decode:
```
DFF  CLUMP { STRUCT(numAtomics, numLights, numCameras), FRAMELIST { STRUCT(u32 n, n x {3x3 matrix, pos, i32 parent, u32 flags}),
     EXTENSION* }, GEOMETRYLIST { STRUCT(u32 n), GEOMETRY* }, ATOMIC* { STRUCT(frame, geometry, flags, 0) }, EXTENSION }
     GEOMETRY { STRUCT(flags, numTriangles, numVertices, numMorphTargets, [prelit colors RGBA], [uv sets], triangles as
     u16 {v1, v0, material, v2}, per morph target {bounding sphere, hasVertices, hasNormals, vertices, normals}),
     MATLIST { STRUCT(count, indices), MATERIAL* { STRUCT(flags, RGBA, 0, textured, ambient, specular, diffuse), TEXTURE } },
     EXTENSION { BINMESH (0x50E): u32 tristrip flag, numMeshes, totalIndices, per mesh {numIndices, material, indices} } }
BSP  WORLD { STRUCT(64: rootIsWorldSector, inverse origin, numTriangles, numVertices, numPlaneSectors, numAtomicSectors,
     colSectorSize, format, bounding box), MATLIST, sector tree, EXTENSION }
     PLANESECT { STRUCT(axis, value, left/right is world sector, left value, right value), left, right }
     ATOMICSECT { STRUCT(material base, numTriangles, numVertices, box, collSectorPresent, 0, vertices, [normals], [colors], [uvs],
     triangles as u16 {v0, v1, v2, material}) } (note: not the geometry word order); format 0x4001004D = prelit, textured
ANM  ANIMANIMATION: i32 0x100, i32 type 1 (HAnim), i32 numFrames, i32 flags, f32 duration, then per key frame (36 bytes)
     f32 time, f32 quaternion[4], f32 translation[3], i32 previous key frame (byte offset; garbage for the first key of a node)
SPL  SPLINE (0x0C): 8 zero words (runtime pointers), u32 numControlPoints, u32 1, then numControlPoints x (x, y, z)
UVA  UVANIMDICT { STRUCT(count), ANIMANIMATION* { i32 0x100, i32 0x1C1, numFrames, flags, f32 duration, i32 0, char name[32],
     i32 nodeToUVChannel[8], per frame f32 time, f32 uv[6] (matrix: scale u/v, offset), i32 previous frame } }
```
Checks (all shipped files): every triangle index is below the vertex count and every material index below the material count,
BSP header totals equal the sector sums, quaternions are unit length, a BSP of the HighwayN track renders as a road with ramps
(top view) and a vehicle DFF as a car. Units are small: a car is about 1 unit long, tracks are 300 wide.

## REP (ghost replays)
`BEST<n>.REP` (Perm folder) is the game's ghost buffer (`FUN_004957f0` writes it): consecutive records
```
header, 0x19C bytes (a memory copy of the replay object; word index = u32 index):
  [0] id 0x10A / 0x10B, [1] size of the whole record, [2] track, [3] f32 target time in s (30, 40, 50, 60, 70, 100, 110),
  [4] 8, [5..7] runtime address of the frame buffer, [8] address of its end (= start + 36 * frames),
  [92] index of the last recorded frame, [93] frame capacity, [94] 2, [95..98] times in ms (multiples of 50)
body: 20 bytes (1, 1, 0, 0, 0), then frames x 36 bytes          size = 0x19C + 20 + 36 * frames, exactly
frame: f32 quaternion x, y, z, w; f32 position x, y, z; u32 runtime address of the frame (+36 per frame);
       u32 time stamp in ms (50, 450, 850, ... every 400 ms, else 0)
```
The car is sampled 7.5 times per second: frames = target time x 7.5 (`FUN_00494460(time, 15)` = time x 15 / 2); only the first
`[92] + 1` frames hold data (~40% of them: the best run is shorter than the allowed time), the rest has zeros in the quaternion
and position. A file has 12 records (one per time-attack track), then zero padding up to the reserved buffer size (256716 bytes; the
code checks its own list size against 0x4178C and hangs with "GHOST Size ERROR" otherwise). The code names are `%sAll%d.rep`
(whole ghost list) and `%sReplay%d.rep` (single replay), written to `U:\\toast\\`.

## BUM
34 files `N.BUM` (43008 bytes) in `Common/SFX`: signed 16 bit little endian values without header. Neither executable contains
the string `.BUM` or `BUM`, so they are probably unused sample data (the signal looks like noisy audio, +-8000). `Bum.to_wav`
wraps them as 22050 Hz mono WAV (the rate is a guess).

## Plugin data inside DFF / BSP files
Besides the geometry, the `EXTENSION` chunks carry plugin data. All of it is decoded now (rwscene.py):
| Plugin | Where | Meaning |
|---|---|---|
| `0x11F` user data | geometry, material, BSP sector | named arrays (int / float / string). Found: `0.tv_part_id` (12,176 geometries; one int for the geometry or one per triangle: vehicle part ids such as 80-92, 1301), `0.Part_ID`, `FVF.UserData` (12291 = 0x3003, a Direct3D vertex format) and per reflective material `JaCFX.baseTexture` (string, e.g. ReflectedSky, JaCFXshine), `JaCFX.cfxType` (1-4) and `JaCFX.cfxBlend` (0 or 2) |
| `0x120` MatFX | material (effect list), atomic / sector (enable flag 1) | effect type + two slots: dual texture (1,726 materials: second texture, src/dst blend), UV transform (114), environment map (9), bump + environment map (2), dual UV transform (3) |
| `0x135` UV animation | material | `STRUCT { u32 1, char name[32] }`: name of the UVA animation that drives the material (all names exist in some `.UVA`, except `bmp_river_M` in RIVER.DFF and `skyDome3_M` in SKYDOME02.DFF) |
| `0x105` morph | geometry | interpolators `{flags, start target, end target, time}`; 28 geometries have one (0 -> 1) |
| `0x11E` HAnim | frame | `{version 0x100, node id (-1 none), numNodes}`; no hierarchies with nodes in the data apart from ids 0, 3, 4 |
| `0x11D` collision | BSP atomic sector (217) | a kd-style `RpCollTree`: bbox, split nodes and an entry map that is a permutation of the sector's triangles; `numEntries` equals the sector's triangle count |
| `0x50E` BINMESH | geometry, sector | triangle list/strip per material (14,178 geometries) |
| `0x511` vertex format | geometry | 4 bytes (0) |
| `0x26990003` | material | game specific, 48 bytes: two zero words, then 0xDD debug heap fill (uninitialised memory); carries no data |

## Audio (`toastaudio/pc/audio/pcdics/**/*.rws`)
59 files, 221 MB. The runtime is RenderWare Audio 3.7 (`rwa.dll` is in the SDK).
```
0x809 wave dictionary:  0x80A (68 bytes, name char[16] at +0x34), 0x80C { u32 count, count x 0x802 { 0x803 header, 0x804 data } }
  0x803 header (160 or 176 bytes): u32 15, u32 sample rate (22050 in all 422 waves), u32 runtime, u32 data size, u32 flags
        (low 16 bits 0x0110, high 16 bits vary), ..., GUIDs, char name[] at +0x70 ("tyre on tarmac", "bird1", ...)
  0x804 data: signed 16 bit little endian mono PCM (checked: smooth signals; only rain, birds, cicadas are noisy)
0x80D voice stream:     0x80E header + 0x80F data      (english/french/german/italian/spanish x 6 characters, tracks' music)
  0x80E: u32 numClips at +32, char stream name at +0x50 ("red"), at +96 numClips x 32 bytes {4 words, u32 size, u32 offset},
         then u32 exact ADPCM byte size per clip, 16 byte GUID per clip, char name[16] per clip ("1_1_1_RD_1": track_?_?_character_line)
  0x80F: clips back to back, each a multiple of 10240 byte packets: 284 blocks of 36 bytes + 16 bytes padding.
         Block = Xbox / IMA ADPCM, mono: int16 predictor, u8 step index, u8 0, 32 bytes of nibbles (low first) = 64 samples
         (the header predictor is sample 0, the last nibble unused). Decoded audio is continuous across blocks and packets.
```
The voice stream sample rate is not stored in a field that was found; 22050 Hz (same as the dictionaries) is assumed. The executable
supports the rate for the sound effects: `sfx_play_at` (0x4726A0 in MFL.exe) starts every voice of an effect with the frequency
`0x46AC4400` = 22050.0, exactly the rate in the 0x803 headers. The 98 lines
of one character are about 2.5 to 6 minutes. The dictionaries per track (`city.rws`, `forest.rws` ...) hold the ambient sounds,
`PERMDICT.RWS` the shared effects (explosions, missiles, ...).

## Lua scripts
See [LUA_API.md](LUA_API.md): all 71 functions the engine registers (69 in Drive to Survive), their C function addresses in both executables, usage counts
and argument kinds from the 105 shipped scripts. The registrations are found by a byte pattern, so the table is checked against the executables by tests.

## Settings and save files written by the game (`mashed_config.py`)
None of them ships with the game. Each one is `fwrite(&global, 1, size, file)` of a global struct in MFL.exe (the Ghidra names are
`g_video_cfg`, `g_controller_cfg`, `g_savegame`; structs `VideoCfg`, `ControllerCfg`, `GameState` in `ghidra/names/MFL_structs.txt`).
All numbers are little endian. The parser keeps every byte, so editing a field and rebuilding is exact.

**`videocfg.bin`** (0x200 bytes, global `0x7712F8`, load `video_cfg_load`, save `video_cfg_save`):

| Offset | Field |
|---|---|
| 0x000 | u32 version, 5 (written on save, not checked) |
| 0x004 | char[80] Direct3D adapter name |
| 0x104 | char[64] text of the video mode |
| 0x184 / 0x188 | adapter index / mode index (`RwEngineSetSubSystem`, `RwEngineSetVideoMode`) |
| 0x18C / 0x190 / 0x194 | width, height, depth |
| 0x198 | fullscreen (mode flags & 1) |
| 0x19C, 0x1A0, 0x1A4, 0x1A8 | Performance Settings dialog: Multi-Texturing (on when 0), Dynamic Shadows (on when 0), Trilinear Filtering (on when not 0), Environmental Effects (on when 0) |

**`contcfg<n>.bin`** (0x200 bytes, controller n = 0..3 of the array at `0x7E7740`, load `controller_cfg_load`, defaults `controller_cfg_init`):

| Offset | Field |
|---|---|
| 0x000 | u32 version, 6 |
| 0x004 | char[260] device name (joystick product name, or the localised word Keyboard) |
| 0x108 | u32[13] bindings: accelerate, brake, fire, drop, menu select, menu back, pause, menu next, insult, left, right, up, down |
| 0x13C | u32 device: 0 none, 1 gamepad, 2 keyboard |
| 0x140 | u32 joystick index |

A bound value is a button number (gamepad) or a DirectInput scancode (keyboard); left/right/up/down on a gamepad are the stick axes and
their slots are not read. The slots menu select/back/next have no entry in the dialog (they copy accelerate/brake/fire).
Defaults: gamepad buttons 0 1 2 3 0 1 4 2 5, keyboard S X A D Enter Esc Esc A C and the arrow keys. At start the game builds the defaults for
all four slots (gamepads found first, then the keyboard, the rest none) and keeps a loaded file only if its version is 6 and its name equals the
detected device's name.

**`gamesave.bin`** (0x42334 bytes, global `0x801B40`; written by `gamesave_write` after `gamesave_gather`, read by `gamesave_load` + `gamesave_apply`;
the surrounding state machines are the memory-card style autosave code of the console versions):

| Offset | Content |
|---|---|
| 0x00000 | u32 `0xDEADBEEF` |
| 0x00004 | 0x4178C bytes: the ghost buffer (`g_replay_buffer`, a list of REP records, see REP above; its size is the sum of all track times x 7.5 frames x 36 bytes plus headers, the game stops with "GHOST Size ERROR" otherwise) |
| 0x41790 | 0xB64 bytes: game state block `GameState` (`g_game_state` at `0x7EEBC0`) |
| 0x422F4 | 0x40 bytes of the global that the gather code never writes |

`GameState`: 0x000 u32 progress[15][18] (0 locked, 1 open, 2 done; the game sets a cell to 2 when an event is won and opens the next ones),
0x438 i32 records[15][5] (-1 = empty), 0x7BC u32 best minutes[45], 0x870 u32 best seconds[45], 0x924 f32 best fraction[45] (the three best
times per course; defaults 9 min 59.xx s), 0x9D8 u8 enabled[15][18], 0xB00 five option slider words, 0xB2C five option flags, 0xB54 mirrored
track mode (the course id is increased by 1000), 0xB58 12 bytes, one per player slot. Not decoded: the meaning of the 15 rows and 18 columns
(the column index selects an event type, 3/4/5 and 14/15/16 are the groups the code treats specially), the unused 600 bytes at 0x564, and the
option fields beyond the slider/flag grouping. No real save file was available, the tests build one.

## Loose files
- `.MPG`: MPEG-1 program streams (intro, frontend, backdrop, publisher logos), standard format.
- `.LST` files list texture names per folder (`textures.lst`, `PowerUpIcons.lst`); a leading number is a flag of the loader.
- `videocfg.bin`, `contcfg<n>.bin`, `gamesave.bin`: see the next section.
- `disc.iso` / `Mashed.iso` / `bin_contents`: InstallShield installers; their content is what is installed (`installed/`), nothing extra.

## Not understood yet
Only things the game itself never uses remain open, so they cannot be tested against the game's behaviour:
- `.AID` (six 3-vectors per record) and the `.GSD` words: no code reads them (see above).
- `.BUM`: no reference at all; the sample rate for the WAV export is a guess.
- REP header words [94] and [95..98] (2 and four times in ms): written by the game, only read back as part of the whole
  record; the meaning of the times was not derived.
- Compressed Xbox textures (DXT): not present in the shipped files, so not implemented.

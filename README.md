# MashedFormats

Python tools and documentation for the file formats of **Mashed** (Drive to Survive) and **Mashed Fully Loaded**.
Python 3.8+, standard library only, nothing to install. Runs on Linux, macOS and Windows.

| What | Where |
|---|---|
| `.piz` archives: preview, extract, repack (byte for byte identical for all 86 shipped archives) | [piz.py](piz.py), [PIZ.md](PIZ.md) |
| Everything inside the archives (LED, AI, MTS, GSD, RenderWare DFF/BSP/TXD/RWS, ...) | [mashedfmt.py](mashedfmt.py), [FORMATS.md](FORMATS.md) |
| Lua functions registered by the executables | [LUA_API.md](LUA_API.md) |

## Quick start
```
python piz.py -o OUT PATH/TO/GAME            # extract every .piz (OUT/<archive>/... + piz.json)
python mashedfmt.py info OUT/**/*            # one line per file
python mashedfmt.py export FILE OUT          # textures -> PNG, DFF/BSP -> OBJ, audio -> WAV
python piz.py -r OUT/<archive> -o NEW.piz    # repack
```

## Tests
```
python -m unittest -v
```
Synthetic tests run everywhere. Tests against the real game files are skipped when absent; point `PIZ_GAME_DIR` at the game folder (default `../MashedGame`).

## History
Replaces the C# GUI [MashedFileExtractor](https://github.com/mashed-reverse-engineering/MashedFileExtractor) (archived) and the former MashedPIZExtractor.

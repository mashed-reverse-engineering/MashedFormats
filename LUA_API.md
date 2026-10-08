# Lua functions of the game engine

Course scripts (`TOASTART/TRACKS/<track>/COURSE.LUA`, the powerup scripts, `LAPDATA.LUA`, ...) are Lua 4 text. The functions they call are C
functions registered by the executable with one helper (`push <function>; push "<name>"; call <register>`, helper at `0x48CF50` in MFL.exe and
`0x47B980` in MASHED.exe). `MashedFormats/lua_api.py` finds them by scanning for that byte pattern (`python lua_api.py list MFL.exe`).

The table lists **all 71 registered names** (MFL.exe has 71, MASHED.exe 69; `-` = not registered there), the address of the C function in each
executable, how often the shipped scripts call it and the argument kinds seen in those calls (`int`, `float`, `string`, `NAME` = a constant from
`DEFINES.TXT`). 51 of them occur in the shipped scripts, the others are registered but never called. Every capitalised name that a script calls is one of
them. In Ghidra the functions are named `lua_<name>` (`../ghidra/names/*_lua_names.csv`; names that share one C function are joined with `__`).
Fully Loaded added two: `ClumpFade` and `ExpObj_Clump_Mts_Pair`.

| Lua function | MFL.exe | MASHED.exe | calls | argument kinds | example |
|---|---|---|---|---|---|
| `AI_Bsp_Filename` | 0x48B9D0 | 0x47A540 | 41 | (string) | `AI_Bsp_Filename("ai.bsp")` |
| `AI_Data_Filename` | 0x48BA10 | 0x47A580 | 0 |  | `` |
| `Ambient_RGB` | 0x48BF60 | 0x47AAD0 | 0 |  | `` |
| `Camera_Anim_Filename` | 0x48BD10 | 0x47A880 | 0 |  | `` |
| `ClumpFade` | 0x48CD80 | - | 58 | (int, int, int, float) or (NAME, int, int, float) | `ClumpFade(3 , 0 , 29 , 1.0)` |
| `Clump_Exclude_From_World` | 0x48BEB0 | 0x47AA20 | 396 | (int) or (NAME) | `Clump_Exclude_From_World(8)` |
| `Clump_Filename` | 0x48B830 | 0x47A3A0 | 502 | (int, string) or (NAME, string) | `Clump_Filename(2,"sea.dff")` |
| `Clump_Mts_Pair` | 0x48BDE0 | 0x47A950 | 15 | (int, int) | `Clump_Mts_Pair(12,0)` |
| `Collision_Bsp_Filename` | 0x48BB40 | 0x47A6B0 | 41 | (string) | `Collision_Bsp_Filename("collisions.bsp")` |
| `Course_Id` | 0x405E40 | 0x47A180 | 30 | (int) | `Course_Id(0)` |
| `Course_Name` | 0x405E40 | 0x5C9D00 | 0 |  | `` |
| `ExpObj_Clump_Mts_Pair` | 0x48CCF0 | - | 30 | (int, int) or (int, int, NAME) | `ExpObj_Clump_Mts_Pair(2 , 1)` |
| `General_Anim_Filename` | 0x48BD40 | 0x47A8B0 | 117 | (int, string) | `General_Anim_Filename(0 ,"H_Anim_1.anm")` |
| `Hoppy_Bsp_Filename` | 0x48BA70 | 0x47A5E0 | 0 |  | `` |
| `KTC_AddPickUp` | 0x48CC60 | 0x47B7D0 | 58 | (NAME, float, float, float, NAME, float) | `KTC_AddPickUp(KTC_NUM, -27.440, 0.4, 26.143, MACHINE_GUN, 5.0)` |
| `KTC_NewCopter` | 0x48CBB0 | 0x47B720 | 6 | (string, int) | `KTC_NewCopter("KTC_Apache", 5)` |
| `Lap_Line` | 0x48BC20 | 0x47A790 | 87 | (int) | `Lap_Line(0)` |
| `Lap_Line_Change` | 0x48BC80 | 0x47A7F0 | 0 |  | `` |
| `Lap_Line_End` | 0x48BC60 | 0x47A7D0 | 36 | () | `Lap_Line_End()` |
| `Lap_Variations` | 0x48BBB0 | 0x47A720 | 41 | (int) | `Lap_Variations(1)` |
| `Lights_Filename` | 0x48BF30 | 0x47AAA0 | 41 | (string) | `Lights_Filename("Lights.dff")` |
| `Modify_Fog` | 0x48C060 | 0x47ABD0 | 2 | (int, int, int, int, int, int) | `Modify_Fog(180,0,300,213,208,179)` |
| `Mts_Filename` | 0x48B710 | 0x47A280 | 153 | (int, string) or (NAME, string) | `Mts_Filename(1,"OrangeLights.mts")` |
| `Object_Bsp_Filename` | 0x48BAA0 | 0x47A610 | 2 | (int, string) | `Object_Bsp_Filename(0, "Trees.bsp")` |
| `Occluder_Filename` | 0x48B640 | 0x47A1B0 | 8 | (string) | `Occluder_Filename("Occluder.dff")` |
| `Physics_Object` | 0x48BE40 | 0x47A9B0 | 0 |  | `` |
| `Physics_Scenario_Add_Animated_Object` | 0x48C910 | 0x47B480 | 0 |  | `` |
| `Physics_Scenario_Add_Objects` | 0x48C8B0 | 0x47B420 | 21 | (NAME, int) | `Physics_Scenario_Add_Objects(HUT_A_SCENARIO, 5)` |
| `Physics_Scenario_Create` | 0x48C7E0 | 0x47B350 | 15 | (string) | `Physics_Scenario_Create("Column Pair 1")` |
| `Physics_Scenario_Create_Trigger` | 0x48C990 | 0x47B500 | 19 | (NAME, NAME, NAME) | `Physics_Scenario_Create_Trigger(COLUMN_PAIR1, ON_MOVE, IMMEDIATE)` |
| `Physics_Scenario_Get_Function` | 0x48CAC0 | 0x47B630 | 8 | (string) | `Physics_Scenario_Get_Function("EgyptPushColumn1")` |
| `Physics_Scenario_Set_Sequence` | 0x48C850 | 0x47B3C0 | 7 | (NAME, NAME, NAME, NAME) or (NAME, NAME, NAME) | `Physics_Scenario_Set_Sequence(COLUMN_PAIR1, PUSH_COLUMN_1_FUNC, COLUMN_PAIR2, PUSH_COLUMN_2_FUNC)` |
| `Physics_Scenario_Trigger_Add_Objects` | 0x48CA40 | 0x47B5B0 | 25 | (NAME, int, float) or (NAME, int, int) | `Physics_Scenario_Trigger_Add_Objects(HUT_A_TRIGGER1, 5, 100)` |
| `Physics_Scenario_Trigger_Set_AI_Poly` | 0x48CB40 | 0x47B6B0 | 6 | (NAME, int, int) | `Physics_Scenario_Trigger_Set_AI_Poly(COLUMN_PAIR1_TRIGGER, 0, 36)` |
| `Place_Powerup` | 0x48C6E0 | 0x47B250 | 224 | () | `Place_Powerup()` |
| `Powerup_Filename` | 0x48BEE0 | 0x47AA50 | 0 |  | `` |
| `RWP_Object` | 0x48C270 | 0x47ADE0 | 120 | (int, string, string, string) | `RWP_Object(0, "White Crash Tyres", "WhiteTyre.dff", "WhiteTyre.mts")` |
| `RWP_Object_BaseLabel` | 0x48C5A0 | 0x47B110 | 120 | (int, NAME) | `RWP_Object_BaseLabel(0, WHITE_TYRE)` |
| `RWP_Object_Box` | 0x48C310 | 0x47AE80 | 78 | (int, float, float, float) or (int, float, float, int) | `RWP_Object_Box(0, 0.6, 0.77, 0.6)` |
| `RWP_Object_Capsule` | 0x48C430 | 0x47AFA0 | 41 | (int, float, float) | `RWP_Object_Capsule(0 , 0.56 , 10.24)` |
| `RWP_Object_CentreOfMassOffset` | 0x48C530 | 0x47B0A0 | 0 |  | `` |
| `RWP_Object_Cylinder` | 0x48C380 | 0x47AEF0 | 1 | (int, float, float) | `RWP_Object_Cylinder(20 , 1.02 , 1.53)` |
| `RWP_Object_Properties` | 0x48C490 | 0x47B000 | 120 | (int, NAME, NAME, NAME, float, NAME) or (int, NAME, NAME, NAME, int, NAME) | `RWP_Object_Properties(0, TYRE_FATNESs, BOUNCE, FRICTION, 0.5 , RUBBER)` |
| `RWP_Object_ShatterVel` | 0x48C5F0 | 0x47B160 | 108 | (int, int) or (int, float) | `RWP_Object_ShatterVel(0 , 100)` |
| `RWP_Object_Sphere` | 0x48C3E0 | 0x47AF50 | 0 |  | `` |
| `RainEnable` | 0x4A0AC0 | 0x4917A0 | 5 | () | `RainEnable()` |
| `RainSetCameraScale` | 0x4A0D30 | 0x491A10 | 0 |  | `` |
| `RainSetDirection` | 0x4A0D90 | 0x491A70 | 0 |  | `` |
| `RainSetHeadColour` | 0x4A0AE0 | 0x4917C0 | 0 |  | `` |
| `RainSetPosition` | 0x4A0C20 | 0x491900 | 0 |  | `` |
| `RainSetScale` | 0x4A0CD0 | 0x4919B0 | 0 |  | `` |
| `RainSetTailColour` | 0x4A0B80 | 0x491860 | 0 |  | `` |
| `Safe_Start_Lines` | 0x48BCA0 | 0x47A810 | 132 | (int, int) | `Safe_Start_Lines(0,5)` |
| `SetClumpAnim` | 0x48C1D0 | 0x47AD40 | 1 | (NAME, int, int) | `SetClumpAnim(LINEAR,0,3)` |
| `SetCopter` | 0x48B7B0 | 0x47A320 | 101 | (string, int) | `SetCopter("Copter" , 0)` |
| `Set_Current_Position` | 0x48C640 | 0x47B1B0 | 224 | (float, float, float) or (int, float, int) | `Set_Current_Position(-23.3, 0.27, 39.4)` |
| `Set_Current_Respawn_Time` | 0x48C6C0 | 0x47B230 | 224 | (int) | `Set_Current_Respawn_Time(5)` |
| `Set_Current_Type` | 0x48C690 | 0x47B200 | 224 | (NAME) | `Set_Current_Type(OIL)` |
| `Set_Track_Powerups` | 0x48C710 | 0x47B280 | 65 | (NAME, NAME, NAME, NAME, NAME, NAME, NAME) | `Set_Track_Powerups(TIMGIDSKI , BRONZE , NONE , NONE , NONE , NONE , NONE)` |
| `Setup_Fog` | 0x48BFC0 | 0x47AB30 | 40 | (int, int, int, int, int) or (float, int, int, int, int) | `Setup_Fog(0.1,70,40,44,48)` |
| `Shadow_Scale` | 0x48C190 | 0x47AD00 | 5 | (float) | `Shadow_Scale(.3)` |
| `Shine_Scale` | 0x48C160 | 0x47ACD0 | 0 |  | `` |
| `Sky_Filename` | 0x48B670 | 0x47A1E0 | 44 | (int, string) or (string) | `Sky_Filename(0,"sky.dff")` |
| `Spline_Filename` | 0x48B930 | 0x47A4A0 | 56 | (int, string) | `Spline_Filename(1,"Wave.spl")` |
| `Split_Sector` | 0x48BBE0 | 0x47A750 | 106 | (int, int) | `Split_Sector(0, 26)` |
| `Texture_Dictionary_Filename` | 0x48BB80 | 0x47A6F0 | 41 | (string) | `Texture_Dictionary_Filename("textures.txd")` |
| `UVA_Filename` | 0x48B8D0 | 0x47A440 | 22 | (int, string) | `UVA_Filename(0 , "Sea.uva")` |
| `Vehicle_Body_Dirt_Texture` | 0x48C1C0 | 0x47AD30 | 0 |  | `` |
| `Vehicle_Shininess_Range` | 0x48C110 | 0x47AC80 | 16 | (float, float) or (float, int) | `Vehicle_Shininess_Range(0.6,1.0)` |
| `Vehicle_Wheel_Dirt_Texture` | 0x48C1C0 | 0x47AD30 | 0 |  | `` |
| `World_Bsp_Filename` | 0x48BA40 | 0x47A5B0 | 41 | (string) | `World_Bsp_Filename("graph.bsp")` |

## What they do (from the names, the calls and the loader code)
- **Course files** (loaded by `FUN_004893e0`): `Texture_Dictionary_Filename`, `World_Bsp_Filename`, `Collision_Bsp_Filename`, `AI_Bsp_Filename`,
  `Object_Bsp_Filename(n, file)`, `Hoppy_Bsp_Filename`, `Lights_Filename`, `Occluder_Filename`, `Sky_Filename(n, file)`, `Clump_Filename(n, file)`,
  `Mts_Filename(n, file)` / `Clump_Mts_Pair(clump, mts)` (matrix lists for placing a clump), `UVA_Filename(n, file)`, `Spline_Filename(n, file)`,
  `General_Anim_Filename(n, file)` / `Camera_Anim_Filename`, `AI_Data_Filename`, `Course_Id`, `Course_Name`.
- **Scene setup:** `Setup_Fog` / `Modify_Fog`, `Ambient_RGB`, `Shadow_Scale`, `Vehicle_Shininess_Range`, `RainEnable`, `ClumpFade`,
  `Clump_Exclude_From_World(n)`.
- **Race logic:** `Lap_Line`, `Lap_Line_End`, `Lap_Variations`, `Safe_Start_Lines`, `Split_Sector`.
- **Pick-ups:** `Set_Current_Position(x, y, z)`, `Set_Current_Type(TYPE)`, `Set_Current_Respawn_Time(s)`, `Place_Powerup()` (one block of four calls per
  powerup box, 224 in the shipped scripts), `Set_Track_Powerups(a ... g)`, `KTC_AddPickUp(...)`, `KTC_NewCopter`, `SetCopter`.
- **Physics objects** (`RWP_*`, RenderWare Physics): `RWP_Object(n, label, dff, mts)`, `RWP_Object_Properties(...)` (fatness, bounce, friction, mass,
  material such as RUBBER), `RWP_Object_BaseLabel`, `RWP_Object_ShatterVel`, shapes `RWP_Object_Box / Capsule / Cylinder`; `Physics_Scenario_*` build scripted
  events (create a scenario, add objects, create a trigger ON_MOVE / IMMEDIATE, set an AI polygon, set a sequence of functions such as `PUSH_COLUMN_1_FUNC`).

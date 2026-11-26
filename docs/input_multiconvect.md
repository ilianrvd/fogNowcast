# Вход от MultiConvect: `fog_fields_<model>.npz`

> Съгласувано и реализирано от MultiConvect на 2026-11-26 (PR #6 там). Верният формат е в **`docs/fog_fields.md` на MultiConvect** (https://github.com/ilianrvd/multiconvect/blob/main/docs/fog_fields.md); тук е само това, на което се опира fogNowcast. Реализиран е само ICON-EU; таблиците за GFS и IFS по-долу са от черновата. Файловете: `https://ilianrvd.github.io/multiconvect/fog_fields_icon_eu.npz` и `.../fog_fields_meta.json`. Първото production публикуване е следващият рън (около 22:50 UTC на 2026-11-26).

## Обхват

- Район: същият като в MultiConvect (`config.yaml`: 40.3–45.1 N, 21.1–31.3 E).
- Водещо време: до +30 ч от рън, по часове. Картата е 0–12 ч, но свежият рън изисква по-дълъг хоризонт за най-новите рънове.
- Модели: `icon_eu`, `gfs`, `ifs`, на нативната мрежа на всеки, без превъртане на мрежата. Изключение е IFS, който е на 3 часа и се интерполира.

## Резолюции (каквото MultiConvect тегли сега)

| Модел | Източник | Мрежа | Стъпка | Роля за мъглата |
|---|---|---|---|---|
| ICON-EU | DWD Open Data | 0.0625° (около 6.5 км) | 1 ч | основно поле, най-финото отворено за България |
| GFS | NOMADS, `pgrb2.0p25` | 0.25° (около 25 км) | 1 ч | съгласие на мащаба |
| IFS | ECMWF Open Data, `oper` | 0.25° | 3 ч | съгласие на мащаба |

ICON-D2 (2.2 км) не покрива България. ALADIN няма отворена мрежа (само Open-Meteo по точки).

## Параметри

Проверено в листинга на DWD (`icon-eu/grib/00/`, 25.11.2026) и в стандартния inventory на GFS `pgrb2.0p25`. При IFS е проверено само основното: 2t, 2d, 10u, 10v, msl, sp.

| Ключ | Величина | ICON-EU (DWD) | GFS | IFS |
|---|---|---|---|---|
| `t2m` | T 2 m, °C | `t_2m` | `TMP:2 m above ground` | `2t` |
| `td2m` | Td 2 m, °C | `td_2m` | `DPT:2 m above ground` | `2d` |
| `rh2m` | RH 2 m, % | `relhum_2m` | `RH:2 m above ground` | — (от 2t/2d) |
| `u10`, `v10` | вятър 10 m, kt (превръща се от m/s при запис) | `u_10m`, `v_10m` | `UGRD`/`VGRD:10 m above ground` | `10u`, `10v` |
| `vis` | видимост, m | `vis` ✓ | `VIS:surface` | няма |
| `ceiling` | височина на облачната база (ceiling), m | `ceiling` ✓ | `HGT:cloud ceiling` | — |
| `clcl` | ниска облачност, % | `clcl` | `LCDC:low cloud layer` | `lcc`? да се провери в inventory |
| `ww` | значимо време (WMO ww, 45/48 = мъгла) | `ww` ✓ | — | — |
| `mslp` | налягане на морското равнище, hPa | `pmsl` | `PRMSL:mean sea level` | `msl` |
| `tg` | температура на повърхността, °C | `t_g` | — | — |
| `hsurf` | височина на клетката, m (статично) | `hsurf` | `HGT:surface` | — |

✓ = в листинга на ICON-EU има поле `vis`, `ceiling` и `ww`. Това е моделна видимост и мъгла, сравнима директно с METAR.

Каквото липсва за даден модел, просто липсва в неговия файл.

## Формат (както е реализиран)

- `.npz` (не `.bin`): fogNowcast го чете в Actions, не в браузъра. `fog_fields_meta.json` е със заглавния блок на портала (`schema` `multiconvect.fog_fields/1.0`, `generated_at`, `source`, `run`, `cadence_min` 360), плюс `grid` и `vars`.
- `run` е ISO низ на рънa на ICON-EU и е равен на `run` в `latest_points.json` за същото пускане. Когато се добавят GFS и IFS, рънът на всеки модел отива в `models.<модел>.run`.
- Масиви: `lat` (възходящ), `lon`, `valid_time` (секунди от 1970 UTC), `hsurf` (ny, nx) и по (nt, ny, nx) int16: `t2m`, `td2m`, `u10`, `v10`, `clcl`. Стойност = `raw * scale + offset`; мащаби 0.01 за `t2m`, `td2m`, `u10`, `v10` (вятърът в kt, като компоненти), 0.1 за `clcl`, 1 за `hsurf`. Липсва: `raw == fill` (−32768).
- Район 40.3–45.1 N, 21.1–31.3 E, мрежа 0.0625° (77 × 160), f000–f030 по часове, рънове 00/06/12/18.
- Потребителите пренебрегват непознати ключове; `vis` (около 2 m на единица) и `ww` (uint8) са по-късна стъпка.
- Измерено при пробно пускане: около 2,7 MB за рън.

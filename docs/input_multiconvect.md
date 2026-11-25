# Вход от MultiConvect: `fog_fields_<model>.npz`

> Чернова, за съгласуване. Файлът се създава и публикува от **MultiConvect** и се описва и в неговия repo. Тук е описано какво очаква fogNowcast.

## Обхват

- Район: същият като в MultiConvect (`config.yaml`: 40.3–45.1 N, 21.1–31.3 E).
- Водещо време: 0–12 ч, по часове (по желание до 24 ч).
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

## Формат

```
fog_fields_icon_eu.npz
  lat        (ny,)            float32
  lon        (nx,)            float32
  valid_time (nt,)            int64, секунди от 1970-01-01 UTC
  hsurf      (ny, nx)         int16, m
  t2m        (nt, ny, nx)     int16, scale 0.01, offset 0
  ...
fog_fields_meta.json
```

`fog_fields_meta.json`:

```json
{
  "generated_at": "2026-11-25T05:20:00Z",
  "source": "MultiConvect",
  "run": {"icon_eu": "2026-11-25T00:00:00Z", "gfs": "2026-11-25T00:00:00Z", "ifs": "2026-11-24T18:00:00Z"},
  "models": {
    "icon_eu": {"file": "fog_fields_icon_eu.npz", "time_step_h": 1, "interpolated_hours": []},
    "ifs":     {"file": "fog_fields_ifs.npz", "time_step_h": 1, "interpolated_hours": [1, 2, 4, 5]}
  },
  "vars": {"t2m": {"units": "degC", "scale": 0.01, "offset": 0, "fill": -32768}}
}
```

- `interpolated_hours`: кои часове са интерполирани (при IFS на 3 ч), за да ги различава верификацията.
- `run` е по модел, защото IFS може да е от предишен рън.

# Вход от MultiConvect: `fog_fields_<model>.npz`

> Чернова, за съгласуване. Файлът се създава и публикува от **MultiConvect** и се описва и в неговия repo. Тук е описано какво очаква fogNowcast.

## Обхват

- Район: около 41.0–44.4 N, 22.2–28.8 E (окончателно се решава в MultiConvect).
- Водещо време: 0–12 ч, по часове (по желание до 24 ч).
- Модели: `icon_eu`, `ifs`, `gfs`, на нативната мрежа на всеки, без превъртане на мрежата.

## Параметри

| Ключ | Величина | ICON-EU | GFS | IFS |
|---|---|---|---|---|
| `t2m` | T 2 m, °C | T_2M | TMP 2 m | 2t |
| `td2m` | Td 2 m, °C | TD_2M | DPT 2 m | 2d |
| `rh2m` | RH 2 m, % | RELHUM_2M | RH 2 m | от 2t/2d |
| `u10`, `v10` | вятър 10 m, m/s | U_10M, V_10M | UGRD, VGRD | 10u, 10v |
| `clcl` | ниска облачност, % | CLCL | LCDC | lcc |
| `vis` | видимост, m | да се провери | VIS surface | вероятно няма |
| `mslp` | налягане на морското равнище, hPa | PMSL | PRMSL | msl |
| `tg` | температура на почвата, °C | T_G | — | — |
| `hsurf` | височина на клетката, m (статично) | HSURF | HGT | z/g |

Наличието на всеки параметър в open data се проверява в MultiConvect. Каквото го няма, просто липсва в съответния файл.

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

# Коригираната карта: `fog_map.npz` и `fog_map_meta.json`

> Чернова (етап 2). Мрежата, методът и обновяването се уточняват в етап 2.

Мрежата е тази на ICON-EU от MultiConvect, освен ако не решим друго. Часове 0–12.

```
fog_map.npz
  lat, lon, valid_time       като във входа
  p_fog      (nt, ny, nx)    uint8, %, 255 = няма данни
  t2m        (nt, ny, nx)    int16, scale 0.01 (коригирана)
  td2m       (nt, ny, nx)    int16, scale 0.01 (коригирана)
  wind_ms    (nt, ny, nx)    int16, scale 0.01 (коригиран)
  corr_weight(nt, ny, nx)    uint8, %: колко от корекцията е останала (0 = само модел)
```

`fog_map_meta.json`:

```json
{
  "generated_at": "2026-11-25T06:20:00Z",
  "source": "fogNowcast core",
  "run": {"icon_eu": "2026-11-25T00:00:00Z"},
  "t0": "2026-11-25T06:00:00Z",
  "inputs": {"ntu": 81, "metar": 5, "synop": 12, "sat": true},
  "vars": {"p_fog": {"units": "%", "fill": 255}}
}
```

`corr_weight` показва на потребителите колко е останало от корекцията: близо до t0 и в радиус от станциите е високо, далеч е 0.

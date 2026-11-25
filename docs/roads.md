# Участъци от пътищата: `static/roads_segments.geojson` и `roads_latest.json`

> Чернова (етап 3).

## Геометрия: `static/roads_segments.geojson`

- Източник: OpenStreetMap (извадка Geofabrik за България), `highway = motorway | trunk | primary` с `ref`. © OpenStreetMap contributors, ODbL.
- Нарязва се веднъж на участъци от 5–10 км по поредица от пътя. Където е възможно, преходът между котловина и било е граница на участък.
- Файлът е версиониран и се сменя само с PR.

Свойства на всеки участък (`LineString`):

| Поле | Описание |
|---|---|
| `id` | `SEG:A1-012` |
| `ref` | `A1`, `I-1`… |
| `name` | напр. „АМ Тракия, Чирпан – Стара Загора“ |
| `km_from`, `km_to` | по нашето референциране (не километражът на АПИ) |
| `elev_min_m`, `elev_mean_m` | от DEM |
| `cells` | кои клетки от мрежата на картата покрива и какъв дял |

## Продукт: `roads_latest.json`

```json
{
  "generated_at": "2026-11-25T06:20:00Z",
  "source": "fogNowcast products",
  "run": {"icon_eu": "2026-11-25T00:00:00Z"},
  "t0": "2026-11-25T06:00:00Z",
  "segments_version": 1,
  "segments": [
    {"id": "SEG:A1-012",
     "valid_time": ["2026-11-25T06:00:00Z", "..."],
     "p_fog_max": [80, 75, "..."],
     "fog_fraction": [40, 35, "..."],
     "onset": null}
  ]
}
```

- `p_fog_max`: най-лошата клетка на участъка, %.
- `fog_fraction`: делът от дължината на участъка с P над прага, %. Прагът не е решен.
- `onset`: първият час, в който участъкът минава прага, ако сега е под него.

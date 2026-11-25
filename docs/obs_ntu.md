# Станции на НТУ: `obs/ntu_latest.json`

> Чернова (етап 1).

## Източник

- Реално време: `https://bgtoll.bg/index.php/mto/data`. Тегли се най-много веднъж на 15 мин, само от GitHub Actions. Файлът започва с BOM и се чете с `utf-8-sig`.
- Контрол на температурата: `https://bgtoll.bg/index.php/mto/temperaturefallback`.
- Архив: дневни zip файлове `meteoData_YYYY-MM-DD.zip` в [bgtoll.bg/otvoreni-danni](https://bgtoll.bg/otvoreni-danni). **Имената на файловете вътре са в местно време, а полето `time` е в UTC.**

## Структура

```json
{
  "generated_at": "2026-11-25T06:05:12Z",
  "source": "bgtoll.bg/mto/data",
  "obs_time_latest": "2026-11-25T06:00:00Z",
  "stations": [
    {
      "id": "NTU:57",
      "name": "Гъмзово",
      "lat": 44.05, "lon": 22.80, "elev_m": null,
      "time": "2026-11-25T06:00:00Z",
      "t": 1.2, "td": 1.0, "rh": 99, "t_surface": 0.4,
      "wind_ms": 0.8, "wind_dir": 250, "pressure_hpa": 1012.3,
      "qc": {"excluded": false, "flags": []}
    }
  ]
}
```
(Координатите в примера са илюстративни.)

| Поле | От полето на източника | Бележка |
|---|---|---|
| `id` | `station_id` | `NTU:` + `station_id` |
| `name` | `name` | |
| `lat`, `lon` | `lat`, `lon` | |
| `elev_m` | — | от DEM, в източника няма |
| `time` | `time` | UTC, преобразувано в ISO 8601 |
| `t` | `air` | °C |
| `td` | — | смята се от `t` и `rh` (Magnus, коефициенти на Alduchov–Eskridge) |
| `rh` | `humidity` | % |
| `t_surface` | `surface` | °C, настилка |
| `wind_ms` | `wind` | Източникът вероятно е в km/h, **да се провери** преди превръщането в m/s. |
| `wind_dir` | `wind_dir` | ° |
| `pressure_hpa` | `pressure` | на нивото на станцията |

Полето `scp` от източника не се пренася, докато не е ясно какво значи.

## QC флагове

| Флаг | Условие | Изключва станцията? |
|---|---|---|
| `surface_missing` | `surface = 0` | Не. Става `null` само `t_surface`. |
| `rh_missing` | `humidity = 0` | Не. `rh` и `td` стават `null`. |
| `rh_suspect` | известен повреден датчик (напр. Чурек) или скокове 0↔100 | Да, за RH/Td. |
| `late` | `time` е по-старо от 30 мин спрямо `generated_at` | Не, но потребителят преценява. |
| `model_fallback` | `fallback_active` или `use_model` в `/temperaturefallback` | **Да**: T е от Open-Meteo, а не мерена. |

`qc.excluded = true`, когато станцията не бива да влиза в корекцията на модела. Списъкът с повредени датчици се пази в кода на `qc/`, с дата от кога важи.

Окончателните прагове и правила се решават в PR-а за QC.

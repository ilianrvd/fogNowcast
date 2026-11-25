# Станции на НТУ: `obs/ntu_latest.json`

> Чернова (етап 1).
>
> Проверено на 2026-11-25 с една заявка от GitHub Actions: HTTP 200, 96 записа, JSON с BOM (чете се с `utf-8-sig`), `time` е в UTC (в 09:33Z последният запис е „09:30:00"). Честотата на обновяване не е измерена от една заявка; архивът е на 15 мин. Отговорът е зад Cloudflare с `Cache-Control: no-store`. Координатите са с 6 знака след запетаята. Ключове: `station_id`, `scp`, `name`, `nameENG`, `lat`, `lon`, `air`, `surface`, `humidity`, `pressure`, `wind`, `wind_dir`, `time`. Заявките са с идентифициращ User-Agent.

## Източник

- Реално време: `https://bgtoll.bg/index.php/mto/data`. Тегли се най-много веднъж на 15 мин, само от GitHub Actions. Файлът започва с BOM и се чете с `utf-8-sig`.
- Контрол на температурата: `https://bgtoll.bg/index.php/mto/temperaturefallback`.
- Архив: [bgtoll.bg/otvoreni-danni](https://bgtoll.bg/otvoreni-danni) (папка в Dropbox). Започва от 2026-01-27, JSON, измервания на 15 мин в UTC. Лиценз **CC BY 4.0**: при публикуване на данни или производни се посочва източникът (Национално тол управление). Дневните zip файлове са `meteoData_YYYY-MM-DD.zip`. **Имената на файловете вътре са в местно време, а полето `time` е в UTC.**

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
      "wind_kt": 1.6, "wind_dir": 250, "pressure_hpa": 1012.3,
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
| `wind_kt` | `wind` | Източникът е в km/h (потвърдено от сайта на АПИ). kt = km/h ÷ 1.852. |
| `wind_dir` | `wind_dir` | ° |
| `pressure_hpa` | `pressure` | налягане на станцията, не е редуцирано до морското равнище |

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

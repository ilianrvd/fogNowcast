# `fog_points.json`: коригирани условия по точки (некалибрирани)

> Чернова (етап 2), съгласувана с Илиан Манафов на 2026-11-26. Изследователски продукт, не за оперативна употреба. Личен проект, не е официален продукт на сертифициран доставчик. Контакт: Илиан Манафов, ilianm@mail.bg.

## Какво е

Модел (ICON-EU по точки от MultiConvect, [archive_points.md](archive_points.md)) плюс затихваща корекция от последното наблюдение (станции на НТУ, METAR за летищата). Методът е проверен в [verification.md](verification.md): за `t`, `td` и вятъра е по-добър от суровия модел при всички срокове 1–6 ч.

**Некалибриран.** Файлът не съдържа вероятност за мъгла (`p_fog`). Дава се дефицитът на насищане `deficit`, а прагове и вероятности идват след калибрирането ([verification.md](verification.md)). Калибрирането е само по летищата с METAR; приложимостта му към станциите на НТУ е допускане, което ще се проверява.

## Метод

`коригирано(valid) = модел(valid) + (наблюдение − модел)(t_obs) · exp(−(valid − t_obs) / τ)`

- `τ`: 9 ч за `t` и `td`, 6 ч за вятъра (обучение октомври 2026).
- Моделът в `t_obs` е линейна интерполация между двата часа около наблюдението.
- Наблюдение по-старо от 90 мин не се ползва. Точката е само по модела: `obs_source: null`, `residual: null`, `corr_weight: 0`.
- Станции на НТУ с `qc.excluded = true` не се ползват. Летище: най-новото METAR съобщение (`t`, `td`, `wind_kt`).
- `td` се ограничава до `t`. `deficit`: `t − td` при `t ≥ 0`, `t` минус точката на скреж при `t < 0` ([obs_ntu.md](obs_ntu.md)).
- Посоката на вятъра и ниската облачност са на модела, некоригирани. Без корекция по височина.

## Файл

Заглавие: `schema` (`fog_points/1.0`), `generated_at`, `source`, `run` (`{"icon_eu": ISO}`), `t0` (текущият час, UTC), `calibrated` (`false`), `units`, `inputs` (`model_age_min`, `ntu_age_min`, `metar_age_min`), `tau_h`.

```json
{"id": "NTU:1", "name": "Мотел Ихтиман", "lat": 42.43, "lon": 23.85, "elev_m": 700, "model_elev_m": 650.0,
 "obs_source": "ntu", "obs_time": "2026-11-26T05:15:00Z", "residual": {"t": -2.0, "td": -1.5, "wind_kt": -3.0},
 "series": [{"valid": "2026-11-26T05:00:00Z", "lead_h": 0, "t": 0.1, "td": -0.9, "deficit": 1.0,
             "wind_kt": 3, "wind_dir": 240, "cloud_low_pct": 20, "corr_weight": 98}]}
```

- `series`: `lead_h` 0–6 от `t0`, по часове. Час, за който моделът няма стойност, се пропуска.
- `corr_weight`: средно претеглено остатъчно тегло на `t`, `td` и вятъра в %. 0 = само модел.
- Липсваща стойност е `null`. Температури и `deficit` в °C, вятър в kt.

## Изпълнение

`python -m core.fog_points --model latest_points.json --ntu ntu_latest.json [--metar metar_latest.json] --points static/points.json --out fog_points.json`

Публикуването в Pages и картата са отделни стъпки (следващи PR-и). Засега `latest_points.json` е непотвърден от MultiConvect: форматът е по [archive_points.md](archive_points.md).

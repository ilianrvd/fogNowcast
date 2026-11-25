# Формати и договорености

## Общи договорености (за всички проекти)

Подготвяме бъдещо обединяване в обща nowcasting система на слоеве: **данни → ядро → продукти → представяне**.

- **Всеки източник се тегли веднъж**, от един проект, и се публикува като файл в Pages с описан формат:
  - модели на мрежа (ICON-EU, GFS, IFS) → MultiConvect;
  - радар, мълнии, METAR, сателит и общият списък на точките → nowcasting-public;
  - станции на НТУ → fogNowcast;
  - SYNOP → още не е решено.

  Решението е от 2026-11-25. Докато другите проекти не публикуват новите файлове, fogNowcast работи със заместител (виж `docs/` на съответния формат).
- Никой проект не тегли източник, който друг вече публикува.
- Всички времена са в **UTC, ISO 8601** с `Z` (`2026-11-25T06:00:00Z`).
- Всеки публикуван файл има полетата:
  - `generated_at`: кога е създаден файлът;
  - `source`: откъде са данните;
  - `run`: моделният рън, когато има такъв (за смесени входове е обект с по един рън на модел).
- Общ списък на точките с единни идентификатори: [`points.md`](points.md). Живее в nowcasting-public.
- GitHub Pages връща `Cache-Control: max-age=600` и това не се променя. Консуматорите не разчитат на свежест под 10 минути.
- Всеки публикуван формат е описан тук, в същия PR, в който се появява.

## Единици

| Величина | Единица | Поле |
|---|---|---|
| Температура, точка на оросяване | °C | `t`, `td`, `t_surface` |
| Относителна влажност | % | `rh` |
| Вятър | **kt** навсякъде, посока в ° (откъдето духа). Превръщане в други единици само при визуализацията. | `wind_kt`, `wind_dir` |
| Налягане | hPa | `pressure_hpa` |
| Видимост | m | `vis_m` |
| Височина | m над морското равнище | `elev_m` |
| Вероятност | 0–100 % | `p_fog` |

Липсваща стойност се записва като `null` в JSON. В бинарните масиви тя е стойността от полето `fill` в метаданните.

## Публикуване

- Pages се публикува от GitHub Actions в клон `gh-pages`. `main` съдържа само код, статични данни и документация.
- Архивът се пази в клонове `archive-YYYY-MM`.

## Формати

| Файл в Pages | Описание | Етап | Състояние |
|---|---|---|---|
| `static/points.json` (публикува nowcasting-public) | [points.md](points.md) | 0 | чернова |
| `static/ntu_stations.json` | [points.md](points.md#станции-на-нту) | 1 | чернова |
| `obs/ntu_latest.json`, `obs/ntu_recent.json`, `obs/ntu_fallback_latest.json` | [obs_ntu.md](obs_ntu.md) | 1 | чернова |
| клон `archive-fallback`: `fallback_ГГГГ-ММ-ДД.csv` | [obs_ntu.md](obs_ntu.md#архив-на-temperaturefallback-клон-archive-fallback) | 1 | чернова |
| `archive-YYYY-MM`: `ntu_YYYY-MM.csv.gz`, `ntu_stations.json`, `ntu_import_meta.json` | [archive_ntu.md](archive_ntu.md) | 1 | чернова |
| `obs/metar_latest.json`, `obs/synop_latest.json` (METAR: nowcasting-public; SYNOP: не е решено) | [obs_metar_synop.md](obs_metar_synop.md) | 1 | чернова |
| `obs/sat_fog_mask.json` (публикува nowcasting-public) | [obs_sat_fog_mask.md](obs_sat_fog_mask.md) | 1–2 | чернова |
| клон `analysis-correction`: `correction_by_station.json`, `correction_report.md` | [correction_model.md](correction_model.md#изход) | 1 | чернова |
| `fog_fields_<model>.npz` (вход от MultiConvect) | [input_multiconvect.md](input_multiconvect.md) | 1 | чернова, за съгласуване |
| `fog_map.npz` и `fog_map_meta.json` | [fog_map.md](fog_map.md) | 2 | чернова |
| `roads_latest.json`, `static/roads_segments.geojson` | [roads.md](roads.md) | 3 | чернова |

„Чернова“ значи, че форматът може да се промени, докато не започне да се публикува.

# METAR и SYNOP: `obs/metar_latest.json`, `obs/synop_latest.json`

> Чернова (етап 1). METAR: Aviation Weather Center (AWC). Публикува го **nowcasting-public** (решение от 2026-11-25), fogNowcast го чете от Pages. Форматът се описва в nowcasting-public; тук е очакваното. SYNOP: Ogimet. Честотата и собственикът на изтеглянето се уточняват с вноса.

Обща структура:

```json
{
  "generated_at": "2026-11-25T06:05:00Z",
  "source": "awc",
  "reports": [
    {
      "id": "LBSF",
      "time": "2026-11-25T06:00:00Z",
      "t": -0.5, "td": -0.8,
      "wind_kt": 2, "wind_dir": null,
      "vis_m": 300, "rvr_m": null,
      "wx": ["FG"],
      "cloud_base_ft": 0, "vv_ft": 100,
      "raw": "METAR LBSF 250600Z ..."
    }
  ]
}
```

- `id`: ICAO за METAR, `SYNOP:<WMO>` за SYNOP.
- `wind_dir: null` при VRB.
- `vis_m`: преобладаващата видимост. `rvr_m`: най-ниската RVR, ако има.
- `wx`: кодовете на явленията (`FG`, `BR`, `FZFG`, `BCFG`, `MIFG`, `PRFG`…).
- `raw`: оригиналното съобщение, винаги се пази.

При SYNOP полето `vis_m` е от групата VV, а `wx` от ww (кодът се пази в `ww`).

## Исторически METAR от IEM и калибриране на флага за мъгла (етап 1)

Това е еднократен архив за калибриране. Живият METAR остава задача на nowcasting-public (виж по-горе).

**Източник:** Iowa Environmental Mesonet, ASOS архив (METAR и SPECI, типове 3 и 4), за LBSF, LBBG, LBWN, LBPD и LBGO, 2026-01-27 … 2026-11-24, на половин час. Проба от 2026-11-25: пълни данни за всички летища, с изключение на LBBG, където METAR има само от 2026-04-28. Съобщенията са AUTO, т.е. явленията са от сензор.

**Определения:**
- видимостта се чете от самото съобщение в метри (IEM дава мили, закръглени, и 1000 m би се получило като 998 m); ако съобщението не може да се разчете, се ползва IEM, закръглена до 100 m (броят се отчита в `vis_from_iem_rounded`);
- `fog`: видимост < 1000 m и код `FG` или `FZFG`. `BCFG`, `MIFG`, `PRFG` и `VCFG` не са мъгла над пистата;
- `low_vis`: видимост < 5000 m.

**Метод:**
1. Слотове по 30 мин (UTC). Към всеки слот отива най-близкото съобщение (в рамките на 15 мин); ако са няколко, печели най-лошата видимост.
2. Към всеки слот най-близкият запис на НТУ (±15 мин) от станциите на летището (`static/correction_stations.json`, групата `airport:<ICAO>`): само записи без флагове; нулев вятър без посока е „неизвестен“.
3. Предиктори: `rh ≥ 90/93/95/97/99.5 %`, `t − td ≤ 0.5/1/2 °C`, `rh ≥ 95 % и t − td ≤ 1`, `rh ≥ 97 % и t − td ≤ 1`; за летища с повече станции и вариант „някоя станция“ (`any`).
4. Таблица на объркванията (TP, FP, FN, TN) и POD, FAR, CSI; за всички слотове, за студените месеци (октомври–март) и за нощта (слънчева височина ≤ 0).
5. Редове с под 10 случая се показват, но не се класират. Праговете не са валидирани на отделен период: случаите са малко и са групирани по епизоди (един епизод е много слотове подред), така че резултатите са ориентировъчни.

**Изход (клон `analysis-metar`, един пренаписван къмит, ръчен workflow `calibrate-metar`):**
- `calibration_metar.json`: `generated_at`, `source`, `period`, `slot_minutes`, `min_events`, `targets` и по летище `stations`, `metar_reports`, `metar_first`, `vis_from_iem_rounded`, `slots`, `fog_slots`, `low_vis_slots` и `rows` (`station`, `predictor`, `target`, `subset`, `n`, `events`, `tp`, `fp`, `fn`, `tn`, `pod`, `far`, `csi`);
- `calibration_report.md`: същото, като най-добри комбинации по CSI;
- `metar_<ICAO>.csv.gz`: нормализираните съобщения (`time` UTC, `vis_m`, `vis_source`, `wx`, `fog`, `low_vis`, `t`, `td` в °C, `wind_kt`, `wind_dir`, `raw`). Температурата и росата в METAR са на цели градуси.

Данни: Iowa Environmental Mesonet; станции: Национално тол управление (CC BY 4.0).

# METAR и SYNOP: `obs/metar_latest.json`, `obs/synop_latest.json`

> Чернова (етап 1). Източникът (OGIMET или друг) и честотата се уточняват с вноса.

Обща структура:

```json
{
  "generated_at": "2026-11-25T06:05:00Z",
  "source": "ogimet",
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

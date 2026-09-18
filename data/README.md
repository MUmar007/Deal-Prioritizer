# Sample dataset

`auto-chicago-il.csv` is a real export from the app: independent auto repair shops within about 10 km of downtown Chicago, ranked by fit. Pulled on 2026-09-18 with:

```
POST /v1/pipeline/runs            {"vertical": "Auto", "market": "Chicago, IL", "limit": 40}
GET  /v1/pipeline/runs/{id}/export?min_score=0&include_rejects=true
```

38 shops in total: 3 prime, 10 solid, 18 watch, plus 7 chains that were rejected because OSM tags them with `brand:wikidata` (Midas, Goodyear, Car-X, Caliber Collision, Crash Champions, Gerber Collision & Glass, Rivian Service Center).

Values come straight from OpenStreetMap, so an empty cell means OSM doesn't have that field. The `notes` column lists what each score is based on; the full scoring table is in the main README.

## License

Data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), available under the [Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/). This extract is shared under the same license.

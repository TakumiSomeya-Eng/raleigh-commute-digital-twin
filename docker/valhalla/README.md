# Valhalla Map-Matching Setup (T4.1)

Local Valhalla 3.5.1 instance for Meili map-matching (FR-9.1).
Tile coverage: North Carolina OSM extract (~350 MB PBF, ~1.2 GB built tiles).

## First-run tile build (one-time, ~5-10 minutes)

```bash
# From project root -- downloads NC PBF and builds tiles automatically
docker compose up valhalla
```

The container will:

1. Download `north-carolina-latest.osm.pbf` from Geofabrik (~350 MB)
2. Build Valhalla routing tiles (CPU-bound, ~5 min on a laptop)
3. Start the HTTP service on port 8002

Tiles are stored in `docker/valhalla/` and cached between restarts.
On subsequent starts the container checks if tiles exist and skips rebuild.

## Verify the service

```bash
curl http://localhost:8002/status
# Expected: {"version":"3.5.x","tileset_last_modified":...}
```

## Manual PBF override

If automatic download is blocked (firewall, offline), download manually:

```bash
curl -L -o docker/valhalla/north-carolina-latest.osm.pbf \
  https://download.geofabrik.de/north-america/us/north-carolina-latest.osm.pbf
```

Then set `use_tiles_ignore_pbf=False` (default) and restart:

```bash
docker compose restart valhalla
```

## Meili tuning (valhalla.json)

`docker/valhalla/valhalla.json` overrides Meili search parameters:

- `search_radius: 50` m -- GPS accuracy for urban driving
- `gps_accuracy: 10` m -- expected position noise
- `interpolation_distance: 10` m -- snap density along road

Adjust these if match quality is poor on specific road segments.

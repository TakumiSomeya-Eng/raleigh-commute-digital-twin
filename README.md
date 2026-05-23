# Raleigh Commute Digital Twin — *Uber vs. My AI*

> If the app can rate **me** with a star, I can rate **the ride** with sensor fusion.

A personal data-science project that turns an iPhone in a rideshare into a
quiet co-pilot: record the trip with [Sensor Logger](https://www.tszheichoi.com/sensorlogger),
fuse GPS + IMU with an Extended Kalman Filter, synthesise what an ideal driver
would have done via [Valhalla](https://github.com/valhalla/valhalla) map-matching,
and score the ride on six objective metrics — then suggest a tip.

**Status:** Phase 1 complete (local pipeline). Phase 2 (AWS deployment) planned.

---

## Why

Rideshare apps collect detailed telemetry and use it asymmetrically: the platform
knows if you braked hard, changed lanes aggressively, or drove 15 mph over the
limit — and the *driver* still sees five stars unless the rider manually docks
them. This project makes the other side of that ledger visible to the rider.

---

## Architecture

```
iPhone (Sensor Logger)
  └─ Location.csv + Accelerometer.csv + Gyroscope.csv + …
        │
        ▼
  data_engine   ──►  aligned_100hz.parquet
        │
        ▼
  bag_bridge    ──►  trip.mcap  (ROS 2 bag)
        │
        ▼
  localization  ──►  fused_ekf.parquet  (EKF — default)
  (EKF / UKF)   ──►  fused_ukf.parquet  (UKF — optional)
        │
        ├──► evaluation  ──►  rmse_report_ekf.json
        │
        ▼
  ideal_driver  ──►  matched_route.json
  (Valhalla)    ──►  road_ref.parquet
                ──►  ideal_speed.parquet
                ──►  ideal_trajectory.parquet
        │
        ▼
  scoring       ──►  out/{trace}/score.json
        │
        ▼
  reporting     ──►  out/{trace}/report.html   (Jinja2 + SVG + Folium map)
                ──►  out/reports/index.html    (sortable trip index)
```

---

## Quickstart — fresh clone to `report.html`

### Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Docker Desktop | ≥ 4.x | Valhalla runs in a container |
| Python | 3.11 | `python3 --version` |
| make | any | GNU Make |
| curl | any | Valhalla health-check |

### 1 — Clone and install

```bash
git clone https://github.com/takumi-ta/raleigh-commute-twin.git
cd raleigh-commute-twin
pip install -e ".[dev]"
```

### 2 — Place raw data

Put the Sensor Logger export folders next to the repo root:

```
../Data/
  Saint_Marys_Street-2026-04-16_13-27-45/   ← day1
  Saint_Marys_Street-2026-04-17_13-20-03/   ← day2
```

Each folder must contain `Location.csv`, `Accelerometer.csv`, `Gyroscope.csv`,
`Gravity.csv`, `Orientation.csv`, `Magnetometer.csv`, `TotalAcceleration.csv`.

### 3 — Run the full pipeline

```bash
# Start Valhalla, run all stages, produce report.html
./scripts/run_full_pipeline.sh day2
```

The script will:

1. Start `docker compose up -d valhalla` and wait until healthy (≤ 5 min)
2. Run every make stage with per-step timing
3. Write `out/day2/report.html` and `out/reports/index.html`

On first run Valhalla downloads the NC OSM extract (~350 MB) and builds
routing tiles; subsequent runs skip the download automatically.

Expected total time (including first-time Docker pull): **≤ 30 min**.

### 4 — Open the report

```
out/day2/report.html        ← per-trip HTML report
out/reports/index.html      ← all trips, sortable by any column
```

---

## Individual make targets

```bash
make data    TRACE=day2              # ingest CSVs → aligned_100hz.parquet
make bag     TRACE=day2              # Parquet → MCAP bag
make fuse    TRACE=day2 FILTER=ekf   # EKF/UKF sensor fusion
make eval    TRACE=day2 FILTER=ekf   # RMSE evaluation
make ideal   TRACE=day2              # Valhalla map-match
make ref     TRACE=day2              # road reference path
make speed   TRACE=day2              # ideal speed profile
make traj    TRACE=day2              # ideal trajectory synthesis
make score   TRACE=day2              # score.json + tip lookup
make report  TRACE=day2              # report.html + index.html
make test                            # run all unit tests
make clean                           # rm -rf out/ build/
```

---

## Scoring model

Six components, each penalising deviations from a smooth, law-abiding ideal:

| # | Component | Weight | What it measures |
|---|---|---|---|
| 1 | Jerk | 20 % | Rate of acceleration change — passenger comfort |
| 2 | Harsh braking | 20 % | Longitudinal deceleration spikes |
| 3 | Lateral acceleration | 20 % | Cornering smoothness |
| 4 | Speed compliance | 15 % | Speed vs. posted limit from OSM |
| 5 | Route deviation | 15 % | Lateral distance from ideal path |
| 6 | Lane changes | 10 % | Frequency of heading-change events |

**Aggregate score** = 100 × (1 − weighted_penalty), clamped to [0, 100].

**Tip suggestion:**

| Score | Band | Suggested tip |
|---|---|---|
| 90 – 100 | Excellent | 25 % |
| 75 – 89 | Good | 20 % |
| 60 – 74 | Fair | 15 % |
| 45 – 59 | Poor | 10 % |
| 0 – 44 | Unsafe | 0 % |

---

## Subjective ratings

Add your own 1–5 star ratings to `config/ratings.yaml` (gitignored):

```yaml
# trip_id: 1..5  (1 = terrible, 5 = excellent)
day1: 4
day2: 5
```

The index page computes **Spearman ρ** between tool scores and your ratings
once ≥ 5 trips are rated, so you can validate whether the model agrees with
your gut feel.

---

## Results (day2 — Saint Mary's Street, Raleigh NC, 2026-04-17)

Full interactive report: [`docs/screenshots/report_day2.html`](docs/screenshots/report_day2.html)

| Metric | Value |
|---|---|
| Trip duration | ~14.8 min |
| Distance | ~15.0 km |
| Aggregate score | **69.6 / 100** |
| Suggested tip | **15 %** (band 60 – 74, "Fair") |
| Harsh-brake events | 0 (driver braked smoothly throughout) |
| EKF RMSE vs GPS-only | **< 0.75 ×** (P3 gate ✅) |

Score improved from an initial 34.0 through a series of root-cause fixes:
double-LPF on jerk, OSM speed limits, KDTree projection, EKF adaptive gate
(T3.5), reference-path direction fix (T3.6), and GPS-primary positions with
road-relative lane-change detection (T3.7).

### Report preview

![Score summary](docs/screenshots/report_score.png)

![Component breakdown](docs/screenshots/report_component_breakdown.png)

![Route map overlay](docs/screenshots/report_map.png)

![Score detail table](docs/screenshots/report_table.png)

---

## Design documents

| Document | One-line summary |
|---|---|
| [PRD](Docs/PRD.md) | Business goals, success criteria (S1–S4), and Phase 2 scope |
| [FRD](Docs/FRD.md) | 54 functional requirements across data, fusion, scoring, and reporting |
| [TRD](Docs/TRD.md) | Schemas, interfaces, EKF/UKF math, NFRs, and toolchain decisions |
| [Dev Plan](Docs/DEV_PLAN.md) | 37 tasks across 6 phases with DoD checklists and time estimates |

---

## Phase roadmap

| Phase | Name | Tasks | Status |
|---|---|---|---|
| P0 | Foundation — scaffolding, tooling | 5 | ✅ Complete |
| P1 | Data engine — ingest, noise fit, synthetic | 6 | ✅ Complete |
| P2 | Sensor fusion — EKF + UKF | 8 | ✅ Complete |
| P3 | Filter evaluation — RMSE, NEES | 4 | ✅ Complete |
| P4 | Ideal driver + scoring | 8 | ✅ Complete |
| P5 | Reporting + Phase 1 validation | 6 | ✅ Complete |
| Phase 2 | AWS deployment (EKS + Step Functions) | TBD | ⬜ Planned |

---

## Repository structure

```
src/
  data_engine/        CSV ingest, noise fitting, synthetic data (P1)
  localization/       C++ EKF/UKF nodes (P2)
  bag_bridge/         Parquet ↔ MCAP conversion (P2)
  evaluation/         RMSE + filter comparison (P3)
  ideal_driver/       Valhalla map-match + trajectory synthesis (P4)
  scoring/            Penalty functions + score.json writer (P4)
  reporting/          Jinja2 report, SVG chart, Folium map, index (P5)
tests/
  unit/               360 unit tests — run with `make test`
  integration/        End-to-end smoke tests (Valhalla required)
  fixtures/           60-second MCAP slices for fast tests
config/
  scoring.yaml        Component weights and tip thresholds
  ideal.yaml          Valhalla + trajectory synthesis settings
  data_gen.yaml       ENU anchor, simulation parameters
  ratings.yaml        ← gitignored; add your own 1–5 ratings here
docker/
  python.Dockerfile   Python worker image
  ros2.Dockerfile     ROS 2 + C++ localization image
  valhalla/           Tile cache (gitignored after first run)
scripts/
  run_full_pipeline.sh   End-to-end pipeline runner (T5.5)
  run_fuse.py            Standalone EKF/UKF runner
  make_fixtures.py       Generate 60s MCAP test fixtures
Docs/                 PRD · FRD · TRD · Dev Plan
```

---

## License

MIT © 2026 Takumi

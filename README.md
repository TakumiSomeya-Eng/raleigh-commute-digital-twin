# Raleigh Commute Digital Twin — *Uber vs. My AI*

> If the app can rate **me** with a star, I can rate **the ride** with sensor fusion.

A personal data-science project that turns an iPhone in a rideshare into a
quiet co-pilot: record the trip with [Sensor Logger](https://www.tszheichoi.com/sensorlogger),
fuse GPS + IMU with an Extended Kalman Filter, synthesise what an ideal driver
would have done via [Valhalla](https://github.com/valhalla/valhalla) map-matching,
and score the ride on six objective metrics — then suggest a tip.

**Status:** Phase 1 complete ✅ · Phase 2 infrastructure complete ✅ · Phase 2 E2E pending Docker image 🚧

---

## Why

Rideshare apps collect detailed telemetry and use it asymmetrically: the platform
knows if you braked hard, changed lanes aggressively, or drove 15 mph over the
limit — and the *driver* still sees five stars unless the rider manually docks
them. This project makes the other side of that ledger visible to the rider.

---

## Architecture — Data Pipeline Overview

> This diagram shows **what data flows through the system end-to-end**, independent of deployment environment.  
> Phase 1 runs this pipeline locally via Docker Compose.  
> Phase 2 deploys the same pipeline on AWS Fargate (ECS), orchestrated by Step Functions.
> EKS is intentionally omitted from the MVP: `py_ekf.py` matches C++ EKF accuracy (VL-1)
> and the EKS control plane exceeds the $50/month cost ceiling (VL-2).

```mermaid
flowchart TD
  classDef phoneNode fill:#F1F5F9,stroke:#64748B,color:#1E293B
  classDef ros2Node  fill:#DBEAFE,stroke:#2563EB,color:#1E3A8A
  classDef pyNode    fill:#DCFCE7,stroke:#16A34A,color:#14532D
  classDef valNode   fill:#FEF3C7,stroke:#D97706,color:#78350F
  classDef outNode   fill:#D1FAE5,stroke:#059669,color:#064E3B
  classDef infraNode fill:#EDE9FE,stroke:#7C3AED,color:#3B0764

  PHONE["Smartphone
  GPS / IMU / baro — recorded as Uber passenger"]:::phoneNode

  subgraph C_ROS2["Phase 1: Container ros2  (ROS2 Jazzy / C++17)
  Phase 2 MVP: replaced by py_ekf.py on Fargate (VL-1)"]
    GPS["/gps/fix — 1Hz
    IN: raw GPS signal
    OUT: lat/lng · outliers possible (up to 122m)"]:::ros2Node
    IMU["/imu/data — 100Hz
    IN: raw IMU signal
    OUT: accel + angular velocity · drift accumulates"]:::ros2Node
    EKF["EKF Node
    IN: /gps/fix + /imu/data
    predict 100Hz via IMU · correct 1Hz via GPS
    chi-squared gate rejects outliers"]:::ros2Node
    ODOM["/fused/odom
    OUT: corrected position · velocity · heading · P_"]:::ros2Node
    GPS & IMU --> EKF --> ODOM
  end

  subgraph C_PY["Container: python  (Python 3.11)"]
    SYN["Synthetic Engine
    IN: corrected trajectory
    extract noise stats · generate 100-day dataset
    OUT: actual trajectory
    position · velocity · heading · brake events"]:::pyNode
    SCO["Scoring Engine
    IN-A: actual trajectory (state vectors + events)
    IN-B: ideal road path (geometry only)
    OUT: ride score per metric
    route deviation · speed · braking · time"]:::pyNode
    SYN -->|"A: actual trajectory — state vectors + brake events"| SCO
  end

  subgraph C_VAL["Container: valhalla  (self-hosted)"]
    VAL["Map Matching
    IN: GPS trace (start to end)
    OUT: ideal road path geometry
    coordinate seq · speed limits · est. time
    no velocity or heading state"]:::valNode
  end

  OUT(["Ride Score → Tip Rate
  available immediately after each ride"]):::outNode
  S3[("S3
  real/synthetic / logs")]:::infraNode
  INFRA["Phase 1: Docker Compose (local)
  Phase 2: ECS Fargate + Step Functions (AWS)
  EKS omitted from MVP (VL-1, VL-2)"]:::infraNode

  PHONE --> GPS & IMU
  ODOM -->|"corrected trajectory"| SYN
  ODOM -->|"GPS trace"| VAL
  VAL -->|"B: ideal road path — geometry · speed limits · est. time (no state vectors)"| SCO
  SCO --> OUT
  OUT -.->|save| S3
  C_ROS2 & C_PY & C_VAL -.->|deploy| INFRA
```

### Scoring Engine — two input types

| Input | Source | Contains |
|---|---|---|
| A — actual trajectory | Synthetic Engine | position · velocity · heading · brake events (state vectors) |
| B — ideal road path | Valhalla | coordinate sequence · speed limits · estimated time (geometry only, no state vectors) |

The Scoring Engine compares A against B per metric: route deviation, speed vs. limit, braking frequency, and time efficiency.

### Container summary

| Container | Runtime | Role |
|---|---|---|
| `ros2` | ROS2 Jazzy / C++17 | EKF sensor fusion — predict (IMU 100Hz) + correct (GPS 1Hz). Phase 1 only; replaced by `py_ekf.py` in Phase 2 MVP |
| `python` | Python 3.11 | Synthetic data generation + ride scoring |
| `valhalla` | gisops/valhalla (self-hosted) | Map matching + AI optimal route (no API cost, offline) |

All three containers share the `rct-net` network and `/workspace`, `/data`, `/out` mounts.

---


## System Boundary

> **The core question of system boundary design is not "what can we build?" but "what are we responsible for?"**
> Drawing the boundary defines the system's purpose, the interfaces it exposes, and where responsibility ends.

### System of Interest (SoI)

> *"A pipeline that automatically scores an Uber ride from raw sensor data and suggests a tip."*

Everything **inside** the boundary is what this system builds, owns, and is responsible for fixing when it breaks.
Everything **outside** is what it consumes or interacts with — but does not control.

---

### Full System Picture

```mermaid
flowchart TB
  classDef actor    fill:#F8FAFC,stroke:#94A3B8,color:#1E293B,stroke-width:1.5px
  classDef soi      fill:#EFF6FF,stroke:#3B82F6,color:#1E3A8A,stroke-width:2px
  classDef stage    fill:#DBEAFE,stroke:#2563EB,color:#1E3A8A,stroke-width:1px
  classDef store    fill:#F0FDF4,stroke:#16A34A,color:#14532D,stroke-width:1px
  classDef infra    fill:#FEF9C3,stroke:#CA8A04,color:#713F12,stroke-width:1px
  classDef out      fill:#F0FDF4,stroke:#059669,color:#064E3B,stroke-width:2px
  classDef excluded fill:#FEF2F2,stroke:#FCA5A5,color:#991B1B,stroke-dasharray:4,stroke-width:1.5px
  classDef ifnode   fill:#FFFFFF,stroke:#6366F1,color:#3730A3,stroke-width:1.5px

  %% ── Actors (outside SoI) ──────────────────────────────────────
  PHONE("📱 Sensor Logger
[External App]
Records GPS + IMU
during Uber ride"):::actor
  PASSENGER("🧑 Passenger
[Human Actor]
Uploads CSVs
Receives scored report"):::actor
  DRIVER("🚗 Uber Driver
[Human — no interface]
Subject of scoring
not notified by SoI"):::actor
  OSM("🗺️ OpenStreetMap
[External Data]
Road network +
speed limits"):::actor
  AWS_PLATFORM("☁️ AWS Platform
[Environment]
ECS · S3 · EventBridge
Step Functions · SNS"):::infra

  %% ── Interfaces (boundary crossings) ──────────────────────────
  IF1("IF-1
7 CSV files
Location · Accel · Gyro
Gravity · Orient · Mag · Total"):::ifnode
  IF2("IF-2
S3 upload
raw/{trip_id}/*.csv
via AWS Console"):::ifnode
  IF3("IF-3
SNS email
score + report link
+ error details"):::ifnode
  IF5("IF-5 / IF-6
GPS trace → Valhalla
matched route ← Valhalla"):::ifnode

  %% ── System of Interest (SoI) ─────────────────────────────────
  subgraph SOI["  System of Interest — Raleigh Commute Digital Twin  "]
    direction TB

    subgraph PIPELINE["  Pipeline (ECS Fargate · Step Functions)  "]
      direction LR
      INGEST("⚙️ ingest
CSV → Parquet
100 Hz aligned"):::stage
      FUSE("🔀 fuse
py_ekf.py
GPS + IMU → EKF"):::stage
      IDEAL("📍 ideal
Valhalla
map-match"):::stage
      SCORE("🏆 score
6 components
→ score.json"):::stage
      REPORT("📄 report
Jinja2 + Folium
→ report.html"):::stage
      INGEST --> FUSE --> IDEAL --> SCORE --> REPORT
    end

    subgraph STORAGE["  S3 Prefix Layout  "]
      direction LR
      S_RAW("raw/
{trip_id}/"):::store
      S_PROC("processed/
{trip_id}/"):::store
      S_FUSED("fused/
{trip_id}/"):::store
      S_IDEAL("ideal/
{trip_id}/"):::store
      S_SCORE("scores/
{trip_id}/"):::store
      S_REPORT("reports/
{trip_id}/"):::store
    end

    subgraph VALHALLA_CONTAINER["  Container: Valhalla (self-hosted)  "]
      VAL("🗺️ Map matching
OSM tiles bundled
No external API calls"):::stage
    end

    INGEST --> S_RAW
    INGEST --> S_PROC
    FUSE --> S_FUSED
    IDEAL --> S_IDEAL
    SCORE --> S_SCORE
    REPORT --> S_REPORT
    IDEAL <--> VAL
  end

  %% ── Excluded (deliberate non-SoI) ────────────────────────────
  EKS("❌ EKS
$72/month
> $50 ceiling"):::excluded
  LAMBDA("❌ Lambda
15-min limit
day2 = 14.8 min"):::excluded
  MOBILEAPP("❌ Mobile App
AWS Console
covers upload"):::excluded

  %% ── Output ───────────────────────────────────────────────────
  TIP("💰 Tip Decision
[Human — outside SoI]
Passenger decides
SoI proposes only"):::out

  %% ── Connections ──────────────────────────────────────────────
  PHONE -->|"records"| IF1
  IF1 -->|"schema-validated"| INGEST
  PASSENGER -->|"uploads"| IF2
  IF2 -->|"EventBridge trigger"| PIPELINE
  OSM -->|"tiles bundled
at build time"| VAL
  PIPELINE -->|"SNS notify"| IF3
  IF3 -->|"email"| PASSENGER
  SCORE -->|"score.json"| TIP
  AWS_PLATFORM -.->|"provides APIs"| SOI
```

---

### Interface Definitions — Responsibility Boundaries

> When something breaks, these definitions answer: **whose responsibility is it?**

| IF | From → To | Format | SoI owns | External owns |
|---|---|---|---|---|
| **IF-1** | Sensor Logger → SoI | 7 CSV files per trip | Schema validation, error detection | CSV format spec, app behaviour |
| **IF-2** | Passenger → SoI | S3 `raw/{trip_id}/` upload via AWS Console | Everything after upload lands | The upload action itself |
| **IF-3** | SoI → Passenger | SNS email (score + report link + error details) | Content accuracy, failure notification | Email delivery (SNS/SES) |
| **IF-4** | SoI → Passenger | `score.json` (aggregate_0_100, tip_pct, config_hash) | Scoring correctness, reproducibility | Whether to act on the tip suggestion |
| **IF-5** | SoI → Valhalla | GPS trace (GeoJSON) | HTTP request format, retry logic | Routing algorithm correctness |
| **IF-6** | Valhalla → SoI | Matched route (coordinates, speed limits, time) | Parsing and interpretation | Map data accuracy (OSM) |
| **IF-7** | AWS → SoI | EventBridge, ECS, S3, SNS APIs | Configuration, deployment, usage | Service availability, regional outages |

---

### What Was Deliberately Placed Outside

Every exclusion is a documented decision — not an omission.

| Excluded | Reason | Alternative used |
|---|---|---|
| **EKS / Kubernetes** | Control plane = $72/month > $50 ceiling (VL-2) | ECS Fargate — zero idle cost |
| **C++ EKF node (cloud)** | EKS required; py_ekf.py matches accuracy (VL-1) | `scripts/py_ekf.py` on Fargate |
| **AWS Lambda** | day2 = 14.8 min; Lambda hard-caps at 15 min | Fargate — no time limit |
| **Mobile app** | AWS Console covers the upload; +40h saved (VL-4) | Mobile browser + AWS Console |
| **Tip payment** | Passenger decides; SoI proposes only | Score + tip % in email |
| **UKF (cloud)** | EKF accuracy sufficient (VL-1); saves task definition + cost | EKF only |
| **Driver notification** | Rider-side transparency is the goal; driver-side is out of scope | — |
| **Multi-region / DR** | Explicit non-goal (PRD §1.4) | — |

---

### Engineering Checklist for Boundary Decisions

Four questions applied to every boundary decision in this project:

**1. "If it breaks, who fixes it?"**
If the answer is unclear, the boundary is ambiguous.
*Example: Valhalla map data accuracy → OSM's responsibility. SoI detects anomalies via score regression tests.*

**2. "If something outside changes, what does SoI change?"**
This defines the interface (IF).
*Example: If Sensor Logger changes its CSV column names → SoI updates schema validation in `src/data_engine/schemas.py`. Nothing else changes.*

**3. "Why is this inside SoI? What happens if it's outside?"**
*Valhalla inside: zero API cost, offline, precision control.*
*Mobile app outside: AWS Console covers it with zero implementation cost.*

**4. "Can this boundary change? If so, what is affected?"**
Design stable internal interfaces to limit blast radius.
*Example: EKS → Fargate mid-project. Only the execution layer changed.*
*S3 prefix layout and `StorageAdapter.from_env()` were unchanged — they were the stable internal interface.*

```
Boundary change:  EKS (C++ EKF)  →  Fargate (py_ekf.py)
                        │                    │
                        ▼                    ▼
              S3 fused/{trip_id}/fused_ekf.parquet   ← stable
                        │                    │
                        ▼                    ▼
              StorageAdapter.from_env()              ← stable
```

The S3 prefix design and `StorageAdapter` absorbed the change.
Nothing downstream (scoring, reporting, SNS) required modification.

---

## Development Process

This project follows the **Hypothesis Hierarchy Model** — all implementation
decisions trace back to a validated Value hypothesis. See `.claude/prompts/` for
the full five-layer framework and Phase 2 hypothesis records.

```
Value → Behavior → Domain → Interaction → Implementation
```

Validated learnings are tracked in `docs/LIVING_SPEC.md`.

---

## Phase 2 Infrastructure (AWS) — Completed 2026-05-30

All resources deployed to `us-east-1` via Terraform (`infra/terraform/`).

| Module | Resource | Purpose |
|---|---|---|
| `s3` | `rct-data-takumi2026` | Pipeline data store (raw → processed → scores → reports) |
| `ecr` | `rct/python-worker` | Docker image registry |
| `iam` | `rct-gha-dev` | GitHub Actions OIDC (no long-lived keys) |
| `iam` | `rct-fargate-task-dev` | ECS task S3 read/write |
| `iam` | `rct-fargate-execution-dev` | ECS agent ECR pull + CloudWatch logs |
| `iam` | `rct-stepfn-dev` | Step Functions ECS + SNS |
| `ecs` | `rct-dev` cluster + 5 task defs | Fargate pipeline workers |
| `stepfn` | `rct-pipeline-dev` | Orchestration state machine |
| `stepfn` | `rct-notify-dev` SNS | Pipeline completion/failure email |
| `eventbridge` | `rct-s3-raw-upload-dev` | S3 upload → Step Functions trigger |
| `observability` | `rct-monthly-dev` budget | $50/month cost ceiling (BR-4) |

**Cost at idle:** ~$0/month (no running containers, no EKS control plane).

---

## Phase 2 — Next Steps (T7.x)

Before the E2E smoke test can pass, the Phase 1 Python code needs three changes:

1. **S3 adapter** ✅ — `src/storage.py` added; all pipeline modules updated (T7.1–T7.3)
2. **Docker build** — build `docker/python.Dockerfile` and push to ECR
3. **E2E validation** — upload day2 CSVs to S3, assert `score.json` within ±2 of baseline 34.8

---

## Quickstart — Local Pipeline (Phase 1)

### Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Docker Desktop | ≥ 4.x | Valhalla runs in a container |
| Python | 3.11 | `python3 --version` |
| make | any | GNU Make |
| curl | any | Valhalla health-check |

### 1 — Clone and install

```bash
git clone https://github.com/TakumiSomeya-Eng/raleigh-commute-digital-twin.git
cd raleigh-commute-digital-twin
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
./scripts/run_full_pipeline.sh day2
```

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
| [Living Spec](docs/LIVING_SPEC.md) | Phase 2 hypothesis records and validated learnings (VL-1 – VL-8) |

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
| **Phase 2 — Infra** | AWS infrastructure (T6.1 – T6.8) | 8 | ✅ Complete |
| **Phase 2 — Code** | S3 adapter ✅ + Docker build + E2E (T7.x) | TBD | 🚧 In progress |

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
src/
  storage.py          S3/local transparent storage adapter (T7.1)
scripts/
  run_full_pipeline.sh   End-to-end pipeline runner
  py_ekf.py              Python EKF fallback (Phase 2 MVP, VL-1)
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
  python.Dockerfile   Python worker image (Phase 2 ECR target)
  ros2.Dockerfile     ROS 2 + C++ localization image
infra/
  terraform/          Phase 2 AWS infrastructure (Terraform)
    modules/          s3 · ecr · iam · ecs · stepfn · eventbridge · observability
    envs/dev/         Dev environment — apply with `terraform apply`
.claude/
  prompts/            Hypothesis-Driven Development prompts (0–4)
  skills/             Agent knowledge capsules (aws-infra, sensor-fusion, …)
docs/
  LIVING_SPEC.md      Phase 2 hypothesis log and validated learnings
  PRD / FRD / TRD / DEV_PLAN
```

---

## License

MIT © 2026 Takumi

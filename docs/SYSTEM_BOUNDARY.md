# System Boundary — Raleigh Commute Digital Twin

> **The core question of system boundary design is not "what can we build?" but "what are we responsible for?"**
> Drawing the boundary defines the system's purpose, the interfaces it exposes, and where responsibility ends.

## System of Interest (SoI)

> *"A pipeline that automatically scores an Uber ride from raw sensor data and suggests a tip."*

Everything **inside** the boundary is what this system builds, owns, and is responsible for fixing when it breaks.
Everything **outside** is what it consumes or interacts with — but does not control.

---

## Full System Picture

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

## Interface Definitions — Responsibility Boundaries

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

## What Was Deliberately Placed Outside

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

## Engineering Checklist for Boundary Decisions

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

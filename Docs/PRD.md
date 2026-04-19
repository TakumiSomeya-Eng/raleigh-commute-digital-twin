# PRD — Raleigh Commute Digital Twin: *Uber vs. My AI*

**Document version:** 2.0 (formal, narrative reframe)
**Status:** Approved for Step 1 implementation
**Owner:** Takumi
**Last updated:** 2026-04-19

---

## 0. One-line pitch

A personal digital-twin tool that turns every Uber / Lyft ride into a **scored trip**: replay the ride's sensor trace through a ROS 2 localization stack (EKF/UKF fusing mobile IMU + GPS), compare the driver's actual trajectory against an **AI "ideal driver"** baseline over the same route, and emit a comfort/quality score that informs how much the rider tips.

---

## 1. Project Vision

### 1.1 The problem, in one paragraph

Rideshare rides vary enormously in quality — one driver takes Wade Ave at a smooth 45 mph holding the lane centerline, the next brake-checks through every yellow light and drifts across lanes on I-440. The tip flow is 15 % / 18 % / 20 %, decided in three seconds by vibes. There is no objective signal connecting **how the ride actually felt** to **how much the driver gets paid**. I wanted one, for myself, so I built it.

### 1.2 Concept

The phone in my pocket already records everything needed to reconstruct the ride: GPS, accelerometer, gyroscope, magnetometer, orientation — all at 10–100 Hz via Sensor Logger. After the ride:

1. **Reconstruct** the actual trajectory with EKF/UKF sensor fusion (GPS is too noisy and too slow, 1 Hz, to tell the story alone).
2. **Simulate** what an "ideal AI driver" would have done over the same origin–destination pair — smooth acceleration profiles, lane-centered path, law-abiding speed, gentle jerk.
3. **Score the delta.** Where did the Uber driver deviate from the ideal, and by how much? Harsh braking, aggressive lane changes, speeding, cornering jerk — each becomes a penalty in a weighted cost function.
4. **Decide the tip.** The score maps to a suggested tip rate. The mapping is a lookup table I control; the tool does not auto-tip. Final call is always mine.

The AI is the yardstick. The Uber is the thing being measured. The rider is the one holding the ruler.

### 1.3 Why this is interesting beyond "an app for me"

- **Reality Gap, inverted.** Most AV simulators ask "can synthetic match reality?" This one asks "how far is reality from the synthetic ideal?" Same machinery, opposite direction of comparison.
- **It actually runs on phone data.** No specialized hardware, no vehicle instrumentation. If this works on Sensor Logger CSVs from a cheap Android, it works on any ride.
- **The score is a contract.** Each component of the final number — jerk, deviation, speed compliance — is transparent and tunable. A rider can disagree with the weights and re-score the ride; the tool does not hide what it penalized.

### 1.4 Non-goals (explicit)

- Not a real-time onboard system. Scoring happens post-trip, at leisure.
- Not a driver-rating service. The score is private, for my own tipping decision. No driver names, no uploads, no sharing.
- Not a perception project. No camera / LiDAR / object detection. Localization + trajectory scoring only.
- Not an auto-tipper. The last step — deciding the actual tip amount — is always manual.
- Not a novel-algorithm paper. EKF, UKF, cost-function scoring are textbook; the contribution is the end-to-end tool and its honesty about its own measurement noise.

---

## 2. Users and success criteria

### 2.1 Primary user

Me, after a rideshare, with the recorded trace on my phone and five minutes to decide on a tip.

### 2.2 Success criteria

| # | Criterion | Measurement | Target |
|---|-----------|-------------|--------|
| S1 | Fused trajectory is meaningfully better than raw GPS | Horizontal RMSE vs. map-matched reference | EKF/UKF improves on raw GPS by ≥ 25 % |
| S2 | Synthetic noise model stays inside real support | KS-test p-value, per-sensor, real vs. synthetic | p > 0.05 on ≥ 80 % of channels |
| S3 | Tool is reproducible from zero | Fresh clone → `make bootstrap && make score TRACE=day2` | Completes and emits a single `score.json` |
| S4 | Scores correlate with subjective ride experience | Score a ride immediately after it, independently write down how it felt (1–5), compare over ≥ 8 rides | Spearman ρ ≥ 0.6 between tool score and gut score |
| S5 | Every artifact is CLI-produced | `git grep -l 'console.aws.amazon.com'` empty; all infra in Terraform | Hard gate |

S4 is the one that matters for the stated purpose. If the tool's score doesn't track how the ride felt, the tool has failed at its job regardless of how clean the filter math is.

---

## 3. Data — what we actually have

Two baseline recordings, Sensor Logger v1.56.0 on a TMAF025G Android device. These are **my own driving** along Saint Mary's Street and its eastward extensions — used to calibrate noise models and define what "a normal Raleigh drive" looks like before any Uber scoring happens.

| Session | Date | Duration | GPS fixes | Mean horiz. acc. | Max horiz. acc. | Speed max | Notes |
|---------|------|----------|-----------|------------------|------------------|-----------|-------|
| day1 | 2026-04-16 13:27 ET | 15.2 min | 947 @ ~1.04 Hz | 3.01 m | **122.05 m** | 28.91 m/s | One large GPS outlier, likely overpass/tunnel |
| day2 | 2026-04-17 13:20 ET | 14.8 min | 922 @ ~1.04 Hz | 2.44 m | 16.37 m | 29.72 m/s | Clean; primary calibration trace |

Uber / Lyft traces are collected the same way (Sensor Logger running during the ride, exported after). First ones will be captured during implementation of Step 5.

### 3.1 Sensor channels used

| Channel | Rate | Role in stack | File |
|---------|------|---------------|------|
| Location | 1 Hz | EKF measurement update (lat/lon/speed/bearing + `horizontalAccuracy` → R matrix) | `Location.csv` |
| Accelerometer (calibrated) | ~100 Hz | EKF prediction + direct jerk computation for scoring | `Accelerometer.csv` |
| Gyroscope (calibrated) | ~100 Hz | EKF prediction (yaw rate), cornering smoothness | `Gyroscope.csv` |
| Gravity | ~100 Hz | Decompose total accel into body-frame longitudinal + lateral | `Gravity.csv` |
| Orientation (quat + RPY) | ~100 Hz | Initial heading seed, sanity check | `Orientation.csv` |
| Magnetometer | ~50 Hz | Heading prior when GPS speed < 1 m/s (bearing unreliable at rest) | `Magnetometer.csv` |
| TotalAcceleration | ~100 Hz | Cross-check for accel calibration | `TotalAcceleration.csv` |
| Uncalibrated variants | — | Reserved for bias-estimation experiments | `*Uncalibrated.csv` |
| Compass, Battery, Network, Microphone, Annotation | — | Not used | — |

### 3.2 Data realities the stack must handle

- **Clock**: `time` is epoch-ns from Android's `SystemClock`; fusion runs in `seconds_elapsed` (relative to recording start) to avoid timezone / leap-second pitfalls. GPS and IMU are not monotonic-aligned — require interpolation to a common 100 Hz grid.
- **GPS outliers**: day1 has a 122 m-accuracy fix. The EKF must weight measurements by reported accuracy and gate outliers with a chi-squared test at 99 %. This is a required feature, not a nice-to-have.
- **Body-frame, not ENU**: the phone sits in an unknown pose. Gravity gives pitch/roll relative to the car; yaw is aligned once per trip against GPS course-over-ground during a straight segment above 5 m/s.
- **Local frame**: all fusion runs in a local ENU frame anchored near the corridor centroid (≈ 35.773, −78.610), flat-earth projection (valid over the < 10 km span used).
- **Phone-in-pocket vs. phone-in-cup-holder matters.** Scoring must be robust to phone placement, because I can't guarantee how the Uber driver's car accommodates it. The pitch/roll calibration step handles this; the **absolute** value of lateral accel is trusted only after calibration.

---

## 4. System architecture

### 4.1 Logical pipeline

```
Raw Sensor Logger CSVs (S3: /raw/{trip_id}/)
          │
          ▼
 ┌──────────────────────┐      ┌────────────────────────┐
 │  data_engine (Py)    │ ───▶ │  /processed/{trip_id}/ │
 │  • clock sync        │      │  aligned_100hz.parquet │
 │  • ENU projection    │      └───────────┬────────────┘
 │  • noise model fit   │                  │
 │  • synth generator   │ ───▶ /synthetic/{scenario_id}/
 └──────────────────────┘
          │
          ▼
 ┌────────────────────────────────┐
 │  localization (ROS 2 / C++)    │
 │  ekf_node / ukf_node           │
 │  CTRV model, chi-squared gate  │ ───▶ /fused/{trip_id}/odom
 └────────────────────────────────┘
          │
          ▼
 ┌────────────────────────────────┐
 │  ideal_driver (Py)             │
 │  • take route O→D from trip    │
 │  • generate smooth trajectory  │ ───▶ /ideal/{trip_id}/trajectory
 │  • speed limits + gentle jerk  │
 └────────────────────────────────┘
          │
          ▼
 ┌────────────────────────────────┐
 │  scoring (Py)                  │
 │  • per-component penalties     │
 │  • weighted aggregate score    │ ───▶ /scores/{trip_id}/score.json
 │  • tip_rate lookup             │
 └────────────────────────────────┘
          │
          ▼
   Step Functions → static HTML report on S3 (one page per trip)
```

### 4.2 Stack (CLI-native, all tools below are non-interactive)

| Layer | Choice | Why |
|-------|--------|-----|
| Compute | Amazon EKS (Fargate for Python, EC2 for ROS 2 nodes) | ROS 2 DDS discovery needs pod-to-pod UDP; Fargate fine for batch Python |
| Storage | S3, prefix-partitioned (`/raw/`, `/processed/`, `/synthetic/`, `/fused/`, `/ideal/`, `/scores/`) | Cheap, versioned, lifecycle-ruleable |
| Orchestration | AWS Step Functions, defined in Terraform | Legible state-machine view, retry/catch built-in |
| IaC | Terraform ≥ 1.7, AWS provider ≥ 5.x | Declarative, drift-detectable |
| Container | Docker → ECR | Standard |
| CI/CD | GitHub Actions | Free for public repos, OIDC to ECR (no long-lived keys) |
| Local dev | `docker compose` mirror of the EKS topology | Fast laptop iteration |
| Languages | Python 3.11 (data, ideal, scoring), C++17 (ROS 2 nodes), HCL, YAML | — |
| ROS distro | ROS 2 Jazzy (LTS, Ubuntu 24.04) | Current LTS as of 2026 |

### 4.3 Repository layout

```
.
├── Makefile                  # make bootstrap | data | fuse | ideal | score | deploy | clean
├── README.md
├── PRD.md                    # this document
├── config/
│   ├── data_gen.yaml         # real:synth ratio, noise distributions, n_scenarios
│   ├── ekf.yaml              # process/measurement covariances
│   ├── ideal.yaml            # ideal-driver policy: max jerk, speed-limit posture
│   └── scoring.yaml          # cost weights + tip_rate lookup table
├── src/
│   ├── data_engine/          # Python: ingest, align, fit noise, generate synth
│   ├── localization/         # C++17: ekf_node, ukf_node, shared ctrv_model lib
│   ├── ideal_driver/         # Python: ideal trajectory synthesis over given O→D
│   └── scoring/              # Python: penalties, aggregate score, tip lookup
├── infra/                    # Terraform: s3, eks, stepfn, ecr, iam
├── .github/workflows/        # ci.yaml, deploy.yaml
├── scripts/                  # bootstrap.sh, record.sh, run_local.sh
└── tests/
    ├── unit/                 # pytest + gtest
    └── integration/          # headless ROS 2 bag replay on day2
```

### 4.4 Data contracts

All inter-stage handoff via **Parquet** (tabular) or **ROS 2 bag** (replay into filter). No CSV past ingestion. Every schema declared once in `src/data_engine/schemas.py` (pydantic), mirrored to C++ via a generated header.

---

## 5. Algorithmic components

### 5.1 Motion model — CTRV (Constant Turn Rate and Velocity)

State: `x = [px, py, v, ψ, ψ̇]ᵀ` in local ENU. CTRV over CV because Raleigh commutes have real turns (Saint Mary's / Wade Ave). CTRV over CTRA because IMU longitudinal accel is noisy enough that estimating it as a state degrades v; accel is fed as a control input instead.

### 5.2 EKF and UKF

Both implemented, both evaluated. Hypothesis: UKF modestly better through sharp turns (Wade Ave off-ramp) where the CTRV Jacobian linearization weakens; flat segments a tie. If tie throughout, that is reported as-is.

### 5.3 Measurement model

- GPS position: `z = [px, py]`, `R = diag(σ_h², σ_h²)` with `σ_h = horizontalAccuracy`
- GPS velocity: `z = v`, `R = speedAccuracy²`
- GPS bearing: ψ measurement **only when speed > 2 m/s** (bearing is noise at rest)
- Chi-squared innovation gate at 99 % for the day1-style 122 m outlier

### 5.4 Ideal driver baseline

`src/ideal_driver/synthesize.py` takes the trip's actual O→D and the actual route driven (map-matched from the fused trajectory) and generates a reference trajectory that would result if a conservative, smooth driver handled the same route. Parameters in `config/ideal.yaml`:

- Max longitudinal acceleration: 1.5 m/s² (gentle)
- Max longitudinal deceleration: 2.5 m/s² (firm but not harsh)
- Max lateral acceleration in turns: 2.0 m/s²
- Max jerk: 2.0 m/s³
- Speed: min(posted limit, comfort speed for curvature)
- Lane-centered path (map-matched road centerline)

The ideal driver is intentionally **not** an optimal or fastest driver. It is the driver you wish your Uber was: legal, smooth, unhurried, predictable.

### 5.5 Scoring

`src/scoring/score.py` computes penalties component-by-component against the ideal baseline:

| Component | Definition | Default weight |
|-----------|------------|----------------|
| Jerk | `∫ |j| dt` excess over ideal | 0.30 |
| Harsh braking | count of events where decel > 3.5 m/s² | 0.20 |
| Lateral accel | `∫ max(0, |a_lat| − ideal_lat)² dt` | 0.15 |
| Speed compliance | time-weighted `max(0, v − speed_limit)` | 0.20 |
| Route deviation | lateral distance from map-matched centerline, ∫ | 0.10 |
| Lane changes | count of abrupt yaw excursions > threshold | 0.05 |

Aggregate score ∈ [0, 100]. The `config/scoring.yaml` lookup table maps score bands to suggested tip rates (e.g., 90+ → 25 %, 75–89 → 20 %, 60–74 → 15 %, < 60 → 10 % and rethink). All weights and thresholds are configurable; all component scores are reported individually, not just the aggregate.

### 5.6 Synthetic data (reality-gap guardrail)

`data_engine/synth.py` fits empirical noise distributions from my own calibration traces and generates n ≥ 10 perturbed scenarios per config for regression testing the fusion stack (not the scoring itself). KS-test synthetic vs. real per channel; pipeline fails if p < 0.05 on > 20 % of channels. Prevents the filter from quietly over-fitting to day2's particularly clean GPS.

---

## 6. Evaluation of the tool itself

### 6.1 Filter evaluation (S1)

| Metric | Definition | Direction |
|--------|------------|-----------|
| Horizontal RMSE | √mean((p_fused − p_gt)²) over trip | lower better |
| GPS-only RMSE | Same, raw GPS as p_fused | reference |
| Innovation NEES | Normalized estimation error squared | ≈ dim(z) |
| Rejection rate | Fraction of measurements gated out | low, non-zero |

### 6.2 Score validity (S4)

For ≥ 8 Uber/Lyft rides:

1. Record the ride.
2. Immediately after exiting, write a 1–5 subjective comfort rating in a notes file, before running the tool.
3. Run `make score`.
4. Compare tool score (0–100) to subjective rating (1–5) via Spearman rank correlation.

If ρ < 0.6, the weights in `scoring.yaml` get a calibration pass — but only once, and the rationale is committed in the repo.

### 6.3 Reporting

Each trip emits `score.json` and a one-page static HTML report rendered from a Jinja template: fused trajectory overlaid on ideal, per-component penalty bars, final score, suggested tip band. All reports live at `s3://…/scores/{trip_id}/`.

---

## 7. Milestones

| Step | Deliverable | Gate |
|------|-------------|------|
| 1 | Repo scaffold, `data_engine/ingest.py`, aligned Parquet for day1+day2, noise-fit script | S2 passes on calibration data |
| 2 | `ekf_node` (C++) consuming bag, producing `/fused/odom`; `ukf_node` variant | S1 passes |
| 3 | Terraform for S3 + ECR + EKS + Step Functions; `make deploy` green | S5 passes |
| 4 | GitHub Actions: test, build, push, headless sim, RMSE threshold check | S3 passes end-to-end |
| 5 | `ideal_driver` + `scoring`; record first 8 Uber/Lyft rides | S4 measurable |
| 6 | Static per-trip HTML report; README polish | — |

Steps 1–4 are this PRD's implementation scope. Steps 5–6 depend on collecting real Uber traces and are tracked here but governed by a v2.1 addendum once the first rides are in.

---

## 8. Open questions and explicit deferrals

1. **Weight calibration with n = 8.** Eight rides is not enough for statistically solid weight learning; it is enough for sanity. The weights stay hand-tuned until the sample reaches ≥ 30, at which point a simple regression on subjective ratings is warranted.
2. **Ground truth.** No RTK reference. "Ground truth" for S1 is an RTS-smoothed pass over the full trip; the S1 claim is framed as *improvement over raw GPS*, which is robust to this.
3. **Phone placement confounds.** If the phone rides in the driver's cup holder vs. my pocket vs. my hand, lateral-accel readings differ. The pitch/roll calibration step partly compensates; the long-term answer is a dedicated phone mount used consistently across rides.
4. **Speed-limit source.** OpenStreetMap `maxspeed` tags are incomplete in Raleigh. Fallback: hand-coded speed-limit segments for the three corridors I actually ride (Saint Mary's, Wade Ave, I-440). Captured in `config/ideal.yaml`.
5. **Ethics.** The tool scores my rides for my own tipping decisions and stays on my devices. No driver identification, no uploads, no sharing. This is stated once here and enforced by the fact that no such plumbing exists in the architecture.

---

## 9. What this tool is, in one sentence

A rider's side of the rideshare transparency problem: if the app can rate me with a star, I can rate the ride with sensor fusion.

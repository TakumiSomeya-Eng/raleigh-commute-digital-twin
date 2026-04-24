# Raleigh Commute Digital Twin — *Uber vs. My AI*

> A rider's side of the rideshare transparency problem: if the app can rate me with a star,
> I can rate the ride with sensor fusion.

**Status:** Phase 1 — in active development. See [milestones](#milestones).

---

## Design documents

| Document | Purpose |
|---|---|
| [PRD](Docs/PRD.md) | Why we're building this and what success looks like |
| [FRD](Docs/FRD.md) | What features define "done" (54 functional requirements) |
| [TRD](Docs/TRD.md) | How we build it — schemas, interfaces, NFRs, toolchain |
| [Dev Plan](Docs/DEV_PLAN.md) | In what order — 37 tasks across 6 phases |

---

## Quickstart

> Full quickstart documented in T5.5. For now, see individual `make` targets below.

```bash
# Prerequisites: Docker, Python 3.11, make
make bootstrap      # install pre-commit hooks, verify Docker
make data TRACE=day1
make data TRACE=day2
make synth
make bag   TRACE=day2
make fuse  TRACE=day2 FILTER=ekf
make eval  TRACE=day2 FILTER=ekf
make ideal TRACE=day2
make score TRACE=day2
make report TRACE=day2
# open out/day2/report.html in a browser
```

---

## Architecture

```
Raw CSVs (Sensor Logger) → data_engine → Parquet
                                            │
                                            ▼
                                  localization (ROS 2 EKF/UKF)
                                            │
                                            ▼
                                  ideal_driver (Valhalla + quintic poly)
                                            │
                                            ▼
                                  scoring → score.json → report.html
```

---

## Milestones

| Phase | Description | Status |
|---|---|---|
| P0 | Foundation — scaffolding, tooling | 🚧 In progress |
| P1 | Data engine — ingest, noise fit, synthetic | ⬜ Planned |
| P2 | Fusion — EKF + UKF nodes | ⬜ Planned |
| P3 | Filter evaluation — RMSE, NEES | ⬜ Planned |
| P4 | Ideal driver + scoring | ⬜ Planned |
| P5 | Reporting + Phase 1 validation | ⬜ Planned |
| Phase 2 | AWS deployment (EKS + Step Functions) | ⬜ Planned post Phase 1 |

---

## License

MIT © 2026 Takumi

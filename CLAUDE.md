# CLAUDE.md — Raleigh Commute Digital Twin: Uber vs. My AI

設計図。ルール・手順・決定事項を記録する。コードの説明は書かない（コード自体が語る）。

---

## プロジェクト概要

Sensor Logger（iPhone）で記録したUber乗車データをEKF/UKFで融合し、
「理想ドライバーと比較して運転手を採点・チップ提案する」ツール。

- **ドキュメント**: `docs/PRD.md` / `docs/FRD.md` / `docs/TRD.md` / `docs/DEV_PLAN.md`
- **Phase 1**: 37タスク / 6フェーズ — **全完了** ✅ (2026-05-22時点)
- **Phase 2**: AWS デプロイ（EKS + Step Functions）— **現在地** 🚧
- **現在の作業**: Phase 2 プランニング中（Hypothesis-Driven Frameworkで設計中）

---

## 開発プロセス（必読）

このプロジェクトは **Hypothesis Hierarchy Model** に従う。
すべての実装は必ず以下の順序で進める：

```
Value → Behavior → Domain → Interaction → Implementation
```

**エージェントへの強制制約:**
- Implementation層の作業開始前に、上位4層のRecapを必ずPR本文またはコメントに記載すること
- 上位層が未確認の場合、コード生成を行わず `.claude/prompts/1_value_and_behavior.md` を参照し、仮説定義を先に促すこと
- 「実装できるから作る」思考禁止。各アーキテクチャ選択には必ずEvidence（根拠）を添えること

**Living Docs**: `docs/LIVING_SPEC.md` を常に最新に保つこと

---

## マルチエージェント体制（Phase 2）

| エージェント | 役割 | 使用スキル |
|---|---|---|
| **Orchestrator** | 仮説検証のゲートキーパー、タスク分解、PR調整 | CLAUDE.md全体 + hypothesis prompts |
| **Infra Agent** | EKS/ECR/IAM/VPC のTerraform実装 | `.claude/skills/aws-infra.md` + `.claude/skills/docker-ops.md` |
| **Pipeline Agent** | Step Functions定義、Lambda/Fargate実装、S3スキーマ | `.claude/skills/aws-infra.md` + `.claude/skills/sensor-fusion.md` |
| **QA Agent** | テスト追加、RMSE回帰チェック、CI/CD設定 | `.claude/skills/pipeline-testing.md` |

**ルール:**
- Infra/Pipeline Agentは、OrchestratorがValue/Behavior/Domain仮説を確認するまでコードを生成しない
- QA AgentはPhase 4（`docs/LIVING_SPEC.md` 更新）の役割を担う
- サブエージェント完了後、メインセッションがコミット（権限の都合）

---

## 並列Track体制（Phase 1踏襲）

| Track | 言語 | 現在のタスク | 担当 |
|---|---|---|---|
| **Track A** | Python (`src/`, `infra/`) | Phase 2 実装 | メインセッション |
| **Track B** | Terraform (`infra/`) | インフラ定義 | Infra Agent |

**ルール:**
- Python と Terraform ファイルは必ず別コミットに分ける（staging混線防止）
- サブエージェント完了後、メインセッションがコミット

---

## コミット規則

```
{task_id}: {imperative verb} {object}

例: T6.1: add S3 bucket Terraform module with versioning and lifecycle rules
```

- 末尾に必ず: `Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>`
- HEREDOCで渡す（改行・特殊文字を確実にエスケープするため）
- Phase 2のタスクIDは `T6.x`〜`T11.x` を使用（Phase 1の37タスクの続き）

---

## ログフォーマット（TRD §4.4）

全ステージのstdout出力は以下の形式に統一:

```
[2026-04-19T18:34:05Z] [FR-1.5 ingest] INFO  {message}
```

- タイムスタンプ: `datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")`
- `print()` 禁止 → `sys.stdout.write()` / `sys.stderr.write()` を使う（ruff T20ルール）

---

## 既知の環境差異・ワークアラウンド

### Python 3.10 ローカル互換

- **問題**: ローカル環境が Python 3.10.2。CI は Python 3.11.9（TRD §5）。
- `datetime.UTC` は 3.11+ のみ → `datetime.timezone.utc` を使う
- ruffの `UP017`（`datetime.UTC` への自動昇格）を `pyproject.toml` の ignore リストに追加済み
- `requires-python = ">=3.11"` だが、ローカルテストは `py -3.10` で実行

### Windows / pre-commit / yamllint CP932

- yamllint を remote hookで動かすと CP932 エラー（日本語Windowsの文字コード問題）
- 解決策: `.pre-commit-config.yaml` で `local hook` + `python -X utf8 -m yamllint` を使用

### AWSクレデンシャル

- **絶対禁止**: コードやコミット履歴にAWSクレデンシャルを含めない
- GitHub ActionsはOIDCで認証（long-lived keysはGitHub Secretsにも置かない）
- ローカル開発は `aws sso login` または環境変数（.gitignore済みの`.env`）

---

## Sensor Logger CSV フォーマット

Sensor Logger（Kelvin Tan、iOS/Android）のエクスポート形式:

| ファイル | 必須カラム |
|---|---|
| `Location.csv` | `time`(int64 epoch-ns), `latitude`, `longitude`, `horizontalAccuracy`, `speedAccuracy`, `bearingAccuracy`, `speed`, `bearing` |
| `Accelerometer.csv` | `time`, `x`, `y`, `z` |
| `Gyroscope.csv` | `time`, `x`, `y`, `z` |
| `Gravity.csv` | `time`, `x`, `y`, `z` |
| `Orientation.csv` | `time`, `qw`, `qx`, `qy`, `qz`（古い版は `w`, `x`, `y`, `z`）|
| `Magnetometer.csv` | `time`, `x`, `y`, `z`（単位: µT） |
| `TotalAcceleration.csv` | `time`, `x`, `y`, `z` |

- `time` カラムは epoch **ナノ秒** の int64（精度ロスを防ぐため float 禁止）
- GPS は ~1 Hz、IMU は ~100 Hz で記録される

---

## Parquet メタデータ規則（TRD §1.11）

`parquet_io.write_parquet()` が自動付与するキー:

| キー | 内容 |
|---|---|
| `trip_id` | トレース名（例: `"day2"`） |
| `git_sha` | HEAD の短縮SHA |
| `schema_version` | `"1.0"` |
| `generated_at_utc` | ISO-8601 UTC |
| `base_trip_id` + `seed` | 合成シナリオのみ |

---

## ruff 設定要点

`pyproject.toml` の主要な選択ルール:

| ルール | 意味 | 備考 |
|---|---|---|
| `T20` | `print()` 禁止 | `tests/**` は免除 |
| `N815` | mixedCase クラス変数禁止 | `mag_x_uT` 等は `# noqa: N815` で抑制 |
| `UP017` | `datetime.UTC` 強制 | **ignore済み**（Python 3.10互換のため） |
| `RUF002` | 曖昧Unicode禁止 | **ignore済み**（en-dash・ギリシャ文字が意図的）|

---

## テスト実行

```bash
# Python ユニットテスト（ローカル）
py -3.10 -m pytest tests/unit/ -q

# 全ユニットテスト（コミット前に必ず実行）
py -3.10 -m pytest tests/unit/ -v
```

pre-commit は `git commit` 時に自動実行（ruff lint/format → clang-format）。

---

## ENU投影アンカー

```yaml
# config/data_gen.yaml
enu_anchor:
  lat0_deg: 35.773   # Raleigh, NC 近傍
  lon0_deg: -78.610
```

flat-earth / equirectangular 近似。回廊スパン < 10 km で有効（TRD §1.1）。

---

## フェーズゲート（現況）

| フェーズ | ゲート条件 | 状態 |
|---|---|---|
| P0 | make bootstrap && make test が通る | ✅ |
| P1 | KS-test が 80% 以上のチャネルで通過 | ✅ (T1.6完了) |
| P2 | EKF/UKF がday2.mcapに対してクラッシュしない | ✅ (EKF 8.9MB / UKF 7.9MB Parquet確認) |
| P3 | EKF RMSE ≤ 0.75 × GPS-only RMSE | ✅ (T3.5〜T3.7で修正済み) |
| P4 | score.json が day2 に対して出力される | ✅ |
| P5 | docker compose からreport.htmlが30分以内に生成 | ✅ |
| **Phase 2** | **AWSデプロイ完了・E2E smoke eval green** | 🚧 計画中 |

---

## Phase 2 概要（詳細は `docs/LIVING_SPEC.md` を参照）

**ゴール**: Phase 1のローカルパイプラインをAWSで再現し、複数トリップの自動処理・蓄積・比較を可能にする。

**設計デフォルト（仮説段階 — `docs/LIVING_SPEC.md` で検証中）:**
- Python jobs: ECS Fargate
- ROS 2 fusion: EKS (EC2 t3.medium)
- Orchestration: Step Functions
- Storage: S3（プレフィックス設計はFRD FR-12.1参照）
- Cost ceiling: $50/month steady state、$10/eval-run

**IaC**: Terraform（`infra/` 以下に実装）

**参照**: `docs/FRD.md` §FR-12、`infra/README.md`

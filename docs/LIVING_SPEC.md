# LIVING SPEC — Phase 2 AWS Deployment

**目的**: 仮説とその検証結果を蓄積する Living Documentation。
このファイルは実装が進むにつれて更新される。スナップショットではなく、常に「今の状態」を表す。

**更新方法**: `.claude/prompts/4_update_spec.md` のテンプレートに従い、
各Validated LearningをVL-Nとして採番して追記すること。

---

## 現在の状態サマリー

| 仮説層 | 状態 | 最終更新 |
|---|---|---|
| Value | 🔲 検証待ち | — |
| Behavior | 🔲 検証待ち | — |
| Domain | 🔲 検証待ち | — |
| Interaction | 🔲 検証待ち | — |
| Implementation | 🔲 検証待ち | — |

**Phase 2 MVP 開始ゲート**: Value〜Interaction の4層が ✅ になってから実装開始

---

## Value Hypothesis

**状態**: 🔲 未検証

### The Claim
Phase 1のローカルパイプラインをAWS化することで、
複数トリップの継続的・自動的な処理・蓄積が可能になり、
PRD Success Criterion S4（Spearman ρ ≥ 0.6）の検証が現実的な労力で達成できる。

### Acceptance Criteria
- [ ] AC-V1: 月$50以下のAWS費用で動作すること
- [ ] AC-V2: 新しいトリップの処理に必要なユーザー操作が「ファイルアップロードのみ」であること
- [ ] AC-V3: 8トリップのバッチ処理が手動操作なしで完了すること
- [ ] AC-V4: Phase 1と同じ `score.json` が出力されること

### Options
| | オプション | 状態 |
|---|---|---|
| A | フルAWS（EKS + Step Functions + Fargate） | 候補 |
| B | ミニマルAWS（Lambda + S3 trigger、ROS 2なし） | 候補 |
| C | スケジュール実行（EC2 spot + cron） | 候補 |

### Evidence
*（検証後に記入）*

---

## Behavior Hypothesis

**状態**: 🔲 未検証

### User Action
```
1. Uber乗車中: Sensor Logger を起動（変わらない）
2. 乗車後: CSVフォルダをS3にアップロード
3. 自動処理完了後: report.html + score.json を確認
4. チップ決定: 手動（ツールは提案のみ）
```

### Desired Interaction Model
```
"アップロードするだけ → あとは自動"
入力:  s3://rct-data/raw/{trip_id}/ への CSV アップロード
出力:  s3://rct-data/reports/{trip_id}/report.html
```

### Assumptions to Validate
- [ ] BA-1: 乗車後即座にCSVをアップロードする習慣が成立するか？
- [ ] BA-2: 15分以内の処理完了が体験として重要か？
- [ ] BA-3: スマホからS3操作するUIが必要か？（Phase 1スコープ外）

### Evidence
*（検証後に記入）*

---

## Domain Hypothesis

**状態**: 🔲 未検証（Value/Behavior承認後に作業）

### Domain Entities
*（`.claude/prompts/2_domain_and_interaction.md` 参照）*

### Business Rules
| ID | ルール | 状態 |
|---|---|---|
| BR-1 | score.jsonはPhase 1と同一スキーマ（TRD §1.8） | 定義済み |
| BR-2 | 処理中のトリップは上書きを受け付けない | 定義済み |
| BR-3 | RawファイルはImmutable（S3 versioning） | 定義済み |
| BR-4 | コスト上限$50/monthを超えた場合、新規処理を停止 | 定義済み |
| BR-5 | スコアリング結果には必ずconfig_hashが含まれる | 定義済み |

### Evidence
*（検証後に記入）*

---

## Interaction Hypothesis

**状態**: 🔲 未検証（Domain承認後に作業）

### Proposed Solution
```
S3 PutObject Event → EventBridge → Step Functions 自動起動
```

### Open Questions
- [ ] OQ-1: 全7ファイルのアップロード完了をどう検出するか？
- [ ] OQ-2: 処理失敗時のリトライポリシーをどうするか？

### Evidence
*（検証後に記入）*

---

## Implementation Hypothesis

**状態**: 🔲 未検証（Interaction承認後に作業）

### Architecture Decisions
| コンポーネント | 選択（仮） | 確定状態 |
|---|---|---|
| Python処理 | ECS Fargate | 🔲 仮説 |
| ROS 2 EKF | EKS (EC2) / py_ekf.py fallback | 🔲 仮説 |
| Orchestration | Step Functions Standard | 🔲 仮説 |
| IaC | Terraform | 🔲 仮説 |
| Auth | OIDC（GitHub Actions） | 🔲 仮説 |

### MVP Acceptance Criteria
- [ ] AC-MVP-1: S3アップロードで処理が自動開始する
- [ ] AC-MVP-2: 15分以内にscore.jsonが出力される
- [ ] AC-MVP-3: score.jsonがPhase 1値（34.8）と±2以内で一致する
- [ ] AC-MVP-4: 月額コスト$20以下（EKSなしのMVP）

### Evidence
*（実装後に記入）*

---

## Validated Learnings（蓄積）

### VL-1: py_ekf.py の精度は C++ EKF と同等（2026-05-23）

- **Observation**: T3.7でGPS-primary positions使用時、deviation raw = 0.790（C++/Python両方で同値）
- **Source**: docs/DEV_PLAN.md T3.7
- **Impact**: Phase 2 MVPでEKSを省略できる根拠。Interaction仮説「Python-only pipeline」を支持

### VL-2: EKSコントロールプレーンが月$72（コスト上限超過）（2026-05-30）

- **Observation**: EKSコントロールプレーン $0.10/hr × 720hr = $72/月 > AC-V1($50上限)
- **Source**: AWS価格表（us-east-1, 2026-05）
- **Impact**: EKSを使うとAC-V1違反。MVP段階はpy_ekf.pyを使う方針を支持

### VL-3: Phase 1のday2スコアは34.8/100（2026-05-22実測）

- **Observation**: harsh-brake events: 17回（≥3.0 m/s²）。aggregate = 34.8/100
- **Source**: docs/screenshots/report_day2.html
- **Impact**: AC-MVP-3の基準値として使用（許容誤差±2）

---

## Phase 2 タスクリスト（実装仮説承認後に有効化）

| TaskID | 内容 | 状態 |
|---|---|---|
| T6.1 | S3 bucket + prefix layout (FR-12.1) | 🔲 |
| T6.2 | ECR repositories x2 (FR-12.2) | 🔲 |
| T6.3 | IAM roles (FR-12.7) | 🔲 |
| T6.4 | ECS Fargate cluster + task definitions | 🔲 |
| T6.5 | Step Functions state machine（MVPの5ステージ） | 🔲 |
| T6.6 | EventBridge rule: S3 → Step Functions | 🔲 |
| T6.7 | GitHub Actions deploy.yaml | 🔲 |
| T6.8 | Cost budget + CloudWatch alert | 🔲 |
| T6.9 | E2E smoke test | 🔲 |
| T6.10 | EKS cluster（オプション、後期） | ⬜ |

---

## 改訂ログ

| バージョン | 日付 | 変更内容 |
|---|---|---|
| 0.1 | 2026-05-30 | Phase 2プランニング開始。VL-1〜3を記録。全仮説層を「未検証」で初期化 |

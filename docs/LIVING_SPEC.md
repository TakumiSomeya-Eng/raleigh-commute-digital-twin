# LIVING SPEC — Phase 2 AWS Deployment

**目的**: 仮説とその検証結果を蓄積する Living Documentation。
このファイルは実装が進むにつれて更新される。スナップショットではなく、常に「今の状態」を表す。

**更新方法**: `.claude/prompts/4_update_spec.md` のテンプレートに従い、
各Validated LearningをVL-Nとして採番して追記すること。

---

## 現在の状態サマリー

| 仮説層 | 状態 | 最終更新 |
|---|---|---|
| Value | ✅ 承認済み | 2026-05-30 |
| Behavior | ✅ 承認済み | 2026-05-30 |
| Domain | ✅ 承認済み | 2026-05-30 |
| Interaction | ✅ 承認済み | 2026-05-30 |
| Implementation | ✅ 承認済み | 2026-05-30 |

**Phase 2 MVP 開始ゲート**: ✅ 解除済み（全5層承認、2026-05-30）— T6.1から実装開始

---

## Value Hypothesis

**状態**: ✅ 承認済み（2026-05-30）

### The Claim
Phase 1のローカルパイプラインをAWS化することで、
複数トリップの継続的・自動的な処理・蓄積が可能になり、
PRD Success Criterion S4（Spearman ρ ≥ 0.6）の検証が現実的な労力で達成できる。

### Acceptance Criteria
- [ ] AC-V1: 月$50以下のAWS費用で動作すること
- [x] AC-V2: 新しいトリップの処理に必要なユーザー操作が「ファイルアップロードのみ」であること
- [ ] AC-V3: 8トリップのバッチ処理が手動操作なしで完了すること
- [ ] AC-V4: Phase 1と同じ `score.json` が出力されること

### Options
| | オプション | 状態 |
|---|---|---|
| A | フルAWS（EKS + Step Functions + Fargate） | ✅ 採用（MVP = EKSなし） |
| B | ミニマルAWS（Lambda + S3 trigger、ROS 2なし） | ❌ 除外（py_ekf.py必要） |
| C | スケジュール実行（EC2 spot + cron） | ❌ 除外（自動トリガー不可） |

### Evidence
- ユーザー（Takumi）が「後回しにしそう」と明言 → 乗車直後スマホ完結が必須
- アップロード追加実装ゼロ（AWSコンソール モバイルブラウザで対応）

---

## Behavior Hypothesis

**状態**: ✅ 承認済み（2026-05-30）

### User Action
```
1. Uber乗車中: Sensor Logger を起動（変わらない）
2. 乗車直後: スマホのAWSコンソール（モバイルブラウザ）からS3にCSVをアップロード
3. 自動処理（15分待つだけ、操作不要）
4. report.html確認 → チップ決定（手動）
```

### Desired Interaction Model
```
"スマホから30秒でアップロード → あとは自動"
手段:  AWSコンソール（モバイルブラウザ）
入力:  s3://rct-data/raw/{trip_id}/ への CSV アップロード
出力:  s3://rct-data/reports/{trip_id}/report.html
```

### Assumptions to Validate
- [ ] BA-1: 乗車後即座にCSVをアップロードする習慣が成立するか？
- [ ] BA-2: 15分以内の処理完了が体験として重要か？
- [ ] BA-3: スマホからS3操作するUIが必要か？（Phase 1スコープ外）

### Evidence
- 「後回しにしそう」→ 乗車直後・スマホ完結が摩擦最小化に必須
- AWSコンソール モバイルブラウザ = 追加実装ゼロ・即実現可能
- Sensor LoggerのCSVはスマホ内に保存されるため、スマホから直接アップロードが自然

---

## Domain Hypothesis

**状態**: ✅ 承認済み（2026-05-30）

### Domain Entities
*（`.claude/prompts/2_domain_and_interaction.md` 参照）*

### Business Rules
| ID | ルール | 状態 |
|---|---|---|
| BR-1 | score.jsonはPhase 1と同一スキーマ（TRD §1.8） | ✅ 承認 |
| BR-2 | 処理中のトリップは上書きを受け付けない | ✅ 承認 |
| BR-3 | RawファイルはImmutable（S3 versioning） | ✅ 承認 |
| BR-4 | コスト上限$50/monthを超えた場合、新規処理を停止してメール警告 | ✅ 承認 |
| BR-5 | スコアリング結果には必ずconfig_hashが含まれる | ✅ 承認 |

### Evidence
- BR-1〜5すべてユーザー承認済み（2026-05-30セッション）

---

## Interaction Hypothesis

**状態**: ✅ 承認済み（2026-05-30）

### Confirmed Solution
```
① スマホ → AWSコンソール（モバイルブラウザ） → S3アップロード
② S3 PutObject Event → EventBridge → Step Functions 自動起動
③ ingest → fuse → ideal → score → report（Fargate）
④-a 完了: スコア + report.htmlリンク + tip提案 をメール（SNS + SES）
④-b 失敗: 失敗ステップ名 + エラー内容 をメール（SNS + SES）
```

### Open Questions（解決済み）
- [x] OQ-1: 全7ファイルの検出 → Step Functions内でS3 ListObjects → ファイル数チェック → 不足なら Wait+Retry
- [x] OQ-2: リトライポリシー → Step Functions標準（指数バックオフ、最大3回）+ 失敗時メール通知

### Evidence
- 失敗通知方式: メール（SNS）をユーザーが選択
- メール内容: スコア + リンク + エラー内容（Option C）をユーザーが選択
- 追加実装: SNS + SES で 1〜2h、コスト月数円

---

## Implementation Hypothesis

**状態**: ✅ 承認済み（2026-05-30）

### Architecture Decisions
| コンポーネント | 選択（仮） | 確定状態 |
|---|---|---|
| Python処理 | ECS Fargate | ✅ 確定 |
| EKF | py_ekf.py（EKSなし） | ✅ 確定（VL-1, VL-2） |
| Orchestration | Step Functions Standard | ✅ 確定 |
| 通知 | SNS + SES メール | ✅ 確定（VL-5） |
| IaC | Terraform | ✅ 確定（ユーザー積極導入希望） |
| Auth | OIDC（GitHub Actions） | ✅ 確定 |

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



### VL-5: 失敗通知 = SNSメール（スコア + リンク + エラー内容）で確定（2026-05-30）

- **Observation**: 失敗に気づかないとS4検証でデータ不足になるリスクあり → メール通知が最適
- **Source**: Domain/Interaction仮説セッション（2026-05-30）
- **Impact**: SNS + SES をStep Functionsに追加。追加工数 1〜2h、コスト月数円

### VL-4: アップロード手段 = AWSコンソール（モバイルブラウザ）で確定（2026-05-30）

- **Observation**: 「後回しにしそう」→ 乗車直後スマホ完結が必須。AWSコンソールのモバイルブラウザでアップロード
- **Source**: Value/Behavior仮説セッション（2026-05-30）
- **Impact**: スマホアプリ開発不要（+20〜40h節約）。AC-V2確定

---

## Phase 2 タスクリスト（実装仮説承認後に有効化）

| TaskID | 内容 | 状態 |
|---|---|---|
| T6.1 | S3 bucket + prefix layout (FR-12.1) | 🚧 実装中 |
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
| 0.2 | 2026-05-30 | Value/Behavior仮説承認。VL-4追加。アップロード手段確定 |
| 0.3 | 2026-05-30 | Domain/Interaction仮説承認。VL-5追加。通知方式確定 |
| 0.4 | 2026-05-30 | Implementation仮説承認。全5層完了。MVP実装ゲート解除。T6.1開始 |

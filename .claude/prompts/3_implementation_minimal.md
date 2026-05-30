# Phase 3: Implementation Hypothesis & Minimal Build — AWS Deployment

> **前提**: Value/Behavior/Domain/Interaction の4層が `docs/LIVING_SPEC.md` に承認済みで記録されていること。
> ここで初めてコードと具体的なAWS構成の話ができる。

---

## Recaps of Validated Hypotheses

- **Value**: 月$50以下・アップロードのみ・Phase 1と同一score.json
- **Behavior**: S3アップロード → 自動処理 → report.html + score.json
- **Domain**: Trip/Score/Pipelineエンティティ、BR-1〜5、S3プライベート保存
- **Interaction**: S3 PutObject → EventBridge → Step Functions自動起動

---

## 1. Implementation Hypothesis

### Technical Strategy（アーキテクチャ選択と根拠）

| コンポーネント | 選択 | Evidence（根拠） |
|---|---|---|
| Python処理ジョブ | **ECS Fargate** | - ステートレス・バースト実行に最適<br>- コンテナはPhase 1 `docker/python.Dockerfile` を再利用<br>- Lambda 15分制限がdata_engine/scoring処理で超過する可能性（day2 = 14.8min） |
| ROS 2 EKF/UKFジョブ | **EKS (EC2 t3.medium)** | - ROS 2 DDS multicastにはEC2が必要（LambdaはVPC isolated networkのため不可）<br>- ただし **Python-only EKF fallback** (`scripts/py_ekf.py`) があるため、MVP段階ではEKSを省略できる |
| オーケストレーション | **Step Functions Standard** | - 15分超のジョブに対応（Express Workflowは5分上限）<br>- 可視化がデバッグに有用<br>- リトライ・DLQが組み込み |
| ストレージ | **S3** | - FRD FR-12.1準拠。すでに設計済み |
| IaC | **Terraform** | - `infra/` に既にプレースホルダー。PRD S5「全インフラをTerraformで」 |
| 認証 | **OIDC (GitHub Actions)** | - Long-lived keys不要。FRD FR-12.7 IAM least-privilege |

### Benchmarks / Cost Estimates

```
ECS Fargate (0.25 vCPU, 0.5 GB):
  - data_engine:  ~5 min × $0.0000031/vCPU-sec = $0.00093/run
  - scoring:      ~3 min × $0.0000031/vCPU-sec = $0.00056/run
  合計 Python処理: ~$0.002/トリップ

EKS (t3.medium spot, $0.014/hour):
  - fusion (15 min): $0.014 × 0.25 = $0.0035/トリップ
  - クラスター維持: ~$0/hour（min=0 autoscaler）

Step Functions Standard:
  - state transitions: ~20 × $0.000025 = $0.0005/実行

月20トリップの推定:
  - 20 × ($0.002 + $0.0035 + $0.0005) = $0.12/月
  - EKSコントロールプレーン: $0.10/hour = $72/月 ← ⚠️ 最大のコスト
  
→ EKSコントロールプレーンが支配的。MVPではEKS省略 + py_ekf.py使用を検討
```

---

## 2. Minimal MVP Scope（最小検証スコープ）

### MVP: "Python-only cloud pipeline"

**Phase 2 MVP（EKSなし）:**

```
S3 upload
    ↓ EventBridge
    ↓ Step Functions
    ├── Fargate: ingest (CSV → Parquet)
    ├── Fargate: py_ekf (Python EKF, scripts/py_ekf.py)
    ├── Fargate: ideal_driver (Valhalla in container)
    ├── Fargate: scoring
    └── Fargate: reporting
```

**意図的なOut-of-scope（MVP段階）:**
- EKS / ROS 2 C++ ノード（py_ekf.pyで代替）
- UKFノード（EKF onlyで十分）
- SNSメール通知
- CloudWatch Dashboard（手動確認で十分）
- Multi-region / DR

**MVPのAcceptance Criteria:**
- [ ] AC-MVP-1: `aws s3 cp ./day2-csv/ s3://rct-data/raw/day3/ --recursive` で処理が自動開始する
- [ ] AC-MVP-2: 15分以内に `s3://rct-data/scores/day3/score.json` が出力される
- [ ] AC-MVP-3: score.json の `aggregate_0_100` がローカル実行値と ±2 以内で一致する
- [ ] AC-MVP-4: AWS月額コストが$20以下（EKSなしのため大幅に削減）

---

## 3. Task Breakdown（実装タスク一覧）

Phase 2のタスクIDは `T6.x`〜（Phase 1の37タスクの続き）

| TaskID | 内容 | Blockers | Est. |
|---|---|---|---|
| **T6.1** | S3 bucket + prefix layout (FR-12.1) | — | 1.5h |
| **T6.2** | ECR repositories x2 (FR-12.2) | T6.1 | 1h |
| **T6.3** | IAM roles: GHA-OIDC, Fargate task, Step Functions (FR-12.7) | T6.1, T6.2 | 2h |
| **T6.4** | ECS Fargate cluster + task definitions (Python worker) | T6.2, T6.3 | 2.5h |
| **T6.5** | Step Functions state machine (MVP 5 stages) (FR-12.4) | T6.4 | 3h |
| **T6.6** | EventBridge rule: S3 PutObject → Step Functions (FR-12.4) | T6.5 | 1h |
| **T6.7** | GitHub Actions deploy.yaml: build → ECR → smoke eval (FR-12.5) | T6.2, T6.3 | 2h |
| **T6.8** | Cost budget + basic CloudWatch alert (FR-12.6) | T6.1 | 1h |
| **T6.9** | E2E smoke test: upload day2 → assert score.json | T6.6 | 2h |
| **T6.10** | *(Optional post-MVP)* EKS cluster for ROS 2 (FR-12.3) | T6.4 | 4h |

**Terraform module layout:**
```
infra/
  terraform/
    modules/
      s3/          ← T6.1
      ecr/         ← T6.2
      iam/         ← T6.3
      ecs/         ← T6.4
      stepfn/      ← T6.5, T6.6
      observability/ ← T6.8
      eks/         ← T6.10 (optional)
    envs/
      dev/         ← terraform.tfvars for dev
      prod/        ← terraform.tfvars for prod
    main.tf
    variables.tf
    outputs.tf
```

---

## エージェントへの指示（Infra/Pipeline Agent）

1. このRecapを読んだことをPR本文の冒頭に明記すること
2. T6.1から順番に実装すること。スキップ禁止
3. 各Terraformモジュールは独立してテスト可能にすること（`terraform plan` が通ること）
4. AWSクレデンシャルをコードに含めないこと（OIDC or 環境変数のみ）
5. 実装完了後、`.claude/prompts/4_update_spec.md` を使ってLiving Docsを更新すること

---

**現在の検証状態**: 🔲 未検証（Domain/Interaction承認後に使用）
**Phase 2 MVP 開始ゲート**: `docs/LIVING_SPEC.md` にValue〜Interaction層が記録されていること

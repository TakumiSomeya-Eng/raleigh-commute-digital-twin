# SKILL: AWS Infrastructure (Terraform)

このスキルはInfra AgentとPipeline Agentが使用する。
Phase 2のTerraformコードを書く前に必ずこのファイルを読むこと。

---

## プロジェクト固有のAWS設定

### リージョン
```
us-east-1（デフォルト）
```

### S3バケット構造（FR-12.1）
```
s3://rct-data-{suffix}/
  raw/{trip_id}/           ← ユーザーがCSVをアップロードする場所
  processed/{trip_id}/     ← aligned_100hz.parquet
  synthetic/{trip_id}/     ← 合成シナリオ（30日後Glacierへ）
  fused/{trip_id}/         ← fused_ekf.parquet / fused_ukf.parquet
  ideal/{trip_id}/         ← reference_path, ideal_speed, ideal_trajectory
  scores/{trip_id}/        ← score.json
  reports/{trip_id}/       ← report.html, index.html
```

**必須設定:**
- パブリックアクセス完全ブロック（`block_public_acls = true` 等全4項目）
- バージョニング有効
- `synthetic/` ライフサイクルルール: 30日後 Glacier

### ECRリポジトリ（FR-12.2）
```
rct/python-worker    ← docker/python.Dockerfile
rct/ros2-worker      ← docker/ros2.Dockerfile（Phase 2後期）
```
- スキャンオンプッシュ: 有効
- ライフサイクル: 最新10タグを保持

### IAMロール（FR-12.7 — least privilege）

| ロール | 用途 | 主な権限 |
|---|---|---|
| `rct-gha-role` | GitHub Actions OIDC | ECR push(2リポジトリのみ) + Step Functions:StartExecution |
| `rct-fargate-task-role` | ECS Fargate タスク実行 | S3:GetObject/PutObject(rct-data-* バケットのみ) |
| `rct-stepfn-role` | Step Functions 実行 | ECS:RunTask + EKS:CreateJob + SNS:Publish |
| `rct-eks-node-role` | EKSノード | ECR:GetAuthorizationToken + ECR:BatchGetImage |

**絶対禁止**: `*` ワイルドカードポリシー（FRD FR-12.7）

### ECS Fargate設定（FR-12.4）
```hcl
# Python worker のデフォルト
cpu    = 256   # 0.25 vCPU
memory = 512   # 0.5 GB

# Valhalla（ideal_driver）は大きめ
cpu    = 1024  # 1 vCPU
memory = 2048  # 2 GB
```

### Step Functions State Machine（FR-12.4）
```
StateType: Standard（Express不可 — 15分超のジョブに対応するため）

ステート命名規則: FR IDに合わせる
  "FR-1-Ingest"    → Fargate: data_engine ingest
  "FR-4-Fuse"      → Fargate: py_ekf.py (MVP) or EKS job (後期)
  "FR-9-Ideal"     → Fargate: ideal_driver (Valhalla)
  "FR-10-Score"    → Fargate: scoring
  "FR-11-Report"   → Fargate: reporting

リトライ設定:
  MaxAttempts: 3
  IntervalSeconds: 30
  BackoffRate: 2.0
  ErrorEquals: ["States.TaskFailed"]

DLQ: SQS dead-letter queue（失敗トリップを後で再処理できるよう）
```

---

## Terraform規則

### モジュール構造
```
infra/terraform/
  modules/
    s3/
      main.tf, variables.tf, outputs.tf
    ecr/
      main.tf, variables.tf, outputs.tf
    iam/
      main.tf, variables.tf, outputs.tf
    ecs/
      main.tf, variables.tf, outputs.tf
    stepfn/
      main.tf, variables.tf, outputs.tf
    observability/
      main.tf, variables.tf, outputs.tf
    eks/           ← Phase 2後期のみ
      main.tf, variables.tf, outputs.tf
  envs/
    dev/
      main.tf      ← module呼び出し
      terraform.tfvars
    prod/
      main.tf
      terraform.tfvars
  versions.tf      ← required_providers固定
```

### バージョン固定（versions.tf）
```hcl
terraform {
  required_version = ">= 1.7.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.50"
    }
  }
}
```

### 命名規則
```
リソース名: rct-{component}-{env}
例: rct-data-dev, rct-python-worker-prod, rct-pipeline-dev
```

### バックエンド設定
```hcl
# envs/dev/main.tf
terraform {
  backend "s3" {
    bucket = "rct-tfstate-{suffix}"
    key    = "dev/terraform.tfstate"
    region = "us-east-1"
  }
}
```

---

## コスト上限の強制（FR-12.6）

```hcl
# observability/main.tf に必ず含めること
resource "aws_budgets_budget" "monthly_ceiling" {
  name         = "rct-monthly-ceiling"
  budget_type  = "COST"
  limit_amount = "50"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator = "GREATER_THAN"
    threshold           = 80
    threshold_type      = "PERCENTAGE"
    notification_type   = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }
}
```

---

## 実装チェックリスト（各Terraformモジュール完了時）

- [ ] `terraform init && terraform validate` 通過
- [ ] `terraform plan` が意図したリソースのみ作成・変更
- [ ] `terraform plan` を2回実行して差分なし（idempotent）
- [ ] `*` ワイルドカードポリシーが使われていないことを確認
- [ ] パブリックアクセスのS3バケット/リソースがないことを確認
- [ ] コスト試算: このモジュールの月額コストを `#` コメントに記載

# infra/ — Phase 2 (AWS Deployment)

Phase 1ローカルパイプラインをAWSで再現する。
設計は Hypothesis-Driven（`docs/LIVING_SPEC.md` 参照）。全5層承認済み（2026-05-30）。

**MVPアーキテクチャ（EKSなし）:**

```
スマホ → AWSコンソール → S3 raw/{trip_id}/
                              ↓ EventBridge (PutObject)
                          Step Functions
                              ↓
   Fargate: ingest → fuse(py_ekf) → ideal → score → report
                              ↓
                          SNS + SES メール通知（スコア + report.htmlリンク + エラー内容）
```

EKSを省略する理由: EKSコントロールプレーンが$72/月でコスト上限$50を超過（VL-2）。
py_ekf.pyはC++ EKFと精度同等（VL-1）。

---

## Terraform 構成

```
infra/terraform/
  versions.tf              プロバイダ・バージョン固定
  modules/
    s3/                    ← T6.1 ✅ S3バケット + プレフィックス + ライフサイクル
    ecr/                   ← T6.2 (予定)
    iam/                   ← T6.3 (予定)
    ecs/                   ← T6.4 (予定)
    stepfn/                ← T6.5, T6.6 (予定)
    observability/         ← T6.8 (予定)
  envs/
    dev/                   ← dev環境のモジュール呼び出し
    prod/                  ← prod環境（予定）
```

---

## 初回セットアップ（chicken-and-egg）

リモートstateバケットは手動で1度だけ作成する（Terraformで管理する前の卵が先か問題）:

```bash
# 1. tfstate用バケットを手動作成（{suffix}は自分のアカウントID等）
aws s3api create-bucket \
  --bucket rct-tfstate-{suffix} \
  --region us-east-1

aws s3api put-bucket-versioning \
  --bucket rct-tfstate-{suffix} \
  --versioning-configuration Status=Enabled

# 2. envs/dev/main.tf の backend.bucket を rct-tfstate-{suffix} に書き換え
```

---

## デプロイ手順（dev）

```bash
cd infra/terraform/envs/dev

# tfvars を用意
cp terraform.tfvars.example terraform.tfvars
# terraform.tfvars を編集して bucket_suffix を設定

# 初期化
terraform init

# 構文チェック
terraform validate

# プラン確認（何が作られるか）
terraform plan

# 適用
terraform apply
```

---

## T6.1 検証チェックリスト（FR-12.1 DoD）

- [ ] `terraform validate` 通過
- [ ] `terraform plan` が S3バケット1個 + 関連設定のみ作成
- [ ] `terraform plan` を2回実行して差分なし（idempotent）
- [ ] バケットがパブリックアクセス完全ブロック
- [ ] バージョニング有効
- [ ] synthetic/ に30日後Glacierライフサイクル
- [ ] バケット名がパラメータ化されている（ハードコードなし）

---

## 進捗（Phase 2 タスク）

| TaskID | 内容 | 状態 |
|---|---|---|
| T6.1 | S3 bucket + prefix layout (FR-12.1) | ✅ 完了（apply済み） |
| T6.2 | ECR repository: python-worker only (FR-12.2, MVP) | 🚧 実装完了・検証待ち |
| T6.3 | IAM roles: 4 roles, least-privilege (FR-12.7) | ✅ 完了（apply済み 2026-05-30） |
| T6.4 | ECS Fargate cluster + task definitions | ✅ 完了（apply済み 2026-05-30） |
| T6.5 | Step Functions state machine + SNS notify | ✅ 完了（apply済み 2026-05-30） |
| T6.6 | EventBridge rule: S3 → Step Functions | ✅ 完了（apply済み 2026-05-30） |
| T6.7 | GitHub Actions deploy.yaml (build+push+smoke-eval) | 🚧 実装完了・Secrets設定待ち |
| T6.8 | Cost budget + CloudWatch alert | ⬜ |
| T6.9 | E2E smoke test | ⏸ Dockerイメージ待ち |

詳細は `docs/LIVING_SPEC.md` 参照。

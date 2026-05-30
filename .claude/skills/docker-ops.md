# SKILL: Docker Operations

このスキルはInfra AgentとPipeline Agentが使用する。
Dockerfile・docker-compose・コンテナビルドに関する作業前に読むこと。

---

## 既存コンテナ構成（Phase 1）

```yaml
# docker-compose.yml
services:
  python:    ← docker/python.Dockerfile
  ros2:      ← docker/ros2.Dockerfile
  valhalla:  ← gisops/valhalla（外部イメージ）

network: rct-net（bridge）
```

### python.Dockerfile
```
Base: ubuntu:24.04
Python: 3.11（pip pinned via requirements.txt）
Mount: /workspace（repo）, /data（CSVs）, /out（outputs）
```

### ros2.Dockerfile
```
Base: osrf/ros:jazzy-desktop
追加: colcon, ament_cmake, Eigen3
Mount: /workspace, /data, /out
```

---

## Phase 2 でのコンテナ利用

### ECRへのプッシュ（GitHub Actions OIDC）

```yaml
# .github/workflows/deploy.yaml より
- name: Build and push Python worker
  uses: docker/build-push-action@v5
  with:
    context: .
    file: docker/python.Dockerfile
    push: true
    tags: ${{ env.ECR_URI }}/rct/python-worker:${{ github.sha }}
    cache-from: type=gha
    cache-to: type=gha,mode=max
```

### ECS Fargate でのコンテナ実行

```hcl
# infra/terraform/modules/ecs/main.tf
resource "aws_ecs_task_definition" "python_worker" {
  family                   = "rct-python-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512

  container_definitions = jsonencode([{
    name  = "python-worker"
    image = "${var.ecr_uri}/rct/python-worker:latest"
    environment = [
      { name = "S3_BUCKET", value = var.s3_bucket },
      { name = "AWS_DEFAULT_REGION", value = "us-east-1" }
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = "/ecs/rct-python-worker"
        "awslogs-region"        = "us-east-1"
        "awslogs-stream-prefix" = "ecs"
      }
    }
  }])
}
```

---

## Valhalla コンテナ設定（Phase 2）

```
Phase 2では Fargate内に Valhalla を同梱する。
（Phase 1の docker-compose サービスをFargate化）

NCタイルのキャッシュ:
  - 初回: S3からダウンロード (s3://rct-data-{suffix}/valhalla-tiles/nc.tar.gz)
  - 以降: EFSマウントでキャッシュ（オプション）
```

---

## ビルドキャッシュ戦略

```bash
# ローカルビルド（開発中）
docker build \
  --cache-from rct-python:cache \
  --build-arg BUILDKIT_INLINE_CACHE=1 \
  -t rct-python:dev \
  -f docker/python.Dockerfile .

# CI/CDビルド（GitHub Actions cache）
# deploy.yamlでcache-from: type=gha を使用
```

---

## Hadolint（Dockerfile lint）

```bash
# ローカル確認
hadolint docker/python.Dockerfile
hadolint docker/ros2.Dockerfile

# pre-commit hookで自動実行（.pre-commit-config.yaml設定済み）
```

**主要なHadolintルール（このプロジェクトで遵守）:**
- `DL3008`: `apt-get install` にバージョンピン（`=` 指定）
- `DL3009`: `apt-get clean` でキャッシュ削除
- `DL3025`: CMD/ENTRYPOINTはJSON形式
- `SC2086`: 変数展開はダブルクォートで囲む

---

## コンテナサイズ目標（TRD §T0.2 DoD）

| イメージ | 上限 | 確認方法 |
|---|---|---|
| python worker | 1.5 GB | `docker images` |
| ros2 worker | 3 GB | `docker images` |

**サイズ削減のヒント:**
- multi-stage build（builderステージとruntimeステージを分ける）
- `--no-install-recommends` でapt最小化
- `.dockerignore` でout/, data/, .git/, __pycache__/ を除外

---

## ローカルでFargate動作を再現する方法

```bash
# Fargateタスクと同じ環境でローカルテスト
docker run --rm \
  -e S3_BUCKET=rct-data-dev \
  -e AWS_PROFILE=dev \
  -v ~/.aws:/root/.aws:ro \
  rct-python:dev \
  python -m data_engine ingest --trace day2
```

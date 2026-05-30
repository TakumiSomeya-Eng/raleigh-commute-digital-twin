# SKILL: Pipeline Testing & Quality Gates

このスキルはQA Agentが使用する。
テストを追加する前、CIを設定する前に必ずこのファイルを読むこと。

---

## テスト体系

### Phase 1（ローカル）— 完了済み

```
tests/
  unit/         ← 360テスト（make test で実行）
  integration/  ← Valhalla必要（@pytest.mark.integration）
  fixtures/
    baseline_rmse.json        ← 回帰テスト基準値
    tiny_day2_60s/            ← 60秒MCAPスライス
    synthetic_sinusoid/       ← 合成サイン波フィクスチャ
```

### Phase 2（AWS）— 追加予定

```
tests/
  cloud/
    test_s3_upload_trigger.py   ← S3 → Step Functions トリガーテスト
    test_fargate_ingest.py      ← Fargate ingestジョブのテスト
    test_e2e_cloud_day2.py      ← day2のフルE2Eクラウドテスト
    conftest.py                 ← AWS接続のスキップ条件
```

---

## テスト実行コマンド

```bash
# ユニットテスト（ローカル、毎回必ず）
py -3.10 -m pytest tests/unit/ -q

# インテグレーションテスト（Valhalla Docker必要）
pytest tests/integration/ -m integration -v

# Phase 2 クラウドテスト（AWS認証必要）
pytest tests/cloud/ -m cloud --aws-profile dev -v

# CIで全実行
make test
```

---

## フィクスチャ規則

### tiny_day2_60s フィクスチャ
- day2の最初の60秒を切り出したもの
- EKF/UKFのインテグレーションテストに使用
- 生成: `python scripts/make_fixtures.py --trace day2 --duration 60`

### baseline_rmse.json
```json
{
  "trip_id": "day2",
  "filter": "ekf",
  "overall_rmse_m": 0.XX,
  "gps_only_rmse_m": 0.XX,
  "improvement_pct": 25.X,
  "generated_at": "2026-05-23T..."
}
```
**Phase 2のデプロイCIでこのファイルと比較（FRD FR-12.5）:**
```python
assert cloud_rmse <= baseline_rmse * 1.10, "RMSE regression > 10%"
```

---

## CI/CDパイプライン（.github/workflows/）

### ci.yaml（PRごと）
```yaml
jobs:
  lint:           # pre-commit --all-files
  py-unit:        # pytest tests/unit/
  cpp-unit:       # colcon test (ROS 2コンテナ)
  integration:    # pytest tests/integration/ (Valhallaコンテナ込み)
```
**目標**: < 10分（FRD FR-12.5）

### deploy.yaml（main pushごと）
```yaml
jobs:
  build-push:     # Docker build → ECR push（OIDC認証）
  smoke-eval:     # day2フィクスチャでE2Eテスト
  regression:     # RMSE baseline比較（±10%以内ならOK）
```
**注意**: `deploy.yaml` のトリガーはmainへのpushのみ。PRでは実行しない

---

## Phase 2 受け入れ基準テスト

### AC-MVP-1テスト
```python
@pytest.mark.cloud
def test_s3_upload_triggers_pipeline(s3_client, sfn_client, day2_csv_dir):
    # day2のCSVをS3にアップロード
    s3_client.upload_folder(day2_csv_dir, "raw/test_trip/")
    # Step Functionsが5分以内に起動されること
    execution = wait_for_execution(sfn_client, "test_trip", timeout=300)
    assert execution["status"] == "RUNNING"
```

### AC-MVP-3テスト（スコア回帰テスト）
```python
@pytest.mark.cloud
def test_cloud_score_matches_local(cloud_score_json):
    LOCAL_BASELINE = 34.8  # VL-3: Phase 1 day2スコア
    TOLERANCE = 2.0
    assert abs(cloud_score_json["aggregate_0_100"] - LOCAL_BASELINE) <= TOLERANCE
```

---

## テスト品質基準

### ユニットテスト
- 各関数: 正常系1つ + 境界値1つ + エラーケース1つ（最低3テスト）
- 数値精度: 物理量の比較は `pytest.approx(rel=1e-3)` または絶対値で
- モック: AWSリソースへのアクセスは `moto` でモック

```python
from moto import mock_s3
@mock_s3
def test_upload_to_s3():
    ...
```

### インテグレーションテスト
- `@pytest.mark.integration` マーカー必須
- `skip if not docker` 条件を `conftest.py` で管理
- Valhallaコンテナが起動していない場合は `pytest.skip`

### クラウドテスト
- `@pytest.mark.cloud` マーカー必須
- AWS認証がない場合は `pytest.skip`
- テスト用トリップID: `test_day2_{random_suffix}` (本番データと区別)
- テスト完了後: S3のテストデータを自動クリーンアップ (`autouse=True`)

---

## Living Docs への記録（Phase 4との連携）

テスト結果はValidated Learningとして記録する:

```markdown
# .claude/prompts/4_update_spec.md に追記する形式

- **Observation**: [テスト結果]
- **Source**: pytest出力 / CI ログ
- VL-N として採番
```

**特に記録すべきもの:**
- RMSE改善率の実測値（目標: ≥25%、PRD S1）
- クラウド処理時間（目標: <15分、FRD FR-12.4）
- 月額コスト（目標: <$50、AC-V1）

# SKILL: Sensor Fusion & Data Pipeline

このスキルはPipeline Agentが使用する。
センサーフュージョン・データ処理に関するコードを書く前に必ずこのファイルを読むこと。

---

## パイプライン全体像

```
iPhone (Sensor Logger)
  └─ Location.csv + Accelerometer.csv + Gyroscope.csv + Gravity.csv
     Orientation.csv + Magnetometer.csv + TotalAcceleration.csv
        │
        ▼
  data_engine (src/data_engine/)
  └─ ingest.py: CSV → 100Hz aligned Parquet
  └─ noise_fit.py: ノイズモデルフィッティング
  └─ synth.py: 合成シナリオ生成
        │
        ▼
  bag_bridge (bag_bridge/)
  └─ parquet_to_mcap.py: Parquet → ROS 2 MCAP
        │
        ▼
  localization (src/localization/)
  └─ ekf_node.cpp: C++ EKF (Phase 1)
  └─ [scripts/py_ekf.py: Python EKF fallback ← Phase 2 MVPで使用]
        │
        ▼
  evaluation (src/evaluation/)
  └─ RMSE vs GPS-only基準
  └─ P3ゲート: EKF RMSE ≤ 0.75 × GPS-only RMSE
        │
        ▼
  ideal_driver (src/ideal_driver/)
  └─ Valhalla Meili map-matching
  └─ 速度プロファイル + 軌道合成
        │
        ▼
  scoring (src/scoring/)
  └─ 6コンポーネント × 重み → 0〜100スコア
        │
        ▼
  reporting (src/reporting/)
  └─ Jinja2 + Folium → report.html
```

---

## データスキーマ（TRD §1参照）

### 単位系（必ず守ること）

| 量 | 単位 | 注意 |
|---|---|---|
| 位置 | m（メートル、ENU） | WGS-84は `_wgs84` サフィックス |
| 速度 | m/s | |
| 加速度 | m/s² | ボディフレーム |
| ジャーク | m/s³ | 縦方向のみ |
| 角度 | rad | `[-π, π]` に正規化 |
| 角速度 | rad/s | |
| 時間（相対） | s | `seconds_elapsed` が正規 |
| 時間（絶対） | ns | `time_ns`, int64 epoch ns |
| 曲率 | 1/m | 符号付き（左折が正） |

### ENU座標アンカー
```python
# config/data_gen.yaml より
lat0_deg = 35.773   # Raleigh, NC
lon0_deg = -78.610
```

### Parquetスキーマ（主要なもの）

**aligned_100hz.parquet** (src/data_engine/schemas.py: `Aligned100Hz`):
- `t_s`: float — seconds_elapsed, 0.00, 0.01, ...（100Hzグリッド）
- `px_m`, `py_m`: float — ENU位置
- `horizontal_accuracy_m`: float — GPS精度（R行列に使用）
- `gps_interpolated`: bool — TrueならGPS補間値

**fused_ekf.parquet** (bag_bridge経由):
- `t_s`, `px_m`, `py_m`, `v_mps`, `psi_rad`, `psi_dot_rps`
- `cov_xx`, `cov_yy`, `cov_yaw`

---

## EKFの実装規約

### 状態ベクトル（CTRV）
```
x = [px, py, v, psi, psi_dot]  (5次元)
```

### 観測モデル
```
GPS位置:   z = [px, py]
GPS速度:   z = [v]
GPS方位:   z = [psi]  (v > bearing_min_speed_mps = 1.0 m/s のときのみ)
```

### χ²ゲート
```python
# 2D位置更新の棄却閾値（99%信頼区間）
CHI2_THRESHOLD_2D = 9.21  # χ²(2, 0.99)

# 健全性チェック: rejection_rate > 5% で DEGRADED
# DEGRADED時はゲートをバイパスして再収束させる（T3.5の修正）
```

### Phase 2 MVPでの py_ekf.py 使用

Phase 2 MVPではC++ EKFの代わりに `scripts/py_ekf.py` を使用。
理由: EKSコントロールプレーン $72/月 > コスト上限 $50/月（VL-2）

```bash
# ECS Fargate から py_ekf.py を実行する場合
python scripts/py_ekf.py \
  --input s3://rct-data-{suffix}/processed/{trip_id}/aligned_100hz.parquet \
  --output s3://rct-data-{suffix}/fused/{trip_id}/fused_ekf.parquet
```

---

## Valhalla設定

```json
// docker/valhalla/valhalla.json より
{
  "meili": {
    "default": {
      "sigma_z": 4.07,
      "gps_accuracy": 5.0,
      "search_radius": 50,
      "max_route_distance_factor": 5,
      "interpolation_distance": 10
    }
  }
}
```

**AWS環境でのValhalla**: Fargateコンテナに同梱。
NCタイルは初回実行時にS3からコピー（Geofabrik NC extractを事前にS3に配置）。

---

## スコアリング規則

### 6コンポーネント（config/scoring.yaml）
```yaml
components:
  jerk:          weight: 0.20  # ジャーク
  harsh_brake:   weight: 0.20  # 急ブレーキ
  lateral_accel: weight: 0.20  # 横加速度
  speed:         weight: 0.15  # 速度遵守
  deviation:     weight: 0.15  # 経路逸脱
  lane_change:   weight: 0.10  # 車線変更
```

### 集計式
```python
aggregate_raw = sum(weight_i * penalty_i)  # [0, 1]
score_0_100 = 100 * (1 - aggregate_raw)    # [0, 100]
```

### チップ提案テーブル
```yaml
tip_bands:
  - score_min: 90  tip_pct: 25
  - score_min: 75  tip_pct: 20
  - score_min: 60  tip_pct: 15
  - score_min: 45  tip_pct: 10
  - score_min: 0   tip_pct: 0
```

---

## Phase 2での注意事項

1. **スキーマバージョン**: `score.json` のスキーマは Phase 1と同一（TRD §1.8）。変更禁止
2. **再現性**: 同じinputから同じoutputが得られること（config_hashで検証）
3. **S3パス規則**: `s3://rct-data-{suffix}/{stage}/{trip_id}/` を厳守（FRD FR-12.1）
4. **タイムアウト**: Fargate taskのタイムアウトは `3600s`（1時間）をデフォルトに設定

---

## Phase 2での検証ゲート（AC-MVP-3）

```python
# Phase 1のday2スコア: 34.8 (VL-3より)
# Phase 2で同じトリップを処理した場合の許容誤差: ±2
assert abs(cloud_score - 34.8) <= 2.0, f"Score regression: {cloud_score}"
```

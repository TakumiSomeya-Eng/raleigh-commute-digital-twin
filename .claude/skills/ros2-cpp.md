# SKILL: ROS 2 C++ (localization nodes)

このスキルはFusion Agentが使用する（Phase 2後期 — EKS導入後）。
ROS 2 C++ノードのコードを書く前に必ずこのファイルを読むこと。

---

## Phase 2での位置づけ

**Phase 2 MVP段階ではこのスキルを使わない。**
→ Python EKF fallback (`scripts/py_ekf.py`) で代替（VL-1, VL-2参照）

**このスキルが必要になる条件:**
- EKSを導入する判断が下された（月コスト上限の再検討後）
- C++ EKFがPython EKFより精度で明確に優れていることが確認された
- スループット要件（複数トリップの並列処理）がFargateで賄えなくなった

---

## ROS 2環境

```
ROS 2 Jazzy (Ubuntu 24.04 base)
DDS: Fast-DDS (デフォルト)
ビルドツール: colcon
テスト: gtest + pytest
```

**Dockerイメージ**: `docker/ros2.Dockerfile`
**ECRリポジトリ**: `rct/ros2-worker`

---

## パッケージ構成

```
src/localization/
  CMakeLists.txt
  package.xml
  include/localization/
    ctrv_model.hpp       ← CTRV motion model (header-only)
    chi2_gate.hpp        ← χ²外れ値ゲート
    sigma_points.hpp     ← UKF用シグマ点
    diagnostics.hpp      ← 健全性モニタリング
  src/
    ekf_node.cpp         ← EKFノード
    ukf_node.cpp         ← UKFノード
    bag_bridge.cpp       ← MCAP replay helper
  launch/
    ekf.launch.py
    ukf.launch.py
  test/
    test_ctrv_model.cpp
    test_chi2_gate.cpp
    test_sigma_points.cpp
    test_diagnostics.cpp
```

---

## CTRV状態ベクトル

```cpp
// 状態: [px, py, v, psi, psi_dot]
using State5d = Eigen::Vector<double, 5>;
using Matrix5d = Eigen::Matrix<double, 5, 5>;

// ψ=0 特異点 (l'Hôpital分岐)
constexpr double PSI_DOT_THRESHOLD = 1e-6;

// ψは[-π, π]に正規化
double normalize_angle(double angle);
```

---

## トピック設定

| トピック | 型 | QoS | 方向 |
|---|---|---|---|
| `/gps/fix` | `sensor_msgs/NavSatFix` | SENSOR_DATA | Subscribe |
| `/imu/data` | `sensor_msgs/Imu` | SENSOR_DATA | Subscribe |
| `/mag` | `sensor_msgs/MagneticField` | SENSOR_DATA | Subscribe |
| `/fused/odom` | `nav_msgs/Odometry` | Reliable, keep-last 100 | Publish |
| `/fused/diagnostics` | `diagnostic_msgs/DiagnosticArray` | 1Hz | Publish |

---

## 健全性状態

```
OK       — 正常動作
DEGRADED — rejection_rate > 5%（過去10秒）→ χ²ゲートをバイパス
DIVERGED — NEES > 3 × state_dim（5秒以上継続）
```

**T3.5の修正**: DEGRADED時にχ²ゲートをバイパスすることで
highway exit decelerationでの発散ループを防ぐ。

---

## EKS設定（Phase 2後期）

```yaml
# k8s/ekf-job.yaml (FR-12.3)
apiVersion: batch/v1
kind: Job
spec:
  template:
    spec:
      containers:
      - name: ekf-node
        image: {ECR_URI}/rct/ros2-worker:latest
        resources:
          requests:
            cpu: "1"
            memory: "2Gi"
          limits:
            cpu: "2"
            memory: "4Gi"
```

---

## テスト規則（gtest）

```cpp
// テスト命名
TEST(CTRVModel, StraightLineMatchesCV) { ... }
TEST(Chi2Gate, Day1OutlierRejected) { ... }
TEST(SigmaPoints, MeanCovRoundTrip) { ... }

// フィクスチャ
// tests/fixtures/tiny_day2_60s.mcap  ← 60秒スライス
// tests/fixtures/outlier_day1_sample.csv  ← 122m外れ値サンプル
```

---

## K8sジョブ完了の検出（Step Functionsから）

```python
# Step Functions → AWS Batch or EKS RunJob
# EKS Job完了はJobStatusにポーリング
# 推奨: AWS Batch on EKS (Step Functions.TaskState.EksRunJob)
```

# SKILL: SUMO + OSM — Synthetic Trip Generation (Phase 3)

このスキルは Impl Agent と Test Writer Agent が使用する。
SUMO関連のコードを書く前に必ずこのファイルを読むこと。

---

## Phase 3 の目的

Raleigh NC の実道路ネットワーク上で3種の運転スタイル
（calm / normal / aggressive）の仮想トリップを生成し、
既存パイプライン（ingest → fuse → ideal → score → report）に
変更なく流せる Sensor Logger CSV 形式で出力する。

成果物: 動画A（SUMO-GUI + パイプラインログ）+ 動画B（Foliumアニメーション）

---

## SUMO インストール（Windows）

```powershell
# 公式インストーラー（GUI付き推奨）
# https://sumo.dlr.de/docs/Downloads.php
# → SUMO 1.20.0 Windows installer (.msi) をダウンロード

# インストール後、環境変数を確認
echo %SUMO_HOME%
# → C:\Program Files (x86)\Eclipse\Sumo が設定されていればOK

# PATH確認
sumo --version
sumo-gui --version
netconvert --version
```

---

## OSM → SUMO ネットワーク変換（T8.1）

### Raleigh のバウンディングボックス（Saint Mary's Street 周辺）

```
北: 35.790
南: 35.760
東: -78.590
西: -78.640
```

### ダウンロード＆変換コマンド

```bash
# 1. OSMデータ取得（osmosis または overpass API）
python -m sumolib.osmtools.download \
  --bbox "-78.640,35.760,-78.590,35.790" \
  --output sumo/osm/raleigh.osm.xml

# 2. SUMOネットワーク変換
netconvert \
  --osm-files sumo/osm/raleigh.osm.xml \
  --output-file sumo/net/raleigh.net.xml \
  --geometry.remove \
  --roundabouts.guess \
  --ramps.guess \
  --junctions.join \
  --tls.guess-signals \
  --tls.discard-simple \
  --tls.join \
  --output.street-names \
  --output.original-names \
  --osm.sidewalks false \
  --osm.crossings false \
  --keep-edges.by-vclass passenger

# 3. 検証
sumo-gui -n sumo/net/raleigh.net.xml
```

---

## 運転スタイル定義（T8.2）

3スタイルをSUMOの `vType` パラメータで定義する。

| パラメータ | calm | normal | aggressive |
|---|---|---|---|
| `speedFactor` | 0.85 | 1.00 | 1.20 |
| `speedDev` | 0.05 | 0.10 | 0.20 |
| `accel` | 1.5 | 2.6 | 4.0 |
| `decel` | 2.0 | 4.5 | 7.0 |
| `sigma` | 0.1 | 0.5 | 0.9 |
| `lcCooperative` | 1.0 | 0.5 | 0.0 |
| `lcSpeedGain` | 0.5 | 1.0 | 2.0 |
| `emergencyDecel` | 4.0 | 9.0 | 15.0 |

```xml
<!-- sumo/styles/calm.add.xml -->
<additional>
  <vType id="calm_driver"
    speedFactor="0.85" speedDev="0.05"
    accel="1.5" decel="2.0" sigma="0.1"
    lcCooperative="1.0" lcSpeedGain="0.5"
    emergencyDecel="4.0" color="0,200,0"/>
</additional>
```

---

## FCD 出力フォーマット（T8.3 の入力）

```bash
# SUMO を FCD（Floating Car Data）出力モードで実行
sumo -n sumo/net/raleigh.net.xml \
     -r sumo/routes/calm.rou.xml \
     --fcd-output sumo/fcd/calm_trip.xml \
     --fcd-output.geo true \
     --step-length 0.01 \
     --begin 0 --end 900 \
     --no-step-log
```

FCD の XML 構造:

```xml
<fcd-export>
  <timestep time="0.00">
    <vehicle id="calm_0"
      x="-78.6123" y="35.7789"   <!-- WGS-84 lon/lat (--geo フラグ) -->
      speed="8.33"                <!-- m/s -->
      angle="92.4"                <!-- 度、北=0、時計回り -->
      pos="12.3"                  <!-- エッジ上の位置 m -->
      lane="E123_0"/>
  </timestep>
  ...
</fcd-export>
```

**単位注意:**
- `speed`: m/s（変換不要、Sensor Logger と同じ）
- `angle`: SUMO は北=0・時計回り → `bearing = angle` で OK
- `x`, `y`: WGS-84 の経緯度（`--fcd-output.geo true` 必須）

---

## Sensor Logger CSV 変換規則（T8.3 のコア）

FCD → 7CSVへのマッピング:

| Sensor Logger ファイル | SUMO FCD から生成 | 変換方法 |
|---|---|---|
| `Location.csv` | `x`, `y`, `speed`, `angle` | 直接マッピング |
| `Accelerometer.csv` | `speed` の差分 | `ax = Δv/Δt × cos(heading)` |
| `Gyroscope.csv` | `angle` の差分 | `gz = Δangle/Δt` |
| `Gravity.csv` | 定数（水平移動前提） | `gx=0, gy=0, gz=-9.81` |
| `Orientation.csv` | `angle` から quaternion | `qw = cos(yaw/2)` etc. |
| `Magnetometer.csv` | `angle` から磁北方向 | `mx = cos(angle), my = sin(angle)` |
| `TotalAcceleration.csv` | Accel + Gravity の合成 | ベクトル加算 |

### Location.csv の列定義（TRD §1準拠）

```
time,latitude,longitude,altitude,speed,course,
horizontalAccuracy,verticalAccuracy,speedAccuracy,
bearingAccuracy,floor
```

- `time`: epoch **ナノ秒** int64（`timestamp_s × 1e9` で変換）
- `altitude`: 0.0（SUMO は2D）
- `horizontalAccuracy`: スタイル別固定値（calm=3.0, normal=5.0, aggressive=8.0）
- `course` = `bearing` = SUMO `angle`

---

## ノイズモデル（T8.4）

`src/data_engine/noise_fit.py` の既存ノイズパラメータを流用する。

```python
# synth.py の NoiseFit から取得した実測値
GPS_SIGMA_M   = 4.07   # GPS位置誤差 [m]
ACCEL_SIGMA   = 0.15   # 加速度センサー [m/s²]
GYRO_SIGMA    = 0.008  # ジャイロ [rad/s]
MAG_SIGMA     = 2.5    # 磁力計 [µT]
```

ノイズ付与は `numpy.random.normal(0, sigma)` で各サンプルに加算。
GPS は `horizontalAccuracy` に連動して sigma をスケール。

---

## ディレクトリ構成（T8.1〜T8.5 完成後）

```
sumo/
  osm/
    raleigh.osm.xml          ← T8.1: OSMダウンロード
  net/
    raleigh.net.xml          ← T8.1: netconvert出力
  styles/
    calm.add.xml             ← T8.2: vType定義
    normal.add.xml
    aggressive.add.xml
  routes/
    calm.rou.xml             ← T8.2: ルート定義
    normal.rou.xml
    aggressive.rou.xml
  fcd/
    calm_trip.xml            ← T8.3: SUMO FCD出力
    normal_trip.xml
    aggressive_trip.xml
  cfg/
    calm.sumocfg             ← T8.2: SUMO設定ファイル
    normal.sumocfg
    aggressive.sumocfg

src/data_engine/
  sumo_adapter.py            ← T8.3+T8.4: FCD→CSV変換

tests/unit/
  test_sumo_adapter.py       ← T8.5: TDD（先に書く）

tests/fixtures/sumo/
  tiny_calm_30s.xml          ← T8.5: 30秒フィクスチャ
  tiny_normal_30s.xml
  tiny_aggressive_30s.xml
```

---

## TDD 規則（Test Writer Agent 向け）

`test_sumo_adapter.py` は `sumo_adapter.py` の実装前に書くこと。

テストすべき項目:
1. FCD の正常パース（latitude/longitude/speed/bearing が正しく変換される）
2. time カラムが epoch ナノ秒 int64 であること
3. 7つの CSV ファイルがすべて生成されること
4. ノイズ付与後の GPS 誤差が `GPS_SIGMA_M × 3` 以内であること（3σ）
5. bearing が [0, 360) の範囲に収まること
6. calm / normal / aggressive のスコアが単調減少すること（E2Eレベル）

---

## パイプラインへの接続確認コマンド

```bash
# SUMO FCD → CSV 変換
python src/data_engine/sumo_adapter.py \
  --fcd sumo/fcd/calm_trip.xml \
  --style calm \
  --out data/sumo_calm/

# 既存パイプラインに流す（変更なし）
make data  TRACE=sumo_calm
make fuse  TRACE=sumo_calm FILTER=ekf
make ideal TRACE=sumo_calm
make score TRACE=sumo_calm
make report TRACE=sumo_calm
```

---

## SUMO-GUI 録画設定（T8.10）

```xml
<!-- sumo/cfg/calm.sumocfg に追記 -->
<gui_only>
  <gui-settings-file value="../gui/recording.xml"/>
  <tracker-interval value="0.1"/>
</gui_only>
```

画面録画: Windows Xbox Game Bar（Win + G）または OBS。
推奨解像度: 1920×1080, 30fps, 15秒。

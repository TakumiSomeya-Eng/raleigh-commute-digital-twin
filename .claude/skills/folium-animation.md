# SKILL: Folium Animation (Phase 3 — Video Output B)

このスキルは Impl Agent が T8.7・T8.8 で使用する。
Foliumアニメーション関連のコードを書く前に必ずこのファイルを読むこと。

---

## 目的

`report.html` に TimestampedGeoJson アニメーションを追加し、
車が地図上を時系列で動く様子と急ブレーキ箇所の可視化を実現する。

動画B の素材:
- 実際の軌跡アニメーション（青: 理想, 赤: 実際）
- 急ブレーキ検出箇所が赤く点滅
- 3スタイル比較（calm / normal / aggressive）のスコアパネル

---

## 使用ライブラリ

```python
# requirements.txt に追加（T8.7）
folium>=0.17.0      # 既存（アップグレード確認）
folium[plugins]     # TimestampedGeoJson が plugins に含まれる
```

```python
from folium.plugins import TimestampedGeoJson, AntPath
```

---

## TimestampedGeoJson の基本構造

```python
import folium
from folium.plugins import TimestampedGeoJson

def add_trajectory_animation(
    m: folium.Map,
    fused_df: pd.DataFrame,   # t_s, px_m, py_m, v_mps
    ideal_df: pd.DataFrame,   # t_s, px_m, py_m
    enu_anchor: tuple[float, float],  # (lat0, lon0)
    style: str = "actual",    # "actual" or "ideal"
) -> folium.Map:
    """Add animated trajectory to Folium map."""

    features = []
    for _, row in fused_df.iterrows():
        lat, lon = enu_to_wgs84(row.px_m, row.py_m, *enu_anchor)
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [lon, lat],
            },
            "properties": {
                "time": pd.Timestamp(row.t_s, unit="s").isoformat(),
                "style": {"color": "#EF4444" if style == "actual" else "#3B82F6"},
                "icon": "circle",
                "iconstyle": {
                    "fillColor": "#EF4444" if style == "actual" else "#3B82F6",
                    "fillOpacity": 0.8,
                    "radius": 6,
                },
                "popup": f"v={row.v_mps:.1f} m/s",
            },
        })

    TimestampedGeoJson(
        data={"type": "FeatureCollection", "features": features},
        period="PT0.1S",        # 0.1秒ステップ
        duration="PT0.5S",      # 各点の表示時間
        auto_play=True,
        loop=True,
        max_speed=10,
        loop_button=True,
        time_slider_drag_update=True,
    ).add_to(m)

    return m
```

---

## 急ブレーキ可視化

```python
from folium.plugins import MarkerCluster
import folium

HARSH_BRAKE_THRESHOLD_MPS2 = 3.0  # config/scoring.yaml と同じ値

def add_harsh_brake_markers(
    m: folium.Map,
    fused_df: pd.DataFrame,
    enu_anchor: tuple[float, float],
) -> folium.Map:
    """Red pulsing markers at harsh braking events."""
    # 縦加速度の近似: v の差分
    fused_df = fused_df.copy()
    fused_df["ax"] = fused_df["v_mps"].diff() / fused_df["t_s"].diff()
    harsh = fused_df[fused_df["ax"] < -HARSH_BRAKE_THRESHOLD_MPS2]

    for _, row in harsh.iterrows():
        lat, lon = enu_to_wgs84(row.px_m, row.py_m, *enu_anchor)
        folium.CircleMarker(
            location=[lat, lon],
            radius=10,
            color="#EF4444",
            fill=True,
            fill_color="#EF4444",
            fill_opacity=0.6,
            popup=f"Harsh brake: {row.ax:.1f} m/s²",
            tooltip="⚠️ Harsh brake",
        ).add_to(m)

    return m
```

---

## 3スタイル比較レポート（T8.8）

`src/reporting/compare.py` を新設。

```python
def render_comparison_report(
    styles: list[str],          # ["calm", "normal", "aggressive"]
    score_jsons: list[dict],    # 各スタイルの score.json
    out_path: Path,
) -> Path:
    """Generate side-by-side comparison HTML for 3 driving styles."""
```

出力イメージ:

```
┌─────────────┬──────────────┬──────────────────┐
│    calm     │    normal    │   aggressive     │
│  score: 82  │  score: 64   │   score: 31      │
│  tip: 20%   │  tip: 15%    │   tip: 0%        │
│  🟢 smooth  │  🟡 fair     │   🔴 unsafe      │
│  [map anim] │  [map anim]  │   [map anim]     │
└─────────────┴──────────────┴──────────────────┘
```

---

## スコアカラーマッピング

```python
SCORE_COLORS = {
    (90, 100): {"bg": "#DCFCE7", "text": "#166534", "label": "Excellent"},
    (75,  90): {"bg": "#DBEAFE", "text": "#1E3A8A", "label": "Good"},
    (60,  75): {"bg": "#FEF9C3", "text": "#713F12", "label": "Fair"},
    (45,  60): {"bg": "#FED7AA", "text": "#92400E", "label": "Poor"},
    ( 0,  45): {"bg": "#FEE2E2", "text": "#991B1B", "label": "Unsafe"},
}

def score_color(score: float) -> dict:
    for (lo, hi), style in SCORE_COLORS.items():
        if lo <= score < hi:
            return style
    return SCORE_COLORS[(0, 45)]
```

---

## 録画手順（動画B 用）

1. `src/reporting/compare.py` で比較HTML を生成
2. ブラウザで開く（Chrome 推奨）
3. アニメーションを再生
4. Xbox Game Bar（Win + G）または OBS で録画
5. 推奨: 1920×1080, 30fps, 15秒, MP4形式

---

## テスト規則（Test Writer Agent 向け）

`test_folium_animation.py` でテストすべき項目:

1. `add_trajectory_animation` が folium.Map を返すこと
2. TimestampedGeoJson の features 数が入力の行数と一致すること
3. 急ブレーキマーカーの数が score.json の `harsh_brake_events` と一致すること
4. 比較レポートの HTML に3スタイルすべてのスコアが含まれること
5. スコアカラーが正しいバンドに対応すること（境界値テスト）

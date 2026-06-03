# Phase 3: SUMO + OSM — Synthetic Trip Generation & Video Output

## 概要

**目的**: Raleigh NC実道路上で3種の運転スタイル（calm/normal/aggressive）の
仮想トリップを生成し、既存パイプラインでスコアを出して動画で見せる。

**成果物**:
- 動画A（15秒）: SUMO-GUIで車が走る × パイプラインログ
- 動画B（15秒）: Foliumマップで軌跡アニメーション + スコア比較

**開発スタイル**: TDD（Test Writer Agentが先行）+ Claude Code マルチエージェント

---

## Hypothesis Hierarchy（Phase 3）

### Value Hypothesis ✅
- SUMO仮想トリップでS4検証（Spearman ρ ≥ 0.6）用データを補完できる
- 3スタイルのスコア差が動画で視覚的に伝わる

### Behavior Hypothesis ✅
- `sumo_adapter.py` が FCD → 7CSV を生成する
- 既存パイプライン（`make data/fuse/ideal/score/report`）への変更はゼロ
- 比較レポートHTML + Foliumアニメーションが動画素材になる

### Implementation Hypothesis ✅ （このファイルがそれ）
- SUMO 1.20.0 + netconvert でRaleighネットワーク生成
- FCD出力 → sumo_adapter.py → Sensor Logger CSV形式
- 既存ノイズモデル（noise_fit.py）を流用してIMUノイズを付与

---

## マルチエージェント役割分担

### Orchestrator（このセッション）
- タスクのGoゲート管理
- `docs/LIVING_SPEC.md` の更新
- エージェント間の依存関係管理

### Impl Agent（Claude Code サブエージェント #1）
担当タスク: T8.1, T8.2, T8.3, T8.4, T8.7, T8.8

起動プロンプト:
```
.claude/skills/sumo-osm.md と .claude/skills/folium-animation.md を読め。
Test Writer Agentが書いたテストを通すように実装せよ。
テストを書くな。実装のみ。
```

### Test Writer Agent（Claude Code サブエージェント #2）
担当タスク: T8.5, T8.7のテスト

起動プロンプト:
```
.claude/skills/sumo-osm.md と .claude/skills/pipeline-testing.md を読め。
sumo_adapter.py の実装前にテストを書け（TDD）。
インターフェース仕様: sumo_adapter.py の関数シグネチャのみを参照すること。
実装コードを書くな。テストのみ。
```

### Bug Hunter Agent（Claude Code サブエージェント #3）
担当タスク: T8.9

起動プロンプト:
```
ruff check src/data_engine/sumo_adapter.py src/reporting/compare.py
mypy src/data_engine/sumo_adapter.py src/reporting/compare.py
問題をリストアップして Impl Agent にフィードバックせよ。
コードを修正するな。報告のみ。
```

### QA Orchestrator（Claude Code サブエージェント #4）
担当タスク: T8.6のGoゲート判定

起動プロンプト:
```
pytest tests/unit/test_sumo_adapter.py tests/unit/test_folium_animation.py -v
pytest tests/integration/test_sumo_e2e.py -v
スコアが calm > normal > aggressive の単調減少になっているか確認せよ。
結果をOrchestratorに報告せよ。
```

---

## タスクリスト（依存関係付き）

```
T8.1 ──────────────────────────────────────────▶ T8.2
                                                     │
T8.5（Test Writer: test_sumo_adapter.py先行）         │
  └──────────────────────────────────────────▶ T8.3 ◀─┘
                                                  │
                                               T8.4
                                                  │
                                               T8.9（Bug Hunter）
                                                  │
                                               T8.6（QA: E2E）
                                                  │
                          ┌───────────────────────┘
                          │
T8.7（Foliumアニメーション）  T8.8（比較レポート）
                          │
                       T8.10（録画ガイド）
```

---

## Goゲート定義

| ゲート | 条件 | 判定者 |
|---|---|---|
| T8.3完了 | `test_sumo_adapter.py` 全テスト通過 | QA Agent |
| T8.6完了 | calm > normal > aggressive のスコア単調減少 | QA Agent |
| T8.7完了 | アニメーションが30秒以内にブラウザで再生される | Orchestrator（目視） |
| Phase 3完了 | 動画A + 動画B が15秒で生成できる | Orchestrator（目視） |

---

## 参照スキル

- `.claude/skills/sumo-osm.md` — SUMO/OSM操作・変換規則
- `.claude/skills/folium-animation.md` — Foliumアニメーション実装
- `.claude/skills/pipeline-testing.md` — テスト体系・TDD規則
- `.claude/skills/sensor-fusion.md` — データスキーマ・単位系

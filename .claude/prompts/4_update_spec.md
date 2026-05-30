# Phase 4: Update Specification Based on Validated Learning

> **目的**: 実装（Phase 3）または外部フィードバックから得た新しい知見を
> Living Documentation に反映する。仕様変更は失敗ではなく Validated Learning。

---

## テンプレート（各セッションで記入）

### New Evidence / Validated Learning

```markdown
- **Observation（観察）**: [何が起きたか・何がわかったか]
- **Source（出典）**: [AWSコスト試算 / テスト結果 / ベンチマーク / 自分のUber体験 / etc.]
- **Date**: [YYYY-MM-DD]
```

### 影響を受ける仮説層

```
[ ] Value     — 受け入れ基準の変更 or 別オプションへのピボット
[ ] Behavior  — ユーザーアクションの追加・変更
[ ] Domain    — ビジネスルールの追加・修正
[ ] Interaction — インターフェースの設計変更
[ ] Implementation — インフラ・アーキテクチャの変更
```

### Proposed Spec Updates

```markdown
#### Value（変更あれば）
- 変更前: [元の記述]
- 変更後: [新しい記述]
- 根拠: [Evidence]

#### Implementation（変更あれば）
- 変更前: [元の記述]
- 変更後: [新しい記述]
- 根拠: [Evidence]
```

### Action Plan

```markdown
最小限のコード変更:
1. [変更ファイル]: [内容]
2. [変更ファイル]: [内容]

次のValidated Learningを得るための行動:
- [次のテスト・測定・確認事項]
```

---

## 既知の Validated Learnings（記録済み）

### VL-1: py_ekf.py の精度はC++ EKFと同等（2026-05-23確認済み）

- **Observation**: T3.7でpy_ekf.pyを使ったGPS-primary positionsが deviation raw = 0.790（C++ EKFは0.790で同値）
- **Source**: T3.7の修正ログ（docs/DEV_PLAN.md T3.7参照）
- **Impact**: **Phase 2 MVPでEKSを省略できる根拠** → Implementation仮説「Python-only pipeline」を支持

### VL-2: EKSコントロールプレーンが月$72（コスト支配的）（2026-05-30試算）

- **Observation**: EKSコントロールプレーン $0.10/hr × 720hr = $72/月 > AC-V1($50上限)
- **Source**: AWS価格表（us-east-1, 2026-05）
- **Impact**: EKSを使うと **AC-V1違反**。MVP段階ではEKSを省略し py_ekf.py を使う

### VL-3: Phase 1のday2スコアは34.8/100（harsh-brake 17回）（実測値）

- **Observation**: report.htmlより。harsh-brake events: 17 detected (≥ 3.0 m/s²)
- **Source**: docs/screenshots/report_day2.html
- **Impact**: Phase 2でも同じスコアが出ることがAC-MVP-3の基準値（許容誤差±2）

---

## エージェントへの指示（QA Agent）

このプロンプトを使うとき:

1. 上記テンプレートを記入して、どの仮説層が影響を受けるかを明示せよ
2. `docs/LIVING_SPEC.md` の該当セクションを更新せよ
3. 変更に伴う最小限のコード修正を提案せよ（大きな設計変更は新しい仮説サイクルとして扱う）
4. 「次のValidated Learningを得るための行動」を必ず記述せよ（次のステップを常に明確に）
5. VL番号を採番して「既知のValidated Learnings」に追記せよ

---

**使用タイミング**: 実装完了後・テスト結果確認後・コスト試算後・Uber乗車データ追加後

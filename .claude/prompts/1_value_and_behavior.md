# Phase 1: Value & Behavior Hypotheses — AWS Deployment (Phase 2)

> **前提**: このプロンプトを使う前に `0_hypothesis_framework.md` を読んでいること。
> Implementationの話は一切しない。技術スタックの話はまだしない。

---

## 1. Value Hypothesis

### The Claim（主張）

**Phase 1のローカルパイプラインをAWS化することで、
複数トリップの継続的・自動的な処理・蓄積が可能になり、
PRD Success Criterion S4（Spearman ρ ≥ 0.6）の検証が現実的な労力で達成できる。**

### なぜローカルだけでは不十分か（Evidence）

| 制約 | ローカル | AWS化後 |
|---|---|---|
| トリップ追加のコスト | `make score TRACE=newtrip` を手動実行 | S3アップロードのみ |
| 処理の継続性 | ラップトップが起動中のみ | いつでも非同期処理 |
| S4検証に必要なn数 | 8トリップ × 手動15分 = 2時間 | 8トリップ × 自動 ≈ 0分 |
| Phase 1.5（データ収集期）のフリクション | 高い | 低い |

### Acceptance Criterion（反証可能）

- **AC-V1**: 月$50以下のAWS費用で動作すること（ハード上限）
- **AC-V2**: 新しいトリップの処理に必要なユーザー操作が「ファイルアップロードのみ」であること
- **AC-V3**: 8トリップのバッチ処理が手動操作なしで完了すること
- **AC-V4**: Phase 1と同じ `score.json` が出力されること（ローカル再現性 = PRD S3）

### Options（最低2つ）

| オプション | 概要 | 主なトレードオフ |
|---|---|---|
| **Option A: フルAWS** | EKS + Step Functions + Fargate（FRD FR-12のデフォルト） | 高機能・高複雑・コスト変動大 |
| **Option B: ミニマルAWS** | Lambda + S3 trigger のみ（EKSなし、Python-onlyで処理） | 低複雑・スケール制限・C++/ROS2が使えない |
| **Option C: スケジュール実行** | EC2 spot + cron（EKS/Fargate不使用） | 最シンプル・コスト予測可能・スケール不可 |

### 初期仮説

> ROS 2 (C++) の EKF/UKF ノードが必要なため、Option B（Lambda only）は除外できない可能性がある。
> ただし、Phase 1でPython-only EKFバックアップ（`scripts/py_ekf.py`）が既に存在する。
> **Value仮説を検証してからアーキテクチャを確定すること。**

---

## 2. Behavior Hypothesis

### User Action（ユーザーが価値を実現するまでの行動）

```
1. Uber乗車中: Sensor Logger を起動（現行と変わらない）
2. 乗車後: エクスポートしたCSVフォルダをS3にアップロード
3. 15分後: スマホのメールまたはS3コンソールでreport.htmlが届く
4. チップ決定: score.jsonとreport.htmlを見てチップを決める（手動）
```

### Desired Interaction Model

```
"アップロードするだけ → あとは自動"

入力:  S3 /raw/{trip_id}/ への CSV ファイル群のアップロード
処理:  自動（ユーザー関与なし）
出力:  S3 /reports/{trip_id}/report.html + /scores/{trip_id}/score.json
通知:  （オプション）SES or SNS でメール通知
```

### 検証すべき前提

- **BA-1**: ユーザー（自分）は乗車後にCSVを即座にアップロードするか？
  → *Evidence*: Phase 1では都度手動実行していたため、アップロードの習慣は想定可能
- **BA-2**: 処理が15分以内に終わることが体験として重要か？
  → *Evidence*: Phase 1のdocker-compose run < 30min。AWSでの目標は < 15min（FRD FR-12.4）
- **BA-3**: スマホからS3を操作するUIが必要か？
  → *Evidence*: Phase 1スコープ外（PRD §1.4 Non-goals参照）。CLIアップロードで十分

---

## エージェントへの指示

このプロンプトを受け取ったら:

1. 上記のValue仮説とBehavior仮説を分析せよ
2. 各Acceptance Criterionが「反証可能か」を確認せよ
3. 前提（BA-1〜BA-3）に疑問があれば質問せよ
4. **絶対にやらないこと**: Domain/Interaction/Implementationの話をしない
5. 承認されたら `docs/LIVING_SPEC.md` のValue/Behaviorセクションを更新せよ
6. 次は `.claude/prompts/2_domain_and_interaction.md` へ進むこと

---

**現在の検証状態**: 🔲 未検証（このセッションで検証中）

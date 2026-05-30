# Phase 2: Domain & Interaction Hypotheses — AWS Deployment

> **前提**: `1_value_and_behavior.md` のValue/Behavior仮説が承認済みであること。
> ここではまだ「どう実装するか」は話さない。「何であるべきか」を定義する。

---

## Recaps of Validated Hypotheses

- **Value**: 複数トリップの自動処理により、PRD S4（Spearman ρ）検証を現実的な労力で達成する
  - AC-V1: 月$50以下
  - AC-V2: ユーザー操作 = "ファイルアップロードのみ"
  - AC-V3: 8トリップのバッチ処理が手動操作なしで完了
  - AC-V4: Phase 1と同一の `score.json` が出力される
- **Behavior**: S3アップロード → 自動処理 → report.html + score.json 配信

---

## 1. Domain Hypothesis

### Domain Entities（コアオブジェクト）

```
Trip（トリップ）
  ├── trip_id: str              # "day2", "uber_2026-06-01", etc.
  ├── raw_prefix: S3Path        # s3://{bucket}/raw/{trip_id}/
  ├── status: TripStatus        # PENDING | PROCESSING | SCORED | FAILED
  ├── score: Optional[Score]
  └── created_at: datetime

Score（スコア）
  ├── aggregate_0_100: float
  ├── components: dict[str, float]  # 6コンポーネント
  ├── tip_suggestion_pct: int
  ├── config_hash: str
  └── scored_at: datetime

Pipeline（パイプライン）
  ├── stages: list[Stage]       # ingest → fuse → ideal → score → report
  ├── current_stage: Stage
  ├── started_at: datetime
  └── duration_s: Optional[float]
```

### Business Rules（変更不可のドメインルール）

| ルール | 理由 |
|---|---|
| **BR-1**: `score.json` は Phase 1と同一スキーマ（TRD §1.8）でなければならない | ローカルとクラウドの結果を比較可能にする（PRD S3） |
| **BR-2**: 処理中のトリップは上書きを受け付けない | 部分的な上書きによるデータ破損を防ぐ |
| **BR-3**: RawファイルはImmutable（S3 versioning） | 監査可能性・再処理可能性 |
| **BR-4**: コスト上限$50/monthを超えた場合、新規処理を停止する | AC-V1の強制 |
| **BR-5**: スコアリング結果には必ず `config_hash` が含まれる | どのscoringパラメータで算出したかを追跡（TRD §1.8） |

### Privacy & Data Rules

| データ | 扱い |
|---|---|
| GPS座標 | プライベートS3バケット（パブリックアクセス完全ブロック） |
| トリップ情報 | 外部サービスへの送信禁止（PRD §1.4 Non-goals） |
| レポートHTML | 認証なしURLでの公開禁止 |
| AWSクレデンシャル | OIDC経由のみ。ハードコード・GitHub Secrets禁止 |

---

## 2. Interaction Hypothesis

### The Solution（どのインターフェースがBehavior仮説を実現するか）

```
S3 PutObject Event → EventBridge → Step Functions 自動起動
```

**代替案との比較:**

| 方式 | トリガー | UX | 複雑度 |
|---|---|---|---|
| **A: S3 Event + EventBridge** | 自動 | アップロードのみ | 中 |
| B: SQS ポーリング | 手動キュー投入 | CLIコマンド必要 | 低 |
| C: API Gateway | HTTP POST | URLをコールする必要 | 高 |
| D: 手動 Step Functions 起動 | コンソール/CLI | 操作増える | 最低 |

→ **Option A を仮説として採用**（AC-V2「アップロードのみ」に最も合致）

### Workflow Mapping（Behavior → Domain → Interaction の対応）

```
ユーザー操作: aws s3 cp ./day3/ s3://rct-data/raw/day3/ --recursive
                              ↓ S3 PutObject イベント
EventBridge Rule: prefix = "raw/" かつ ファイル数 = 7（全センサーCSV）
                              ↓ Step Functions ExecutionStarted
State Machine:
  [Ingest]  → Fargate task: CSV → Parquet   (FR-12.1, FR-1)
  [Fuse]    → EKS job: Parquet → fused.parquet  (FR-12.3, FR-4)
  [Ideal]   → Fargate task: Valhalla map-match  (FR-9)
  [Score]   → Fargate task: score.json  (FR-10)
  [Report]  → Fargate task: report.html  (FR-11)
  [Notify]  → SNS: メール通知（オプション）
ユーザー受取: s3://rct-data/reports/day3/report.html
```

### Open Questions（Interaction層で未決定）

- **OQ-1**: 全7ファイルのアップロード完了をどう検出するか？
  → EventBridgeは1ファイルごとに起動するため、全ファイル揃うまで待つ仕組みが必要
  → 候補: Step Functions内でS3 ListObjects → ファイル数チェック → 不足なら Wait+Retry
- **OQ-2**: 処理失敗時のリトライポリシーをどうするか？
  → Step Functionsの標準リトライ（指数バックオフ、最大3回）で十分か？

---

## エージェントへの指示

1. Domain Entities とBusiness Rulesに矛盾がないか確認せよ
2. Interaction Hypothesisが本当にBehavior仮説（AC-V2）を満たしているか検証せよ
3. OQ-1とOQ-2の回答方針を提案せよ
4. **絶対にやらないこと**: Terraformコードを書かない、具体的なAWSリソース設定を決めない
5. 承認されたら `docs/LIVING_SPEC.md` のDomain/Interactionセクションを更新せよ
6. 次は `.claude/prompts/3_implementation_minimal.md` へ進むこと

---

**現在の検証状態**: 🔲 未検証（Value/Behavior承認後に使用）

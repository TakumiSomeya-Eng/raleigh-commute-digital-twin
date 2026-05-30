# Phase 0: Hypothesis-Driven Development Framework

## このプロジェクトにおける位置づけ

Raleigh Commute Digital Twin の **Phase 2（AWSデプロイ）** は、
「技術的に可能だから作る」に陥りやすいフェーズ。
EKS, Step Functions, Fargate — どれも面白いが、
**なぜそのアーキテクチャ選択か** の根拠が薄いまま走ることが最大のリスク。

このフレームワークはその暴走を防ぐための **仮説ファースト開発プロセス**。

---

## Hypothesis Hierarchy Model（5層）

以下の順序を**厳守**する。下位層は上位層が検証されるまで着手禁止。

```
1. Value Hypothesis
   └─ 2. Behavior Hypothesis
         └─ 3. Domain Hypothesis
               └─ 4. Interaction Hypothesis
                     └─ 5. Implementation Hypothesis
```

### 各層の定義

| 層 | 問い | Phase 2 での例 |
|---|---|---|
| **Value** | それを作ることで何の問題が解決するか？ | 「複数トリップを自動処理して蓄積できると、Spearman ρ検証（S4）が現実的になる」 |
| **Behavior** | ユーザーはどうやってその価値を実現するか？ | 「乗車後、アプリを開かずにスコアがS3バケットに自動で届く」 |
| **Domain** | どんなビジネスルールとデータが必要か？ | 「トリップID・スコア・生Parquet・レポートHTMLが紐づいて保存される」 |
| **Interaction** | どのUIまたはインターフェースがその動作を実現するか？ | 「S3 upload eventが自動でStep Functionsをトリガーする」 |
| **Implementation** | どの技術的構成が最も要件を満たすか？ | 「Fargate vs Lambda vs EKS の比較・選択」 |

---

## 基本ルール

1. **階層の厳守**: Implementationを作る前に上位4層が明文化されていること
2. **根拠の明示**: すべての決定に Evidence（根拠）を添えること。直感・ベンチマーク・コスト試算・競合調査、どれでも可
3. **反証可能な仕様**: すべての受け入れ基準は「合格/不合格が数値で判断できる」形で書くこと
4. **学習の記録**: 仕様変更は失敗ではなく Validated Learning。`docs/LIVING_SPEC.md` に必ず記録する

---

## エージェントへの制約

このプロンプトは **Orchestrator Agent** が最初に読むもの。

```
実装コードを生成する前に、必ず確認すること：
1. Value仮説が docs/LIVING_SPEC.md に記録されているか？
2. Behavior仮説が承認されているか？
3. Domain/Interaction仮説が明文化されているか？

いずれかが欠けている場合：
- コード生成を行わない
- .claude/prompts/1_value_and_behavior.md を参照し
  仮説定義のセッションを先に開始するよう促す
```

---

## Phase 2での適用例

### 現在の状態（仮説検証前）

```
❌ 「EKSを使ってPhase 1パイプラインをAWS化する」（実装仮説だけある）
✅ 「複数トリップを月$50以下で自動処理することでS4検証が可能になる」（Value仮説から始まる）
```

### 正しいアプローチ

```
Step 1: Value仮説 → なぜAWSが必要か？ローカルじゃダメな理由は？
Step 2: Behavior仮説 → ユーザー（自分）はどう使うか？
Step 3: Domain仮説 → 必要なデータ構造とビジネスルールは？
Step 4: Interaction仮説 → S3 event? API? Scheduled? どれが自然か？
Step 5: Implementation仮説 → EKS vs Fargate vs Lambda の比較
```

---

**確認**: このフレームワークと5層の階層を理解した。
Phase 2のValue仮説定義から始めるには `.claude/prompts/1_value_and_behavior.md` を参照すること。

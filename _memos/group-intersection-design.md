# group 権限の交差(診療科 × 職位)設計(issue #4)

fork.md §3.3 の二軸権限モデルを v0.6.5 の access control 機構の
**後方互換拡張**として実装する。

## 要件(fork.md §3.3)

- 横軸: 診療科(内科 / 外科 / 看護部 …)、縦軸: 職位(部長 / 一般 / 研修医 …)
- ユーザーは複数 group に所属可能
- 「内科 **かつ** 部長」にだけ特定スキルを見せる交差割り当てを表現できること
- 運用主体は事務(GUI から割り当て) — UI は後続タスク

## 設計

### group の持ち方(命名規則)

交差 group(「内科部長」)は作らない。軸ごとに独立した group を作る:

- 診療科: `dept:内科`, `dept:外科`, `dept:看護部` …
- 職位: `rank:部長`, `rank:一般`, `rank:研修医` …

命名は運用規約であり、システムは prefix を解釈しない(どの group id でも
交差条件に使える)。事務向けには「dept:/rank: の 2 軸で作る」を推奨手順とする。

### access_control スキーマ拡張

既存(v0.6.5、OR のみ):

```json
{
  "read":  { "group_ids": ["g1", "g2"], "user_ids": ["u1"] },
  "write": { "group_ids": [], "user_ids": [] }
}
```

llens 拡張 — `group_terms`(DNF: 項内 AND・項間 OR)を追加:

```json
{
  "read": {
    "group_ids": [],
    "user_ids": [],
    "group_terms": [
      ["<dept:内科の group id>", "<rank:部長の group id>"],
      ["<dept:外科の group id>", "<rank:部長の group id>"]
    ]
  }
}
```

- 判定: ユーザーの所属 group 集合が term の全要素を含む(⊇)term が
  ひとつでもあれば許可。
- `group_ids` / `user_ids` は従来通り OR で併用可(完全後方互換。
  既存データは無変更で動く)。
- 空 term(`[]`)は不成立扱い(誤設定で全開放にならないための安全側)。

### 実装箇所

- `utils/access_control.py`
  - `has_access()`: `group_terms` の判定を追加
  - `get_users_with_access()`: term は「全所属者の積集合」で展開
- 判定はモデル・プロンプト・ツール・ナレッジ等、`has_access` を使う
  全リソースで自動的に有効になる。
- 回帰テスト: `nucllei/test/apps/webui/utils/test_access_control.py`

## 後続タスク(このメモの範囲外)

- [ ] 管理 UI: group×tool 割り当て画面で交差条件を編集できるようにする
      (現行 UI は group_ids の OR 選択のみ)
- [ ] Skill データモデル(issue #5)と統合し、スキル可視性を交差で制御
- [ ] 事務向け運用手順書(dept:/rank: 命名規約、割り当てフロー)

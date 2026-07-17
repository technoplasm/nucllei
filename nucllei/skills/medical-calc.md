---
name: 臨床計算
description: "医学計算・重症度スコアを計算式の暗算なしで正確に出すためのスキル。利用者が eGFR / CCr / MELD-Na / ALBI / FIB-4 / APACHE II / SOFA / Glasgow-Blatchford / Ranson / DIC スコア / 血液ガス解析 / 自由水欠乏量 / カルボプラチン投与量 (Calvert) 等の計算・スコアリングに言及した時に使う。数式を自前で計算せず、必ず本スキルをロードして medical_calculator ツールで計算すること。"
tool_ids: ["medical_calculator"]
---

# 臨床計算 (medical_calculator)

## 大原則

1. **計算・スコアリングを暗算や自前の式でやらない。** 必ず `medical_calculator` の該当関数を呼び、返ってきた値・判定をそのまま使う。
2. **不足パラメータは推測せず利用者に確認する。** 単位 (mg/dL / µmol/L 等) の取り違えに注意し、関数の引数仕様に合わせて渡す。
3. 結果は「値 + 判定区分 + 簡単な解釈」で提示する。ツールの返す interpretation / grade がある場合はそれを優先する。

## 使える計算

| 関数 | 用途 |
| --- | --- |
| `calc_egfr_ckdepi2021` / `calc_egfr_jsn` | eGFR (CKD-EPI 2021 / 日本腎臓学会式) |
| `calc_ccr_cockcroft_gault` | クレアチニンクリアランス (Cockcroft-Gault) |
| `calc_meld_na` | MELD-Na (肝移植優先度) |
| `calc_albi_grade` | ALBI grade (肝予備能) |
| `calc_fib4` | FIB-4 index (肝線維化) |
| `calc_apache2` | APACHE II (ICU 重症度) |
| `calc_sofa` | SOFA スコア (臓器障害) |
| `calc_glasgow_blatchford` | Glasgow-Blatchford (上部消化管出血) |
| `calc_ranson_criteria` | Ranson 基準 (急性膵炎) |
| `calc_dic_score` | DIC スコア |
| `calc_acid_base_analysis` | 血液ガス・酸塩基平衡解析 |
| `calc_free_water_deficit` | 自由水欠乏量 (高 Na 血症) |
| `calc_calvert_carboplatin` | カルボプラチン投与量 (Calvert 式) |

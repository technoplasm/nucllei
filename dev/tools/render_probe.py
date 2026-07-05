"""
title: Render Probe
author: llens dev
version: 0.1
description: レンダリング/tool call 表示のデバッグ用。任意の markdown 表示ケースをモデル経由で再現する開発ツール。air-gapped 前提で外部アクセスなしの純粋関数のみ。
requirements:
"""

import json
from typing import List


# =========================================================================
# サンプルビルダ(モジュール関数)
# 注: tool ローダは class Tools の「dunder でないメソッド」をすべて tool spec に
#     変換する(単一アンダースコアの private メソッドも露出する)。サンプル生成の
#     ヘルパーが tool として現れないよう、メソッドではなくモジュール関数に置く。
# =========================================================================


def _sample_table() -> str:
    small = (
        "小さい表:\n\n"
        "| 項目 | 値 |\n"
        "| --- | --- |\n"
        "| pH | 7.35 |\n"
        "| PaCO2 | 45 mmHg |\n"
        "| HCO3⁻ | 24 mEq/L |\n"
    )
    # 大きめの表(列数・行数多め、右寄せ/中央寄せ混在)
    header = "| # | 薬剤 | 用量 | 経路 | 頻度 | 備考(長め) |\n"
    align = "| ---: | :--- | ---: | :---: | :--- | :--- |\n"
    rows = ""
    drugs = [
        ("アセトアミノフェン", "500mg", "PO", "q6h", "肝機能に注意"),
        ("ロキソプロフェン", "60mg", "PO", "tid", "腎機能・胃粘膜"),
        ("セフトリアキソン", "1g", "IV", "q24h", "胆泥形成の報告あり"),
        ("バンコマイシン", "1g", "IV", "q12h", "TDM 対象・トラフ管理"),
        ("フロセミド", "20mg", "IV", "bid", "電解質モニタ"),
        ("ノルアドレナリン", "0.05γ", "CV", "持続", "血圧に応じ漸増"),
    ]
    for i, (name, dose, route, freq, note) in enumerate(drugs, 1):
        rows += f"| {i} | {name} | {dose} | {route} | {freq} | {note} |\n"
    big = "大きい表(整列混在):\n\n" + header + align + rows
    return "## 表のレンダリング確認\n\n" + small + "\n" + big


def _sample_code() -> str:
    py = (
        "```python\n"
        "# 日本語コメント: 特殊文字 <>&\"' を含む\n"
        "def greet(name: str) -> str:\n"
        '    return f"こんにちは、{name}さん！ 記号: <tag> & \\"quote\\""\n'
        "```"
    )
    js = (
        "```javascript\n"
        "// バッククォート内テンプレート\n"
        "const msg = `合計: ${1 + 2} 円`; // コメント\n"
        "console.log(msg);\n"
        "```"
    )
    sh = (
        "```bash\n"
        "# パイプとリダイレクト\n"
        'grep -n "エラー" log.txt | awk \'{print $1}\' > out.txt\n'
        "```"
    )
    inline = "インラインコード: `printf(\"%d\\n\", x);` と `<div class=\"x\">`。"
    return "## コードフェンスのレンダリング確認\n\n" + inline + "\n\n" + py + "\n\n" + js + "\n\n" + sh


def _sample_math() -> str:
    return (
        "## 数式(KaTeX)のレンダリング確認\n\n"
        "インライン数式: 質量とエネルギーの関係は $E = mc^2$ で表される。"
        "また補正Caは $Ca_{corr} = Ca + 0.8\\,(4 - Alb)$。\n\n"
        "ブロック数式(二次方程式の解):\n\n"
        "$$x = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}$$\n\n"
        "ブロック数式(総和と分数):\n\n"
        "$$\\sum_{i=1}^{n} \\frac{1}{i^2} = \\frac{\\pi^2}{6} \\quad (n \\to \\infty)$$\n"
    )


def _sample_long() -> str:
    return (
        "# 長文サンプル(見出し・リスト・引用)\n\n"
        "## はじめに\n\n"
        "これは長文レンダリングの確認用テキストです。段落が複数あり、"
        "見出し階層とリスト・引用が混在します。\n\n"
        "### 箇条書き\n\n"
        "- 第1項目\n"
        "- 第2項目\n"
        "  - ネストした項目 A\n"
        "  - ネストした項目 B\n"
        "- 第3項目\n\n"
        "### 番号付きリスト\n\n"
        "1. 手順その1\n"
        "2. 手順その2\n"
        "3. 手順その3\n\n"
        "### 引用\n\n"
        "> これは引用ブロックです。\n"
        "> 複数行にわたる引用も確認します。\n"
        ">\n"
        "> — 出典(架空)\n\n"
        "### まとめ\n\n"
        "段落・強調(**太字** と *斜体*)・[リンク風テキスト](https://example.invalid) も含みます。\n"
    )


def _sample_mixed() -> str:
    return (
        "# 全部入りサンプル\n\n"
        + _sample_long()
        + "\n\n---\n\n"
        + _sample_table()
        + "\n\n---\n\n"
        + _sample_code()
        + "\n\n---\n\n"
        + _sample_math()
    )


def _sample_edge() -> str:
    long_line = "超長い1行: " + "あ" * 200 + " END"
    return (
        "# エッジケース\n\n"
        "未クローズのコードフェンス(以降が全部コード扱いになるか確認):\n\n"
        "```python\n"
        "x = 1  # このフェンスは閉じない\n"
        "y = 2\n\n"
        "HTML タグ混入(サニタイズ確認): <script>alert('xss')</script> と <b>bold?</b>。\n\n"
        "reasoning 誤検出確認: <think>これは思考タグ風の文字列であって本物の reasoning ではない</think>\n\n"
        "絵文字: 🩺💊🧪🚑✅❌ と結合絵文字 👨‍⚕️👩‍⚕️。\n\n"
        + long_line
        + "\n\n"
        "特殊文字: <>&\"'` \\ \t (タブ) と全角空白　です。\n"
    )


_BUILDERS = {
    "table": _sample_table,
    "code": _sample_code,
    "math": _sample_math,
    "long": _sample_long,
    "mixed": _sample_mixed,
    "edge": _sample_edge,
}


class Tools:
    """レンダリング刷新(reasoning 構造化・tool call 引数の構造化表示・markdown 移行)の
    動作確認に使う開発用ツール群。

    すべて外部ネットワーク・ファイルアクセスをしない純粋関数。返り値は文字列(markdown)。
    本番運用には載せず、dev DB でのみ登録して使う。
    """

    def __init__(self) -> None:
        pass

    def render_probe(self, kind: str = "mixed") -> str:
        """指定した種別の markdown サンプルを返す。返ってきた文字列をそのままチャットに表示させ、
        レンダリング結果(表・コード・数式・長文・エッジケース)を目視確認する用途。

        :param kind: サンプル種別。次のいずれか:
            "table"(大小2種の表)、
            "code"(複数言語のコードフェンス、日本語コメント・特殊文字入り)、
            "math"(インライン/ブロックの KaTeX)、
            "long"(見出し・リスト・引用を含む長文)、
            "mixed"(上記すべて入り)、
            "edge"(未クローズのフェンス・HTMLタグ混入・<think>風文字列・絵文字・超長い1行)。
            未知の値は "mixed" にフォールバックする。
        :return: 種別に応じた markdown 文字列
        """
        k = (kind or "").strip().lower()
        builder = _BUILDERS.get(k)
        if builder is None:
            return (
                f"> [render_probe] 未知の kind `{kind}` のため mixed にフォールバックしました。\n\n"
                + _sample_mixed()
            )
        return builder()

    def echo(self, text: str = "") -> str:
        """受け取った文字列をそのまま返す。tool call の引数レンダリング(引数フレームの表示)確認用。

        :param text: エコーバックする任意の文字列。改行・引用符・日本語・絵文字などをそのまま返す。
        :return: 受け取った text をそのまま返す
        """
        return text

    def probe_args(
        self,
        code: str = "",
        items: List[str] = [],
        count: int = 0,
    ) -> str:
        """受け取った引数を JSON に整形して返すだけの関数。複数引数・コード引数・特殊文字引数が
        tool call フレームでどう表示されるかの確認用。

        :param code: 任意のコード片(改行・引用符・バックスラッシュなどを含めてよい)
        :param items: 文字列のリスト
        :param count: 整数
        :return: 受け取った引数を整形した JSON 文字列(markdown コードフェンスで囲む)
        """
        payload = {
            "code": code,
            "items": list(items) if items is not None else [],
            "count": count,
        }
        pretty = json.dumps(payload, ensure_ascii=False, indent=2)
        return "受け取った引数:\n\n```json\n" + pretty + "\n```"

"""SSE デルタ → content_blocks の組み立て状態機械(issue #23)。

_memos/rendering-design.md §3 の「#23 同梱判断」を実施したもの:
ブロック組み立て(デルタ→content_blocks + reasoning の閉じ規律)を
独立モジュール化し、filter 適用 / tool 実行ループ / 永続化・イベント emit は
オーケストレーション層(utils/middleware.py)に残す。

このモジュールは I/O を持たない(イベント emit・DB 書き込みは呼び出し側)。
純粋な状態遷移なので単体テストで固められる(test_response_assembler.py)。

ライフサイクル: ターン(1 応答メッセージ)につき 1 個生成し、tool ループの
各ストリームで start_stream() → handle_delta() × N → finish_stream() を繰り返す。

ブロックの内部表現は v0.6.5 由来の形をそのまま核とする(#17 L0 判断)。
wire / 永続化形への変換は utils/content_blocks.py が担う。
"""

import time

# handle_delta が返す変更種別。emit の扱いが呼び出し側で種別ごとに異なる。
CHANGED_TOOL_CALLS = "tool_calls"
CHANGED_REASONING = "reasoning"
CHANGED_TEXT = "text"

# finish_stream が返す不整合種別(llens #18-2)
MISMATCH_MISSING_TOOL_CALLS = "missing_tool_calls"  # 呼び出し側でチャット内エラー化
MISMATCH_UNEXPECTED_FINISH = "unexpected_finish"  # ログ観測に留める


class ContentBlocksAssembler:
    """デルタを content_blocks に畳み込むターンスコープの状態機械。

    - content: text デルタの素朴な累積(webhook 本文・resume 初期値用の派生値)
    - content_blocks: 組み立て中の内部ブロック列(wire 形ではない)
    """

    def __init__(self, initial_content="", seed_blocks=None, tools=None):
        self.content = initial_content
        self.content_blocks = list(seed_blocks or [])
        self.content_blocks.append({"type": "text", "content": initial_content})
        # 表示ディスクリプタ(render)の解決元。metadata["tools"] と同一オブジェクトを
        # 受け取ることで、ストリーム中の tool 遅延追加(load_skill)も見える。
        self._tools = tools if tools is not None else {}
        self._response_tool_calls = []
        self._finish_reason = None
        self.last_frame = {"structural": False, "appends": []}

    # --- ストリーム境界 -------------------------------------------------

    def start_stream(self):
        """ストリーム(モデル呼び出し 1 回)スコープの状態をリセットする。"""
        self._response_tool_calls = []
        self._finish_reason = None
        self.last_frame = {"structural": False, "appends": []}

    def note_finish_reason(self, finish_reason):
        if finish_reason:
            self._finish_reason = finish_reason

    @property
    def finish_reason(self):
        return self._finish_reason

    def finish_stream(self):
        """ストリーム終端処理。(tool_calls, mismatch) を返す。

        - reasoning の閉じ規律③(ストリーム終端 = finish / 切断 / エラー)を適用
        - reasoning の昇格規律(下記)を適用
        - 末尾 text ブロックを strip し、空なら落とす(全滅時は空 text を 1 個残す)
        - mismatch(llens #18-2): finish_reason とパース結果の整合ガード。
          MISMATCH_MISSING_TOOL_CALLS は「tool_calls 終了なのに 1 つも組めて
          いない」= チャンク境界取りこぼしの無音失敗を可視化するためのもの。
        """
        self.close_open_reasoning_block()
        self._promote_orphan_reasoning()

        if self.content_blocks:
            if self.content_blocks[-1]["type"] == "text":
                self.content_blocks[-1]["content"] = self.content_blocks[-1][
                    "content"
                ].strip()

                if not self.content_blocks[-1]["content"]:
                    self.content_blocks.pop()

                    if not self.content_blocks:
                        self.content_blocks.append({"type": "text", "content": ""})

        mismatch = None
        if self._finish_reason == "tool_calls" and not self._response_tool_calls:
            mismatch = MISMATCH_MISSING_TOOL_CALLS
        elif self._response_tool_calls and self._finish_reason not in (
            "tool_calls",
            None,
        ):
            mismatch = MISMATCH_UNEXPECTED_FINISH

        return self._response_tool_calls, mismatch

    # --- デルタ処理 -----------------------------------------------------

    def handle_delta(self, delta) -> set:
        """1 フレーム分の delta を畳み込み、変更種別の集合を返す(空 = 変更なし)。

        同時に last_frame へこのフレームの詳細を残す(wire v2):
        - structural: ブロックの開閉・tool_calls 累積を伴った(= スナップショット必須)
        - appends: 既存の末尾ブロックへの純追記 [{"block": "text"|"reasoning", "content": str}]
          (structural=False のとき、呼び出し側はこれをデルタイベントとして emit できる)
        """
        changed = set()
        structural = False
        appends = []

        delta_tool_calls = delta.get("tool_calls", None)
        if delta_tool_calls:
            # reasoning の閉じ規律②: tool_calls デルタは非 reasoning シグナル
            self.close_open_reasoning_block()
            self._accumulate_tool_call_deltas(delta_tool_calls)

            # llens(#20 ステップ5): 蓄積中の tool_calls を open ブロックとして載せ、
            # 部分 arguments 込みの snapshot を毎フレーム emit できるようにする
            # (フロントは寛容パーサで逐次描画)。ブロックの content は
            # _response_tool_calls と同一オブジェクトなので以後の累積は自動反映。
            self._open_tool_calls_block(self._response_tool_calls)
            changed.add(CHANGED_TOOL_CALLS)
            # tool_calls は部分 arguments の突き合わせが要るためスナップショット一択
            structural = True

        reasoning_content = delta.get("reasoning_content") or delta.get("reasoning")
        if reasoning_content:
            if (
                not self.content_blocks
                or self.content_blocks[-1]["type"] != "reasoning"
            ):
                reasoning_block = {
                    "type": "reasoning",
                    "start_tag": "think",
                    "end_tag": "/think",
                    "attributes": {"type": "reasoning_content"},
                    "content": "",
                    # 経過の計測なので monotonic(時刻表示には使わない)
                    "started_at": time.monotonic(),
                }
                self.content_blocks.append(reasoning_block)
                structural = True
            else:
                reasoning_block = self.content_blocks[-1]
                appends.append({"block": "reasoning", "content": reasoning_content})

            reasoning_block["content"] += reasoning_content
            changed.add(CHANGED_REASONING)

        value = delta.get("content")
        if value:
            # reasoning の閉じ規律①: content デルタの到着で reasoning を確定する
            if (
                self.content_blocks
                and self.content_blocks[-1]["type"] == "reasoning"
                and self.content_blocks[-1].get("attributes", {}).get("type")
                == "reasoning_content"
            ):
                reasoning_block = self.content_blocks[-1]
                reasoning_block["ended_at"] = time.monotonic()
                # llens(#100): int() 切り捨てだと 1 秒未満の思考が全部「0秒」に
                # なっていた。小数で保持し、丸めは表示側の責務にする。
                reasoning_block["duration"] = round(
                    reasoning_block["ended_at"] - reasoning_block["started_at"], 3
                )

                self.content_blocks.append({"type": "text", "content": ""})
                structural = True

            self.content = f"{self.content}{value}"
            # llens(#20 ステップ5): tool_calls ブロックがストリーム中から存在する
            # ため、直後に content デルタが来たら新しい text ブロックを開く
            if not self.content_blocks or self.content_blocks[-1]["type"] not in (
                "text",
                "reasoning",
            ):
                self.content_blocks.append({"type": "text", "content": ""})
                structural = True

            if not structural:
                appends.append({"block": "text", "content": value})

            self.content_blocks[-1]["content"] = (
                self.content_blocks[-1]["content"] + value
            )
            changed.add(CHANGED_TEXT)

        self.last_frame = {"structural": structural, "appends": appends}
        return changed

    # --- tool ループ側の操作 ---------------------------------------------

    def ensure_tool_calls_block(self, calls):
        """calls のブロックを末尾の tool_calls ブロックとして保証する(冪等)。

        tool ループが実行直前に呼ぶ。通常はストリーム中に open ブロック化済み
        (content が同一オブジェクト)で、何もしない。同じストリームで calls の後に
        text / reasoning が積まれていたら(tool call デルタの後に content デルタを
        出すモデル)、ブロックを末尾へ移す — set_tool_results が末尾ブロックに
        結果を綴じるためと、モデル向け履歴で「content + tool_calls の 1 assistant
        メッセージ → tool 結果」の形を保つため。
        """
        self._attach_tool_renders(calls)
        index = self._find_tool_calls_block(calls)
        if index is None:
            self.content_blocks.append({"type": "tool_calls", "content": calls})
        elif index != len(self.content_blocks) - 1:
            self.content_blocks.append(self.content_blocks.pop(index))

    def set_tool_results(self, results):
        """最後の tool_calls ブロックに実行結果を綴じ、次の text ブロックを開く。"""
        self.content_blocks[-1]["results"] = results
        self.content_blocks.append({"type": "text", "content": ""})

    def _promote_orphan_reasoning(self):
        """reasoning の昇格規律(llens):

        ストリームが正常終了(finish_reason=stop)したのに、可視テキストも
        tool_calls も無く reasoning だけで終わった場合、その reasoning を本文
        (text ブロック)へ昇格させる。Kimi(SGLang reasoning parser)は tool
        結果後の追撃応答で最終回答を丸ごと reasoning_content に載せ、content
        空のまま stop することがあり、そのままだと回答が思考折りたたみに
        綴じ込まれて本文が空になる。

        stop 限定なのは意図的: 切断・length 打ち切りの中途 reasoning を
        回答に見せない(閉じ規律③で thinking のまま確定させる)。
        """
        if self._finish_reason != "stop" or self._response_tool_calls:
            return

        if not (
            self.content_blocks
            and self.content_blocks[-1]["type"] == "reasoning"
            and self.content_blocks[-1].get("attributes", {}).get("type")
            == "reasoning_content"
        ):
            return

        # 直前に非空 text があるなら本文は既にある = 真正の末尾思考なので触らない
        if (
            len(self.content_blocks) >= 2
            and self.content_blocks[-2]["type"] == "text"
            and self.content_blocks[-2]["content"].strip()
        ):
            return

        promoted = self.content_blocks.pop()["content"].strip()
        if not promoted:
            return

        if self.content_blocks and self.content_blocks[-1]["type"] == "text":
            self.content_blocks[-1]["content"] += promoted
        else:
            self.content_blocks.append({"type": "text", "content": promoted})
        self.content = f"{self.content}{promoted}"

    def close_open_reasoning_block(self):
        """reasoning の閉じ規律(llens):

        reasoning の終了は content 到着だけでなく「次の非 reasoning シグナル
        (tool_calls / ストリーム終端)」でも確定させる。Kimi は content デルタ
        無しで tool_calls や切断に直行することがあり(fixture:
        tool_call_no_args / aborted_client_cut)、閉じ損ねると done=false の
        「Thinking…」が永続化される。
        """
        if (
            self.content_blocks
            and self.content_blocks[-1]["type"] == "reasoning"
            and "ended_at" not in self.content_blocks[-1]
        ):
            block = self.content_blocks[-1]
            block["ended_at"] = time.monotonic()
            block["duration"] = round(block["ended_at"] - block["started_at"], 3)
            return True
        return False

    # --- 内部 -------------------------------------------------------------

    def _find_tool_calls_block(self, calls):
        """calls(同一オブジェクト)を content に持つ tool_calls ブロックの位置。"""
        for index, block in enumerate(self.content_blocks):
            if block["type"] == "tool_calls" and block["content"] is calls:
                return index
        return None

    def _open_tool_calls_block(self, calls):
        """ストリーム中の calls を open ブロックとして載せる(1 ストリーム 1 個)。

        「末尾が calls のブロックか」ではなく「calls のブロックが既にあるか」で
        判定する。tool call デルタの間に content デルタを挟むモデル(並列 call の
        区切りに改行を出す等)では、間に text ブロックが開いて calls のブロックが
        末尾でなくなる。末尾だけを見ると call ごとに同じ calls のブロックを積み増し、
        results の無い重複ブロックが永続化される(次ターンで [interrupted] 付きで
        再送される)。
        """
        self._attach_tool_renders(calls)
        if self._find_tool_calls_block(calls) is None:
            self.content_blocks.append({"type": "tool_calls", "content": calls})

    def _accumulate_tool_call_deltas(self, delta_tool_calls):
        """index キーで tool call デルタを突き合わせ、name/arguments を累積する。"""
        for delta_tool_call in delta_tool_calls:
            tool_call_index = delta_tool_call.get("index")

            if tool_call_index is None:
                continue

            current = None
            for response_tool_call in self._response_tool_calls:
                if response_tool_call.get("index") == tool_call_index:
                    current = response_tool_call
                    break

            if current is None:
                # 初回デルタの形はエンジン依存: SGLang は arguments: "" を
                # 必ず載せる(fixture の形)が、vLLM は arguments キー自体を
                # 省く。累積(+=)の土台をここで正規化しないと後続の argument
                # デルタが KeyError で全滅する(引数なし tool call になる —
                # 2026-08-18 qwen3.6-35b / vLLM 0.26 で実測)。
                function = delta_tool_call.get("function") or {}
                function.setdefault("name", "")
                function.setdefault("arguments", "")
                delta_tool_call["function"] = function
                self._response_tool_calls.append(delta_tool_call)
            else:
                delta_name = delta_tool_call.get("function", {}).get("name")
                delta_arguments = delta_tool_call.get("function", {}).get("arguments")

                if delta_name:
                    current["function"]["name"] += delta_name

                if delta_arguments:
                    current["function"]["arguments"] += delta_arguments

    def _attach_tool_renders(self, calls):
        """型駆動の表示ディスクリプタ(#20)を call に同梱する(冪等)。

        name が届いた時点で付与(Kimi は初回フレームで id/name が確定する)。
        content_blocks の snapshot は calls をそのまま通すので、ここで付けば
        イベント・永続化(リロード)双方に render が乗る。
        """
        for tool_call in calls:
            if "render" in tool_call:
                continue
            tool_name = tool_call.get("function", {}).get("name", "")
            tool_render = (self._tools.get(tool_name) or {}).get("render")
            if tool_render:
                tool_call["render"] = tool_render

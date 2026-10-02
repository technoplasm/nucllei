"""content blocks の wire / 永続化 / モデル向け履歴への変換(issue #17 L0, #23)。

スキーマの一次情報: _memos/rendering-design.md §3「L0 の確定スキーマ」。
組み立て(utils/response_assembler.py)の内部表現から、chat:completion イベントと
message 永続化で共用する最小形へ落とす。消費者のいないフィールドは足さない。
"""

import copy
import json

from nucllei.utils.image_refs import stored_ref_kind

# llens(#110): result 未着の call に対にする合成 result(FE
# chat-messages.ts expandAssistantToolHistory と同文 — 中断で途切れた履歴の修復)
INTERRUPTED_TOOL_RESULT = "[interrupted: this tool call produced no result]"


def serialize_text_blocks(content_blocks: list) -> str:
    """text ブロックのみを join した派生 content。

    永続化 message.content・イベント content はこれになる(会話検索・タイトル
    生成・API 互換・コピー用)。reasoning / tool_calls は content_blocks が
    正であり、details 擬似 HTML には二度と直列化しない(#17 L0)。
    """
    return "\n\n".join(
        block["content"]
        for block in content_blocks
        if block.get("type") == "text" and block.get("content")
    )


def snapshot_content_blocks(content_blocks: list) -> list:
    """内部 content_blocks を wire 形の累積スナップショットに変換する。

    - text: 空ブロック(組み立て途中のプレースホルダ)は落とす
    - reasoning: done は閉じ規律(ended_at の有無)から導出、duration は done 時のみ
    - tool_calls: calls + results。call 単位の done はフロントが results から導出
    """
    blocks = []
    for block in content_blocks:
        block_type = block.get("type")

        if block_type == "text":
            if block.get("content", ""):
                blocks.append({"type": "text", "content": block["content"]})

        elif block_type == "reasoning":
            done = "ended_at" in block
            snapshot = {
                "type": "reasoning",
                "content": block.get("content", ""),
                "done": done,
            }
            if done:
                snapshot["duration"] = block.get("duration", 0)
            blocks.append(snapshot)

        elif block_type == "tool_calls":
            # deepcopy: ストリーム中(#20 ステップ5)は middleware が calls を
            # 累積更新し続けるため、参照共有だと emit 済みスナップショットが
            # 遡って書き換わる。スナップショットは不変の wire 形として切り離す。
            snapshot = {
                "type": "tool_calls",
                "calls": copy.deepcopy(block.get("content", [])),
                "results": copy.deepcopy(block.get("results", [])),
            }
            if block.get("synthetic"):
                # `/command` 発火の合成 exchange(#35)。フロントの履歴再構成(#18-2)が
                # このマーカでスキップする(実体は inlet が毎リクエスト再注入するため、
                # 再送すると二重になる)。
                snapshot["synthetic"] = True
            blocks.append(snapshot)

    return blocks


def _model_facing_text(blocks: list) -> str:
    """text ブロックをモデル向け履歴の 1 assistant content に落とす。

    v0.6.5 の serialize_content_blocks 由来の join 規約(各ブロック strip +
    改行区切り)を踏襲。reasoning はここに来る前に除外されている。
    """
    return "".join(
        f"{block['content'].strip()}\n" for block in blocks if block["type"] == "text"
    ).strip()


def _call_arguments_are_sendable(call: dict) -> bool:
    """tool call の arguments が履歴再送に耐えるか(llens #110 — 修復の判定)。

    空文字は「引数なし」として送れる。非空は JSON として完全であること —
    途中で切れた壊れ JSON は SGLang が履歴パースで拒否するため落とす
    (FE chat-messages.ts の sendableCalls フィルタと同じ規律)。
    """
    arguments = (call.get("function") or {}).get("arguments") or ""
    if not arguments.strip():
        return True
    try:
        json.loads(arguments)
        return True
    except Exception:
        return False


def convert_content_blocks_to_messages(content_blocks: list) -> list:
    """組み立て済み content_blocks を tool ループ再送用のモデル向け履歴へ変換する。

    - reasoning はモデルへ再送しない(llens #18-2): 自ターンの思考内容が
      次呼び出しのプロンプトへ漏れるため。履歴に載るのは実際に出力された
      text / tool_calls / tool 結果のみ。
    - synthetic ブロック(`/command` 発火の UI 表示用 #35)はスキップ:
      実体の tool exchange は form_data["messages"] に注入済みで、変換すると
      二重になる。
    - tool_calls は OpenAI 形 {id, type, function} だけを綴じ込む(llens #20):
      render(表示ディスクリプタ)・index 等の内部キーを漏らさない。
    - tool 結果の画像(results[].context_images — tool が emitter
      __feed_image__ で push し middleware が載せる、#89)は、tool メッセージ群の直後に
      image_url を持つ user メッセージとして再投入する: role:tool の content は
      文字列契約で画素を運べないため。エントリの振り分けは `image_refs.stored_ref_kind`
      (保存済み参照の 1 箇所の規則): 裸の文字列(file id)だけ
      `/api/v1/files/{id}/content` に整形し、それ以外(`http(s)://…`・同一サーバの
      `/ext/…`・sandbox のパス `/mnt/…` `/workspace/…`・
      `/api/v1/files/{id}/content#page=N`)は参照文字列のまま image_url の url にする。
      実体化(base64)は送信直前 inline_internal_image_urls の 1 回に一任する。
      次ターン以降の履歴では FE expandAssistantToolHistory(chat-messages.ts)が同じ形・
      同じ位置で再送する(#142)— 形は共有 fixture
      test/fixtures/history_wire/context_images.json で両側のテストが固定している。
    - results[].display(人間の目、#88)は **モデルへ再注入しない**(ここで読まない)。
      context_images と対称だが向きが逆 — display はフロントが collapse 外へ描くだけの
      ユーザー面で、モデルには戻り値 content で「表示済み」と伝わる。2 チャネル分離の本体。
    - 壊れ tool_calls の修復(llens #110 契約 M5 — FE expandAssistantToolHistory
      chat-messages.ts と同等): 中断で途切れた履歴を再送すると SGLang は
      tool_calls.arguments をパースして "unexpected end of data" で拒否する(実測
      400)。arguments が壊れ JSON の call は落とし(実行されていない)、残った
      call に result が無ければ合成 result([interrupted])を対にする(tool_calls
      に tool 応答が無い履歴も不正になるため)。全滅ならブロックごとスキップ
      (text は前後に合流)。subagent の継続とサーバ駆動配達の続きターンの両方に効く。
    """
    messages = []

    temp_blocks = []
    for block in content_blocks:
        if block.get("synthetic"):
            continue
        if block["type"] == "tool_calls":
            # call 列のキーは 2 形ある(#110 敵対検証 4): 組み立て中の内部表現は
            # "content"、永続化スナップショット(snapshot_content_blocks)は
            # "calls"。続きターン(execution_resume / subagent の build_wire)は
            # DB の message.content_blocks = 永続化形を読むため、"calls" を
            # 読めないと tool 往復が丸ごと落ち、M5 の壊れ tool_calls 修復も
            # 効かない。両形を受ける("results" キーは両形で同名 — 形差なし)。
            sendable_calls = [
                call
                for call in (block.get("calls") or block.get("content") or [])
                if _call_arguments_are_sendable(call)
            ]
            if not sendable_calls:
                # 全滅(中断直後など)はブロックごとスキップ — text は前後に合流
                continue

            messages.append(
                {
                    "role": "assistant",
                    "content": _model_facing_text(temp_blocks),
                    "tool_calls": [
                        {
                            "id": call.get("id"),
                            "type": call.get("type", "function"),
                            # arguments は常に文字列で綴じる — キー欠落のまま再送
                            # すると上流の validation が Field required で 400 を
                            # 返す(vLLM 0.26 で実測、2026-08-18)
                            "function": {
                                "name": (call.get("function") or {}).get("name") or "",
                                "arguments": (call.get("function") or {}).get(
                                    "arguments"
                                )
                                or "",
                            },
                        }
                        for call in sendable_calls
                    ],
                }
            )

            results_by_call_id = {
                result.get("tool_call_id"): result
                for result in block.get("results", [])
            }
            sendable_results = []
            for call in sendable_calls:
                result = results_by_call_id.get(call.get("id"))
                if result is None:
                    # result 未着(中断)— 合成 result で対にする(修復)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id"),
                            "content": INTERRUPTED_TOOL_RESULT,
                        }
                    )
                    continue
                sendable_results.append(result)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": result["tool_call_id"],
                        "content": result.get("content") or "",
                    }
                )

            result_image_urls = [
                (
                    # 裸の文字列 = file id だけ配信 URL に組む。URL("https://…" と同一
                    # サーバの "/ext/…")・sandbox のパス("/mnt/…" "/workspace/…")・
                    # "/api/v1/files/{id}/content#page=N" は参照文字列のまま — 実体化は
                    # 送信直前の utils/file_images.py
                    f"/api/v1/files/{entry}/content"
                    if stored_ref_kind(entry) == "file_id"
                    else entry
                )
                for result in sendable_results
                for entry in (result.get("context_images") or [])
            ]
            if result_image_urls:
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "(直前の tool 結果の画像)"},
                            *(
                                {"type": "image_url", "image_url": {"url": url}}
                                for url in result_image_urls
                            ),
                        ],
                    }
                )
            temp_blocks = []
        else:
            temp_blocks.append(block)

    if temp_blocks:
        content = _model_facing_text(temp_blocks)
        if content:
            messages.append(
                {
                    "role": "assistant",
                    "content": content,
                }
            )

    return messages

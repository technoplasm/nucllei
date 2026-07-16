"""content blocks の wire / 永続化 / モデル向け履歴への変換(issue #17 L0, #23)。

スキーマの一次情報: _memos/rendering-design.md §3「L0 の確定スキーマ」。
組み立て(utils/response_assembler.py)の内部表現から、chat:completion イベントと
message 永続化で共用する最小形へ落とす。消費者のいないフィールドは足さない。
"""

import copy


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
    """
    messages = []

    temp_blocks = []
    for block in content_blocks:
        if block.get("synthetic"):
            continue
        if block["type"] == "tool_calls":
            messages.append(
                {
                    "role": "assistant",
                    "content": _model_facing_text(temp_blocks),
                    "tool_calls": [
                        {
                            "id": call.get("id"),
                            "type": call.get("type", "function"),
                            "function": call.get("function", {}),
                        }
                        for call in block.get("content", [])
                    ],
                }
            )

            for result in block.get("results", []):
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": result["tool_call_id"],
                        "content": result["content"],
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

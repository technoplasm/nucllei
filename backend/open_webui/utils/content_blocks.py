"""content blocks の wire / 永続化形への変換(issue #17 L0)。

スキーマの一次情報: _memos/rendering-design.md §3「L0 の確定スキーマ」。
middleware 内部の content_blocks 状態機械の表現から、chat:completion イベントと
message 永続化で共用する最小形へ落とす。消費者のいないフィールドは足さない。
"""


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
            blocks.append(
                {
                    "type": "tool_calls",
                    "calls": block.get("content", []),
                    "results": block.get("results", []),
                }
            )

        elif block_type == "code_interpreter":
            snapshot = {
                "type": "code_interpreter",
                "content": block.get("content", ""),
                "attributes": block.get("attributes", {}),
            }
            if block.get("output") is not None:
                snapshot["output"] = block["output"]
            blocks.append(snapshot)

    return blocks

"""SGLang×Kimi の「回答が think に綴じ込まれる」出力パターンを決定的に再現するモック。

フレーム形は _memos/model-frames.md の実キャプチャ仕様に合わせる:
role フレーム → reasoning_content* → (tool_calls*) → finish → usage → [DONE]

挙動:
- 1 巡目(messages に role:tool 無し): reasoning → load_skill(vault) の tool_calls で終了
- 追撃(role:tool あり): 最終回答を**丸ごと reasoning_content に載せ、content 空のまま stop**
  = バグの再現形(SGLang reasoning parser が think 内の回答を reasoning に振り分けた状態)

使い方(dev 実機で UI 再現する場合):
1. .venv/bin/python dev/e2e/mock_reasoning_model.py  (port 13399)
2. 接続に http://127.0.0.1:13399/v1 を追加(_memos/dev-environment.md の config curl)
3. preset を base=mock-reasoning・skillIds=["vault"] で作成(vault fixture 前提)
4. 「秘密のパスワードは?」を送る — 追撃が reasoning のみで stop する
   (昇格規律が無いと回答が「0秒間の思考」に綴じ込まれる)
"""

import asyncio
import json

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
import uvicorn

app = FastAPI()

MODEL_ID = "mock-reasoning"

FIRST_REASONING = (
    "ユーザーが「秘密のパスワードは?」と尋ねています。"
    '利用可能なツールに load_skill があり、skill_id の選択肢に "vault" があります。'
    'したがって、load_skill("vault") を呼ぶべきです。'
)

# 追撃: 回答そのものが reasoning_content として届く(content は一切来ない)
FINAL_REASONING = "秘密のパスワードは「じゃがいも」です。"


def chunk(delta, finish_reason=None):
    return (
        "data: "
        + json.dumps(
            {
                "id": "mock-1",
                "object": "chat.completion.chunk",
                "model": MODEL_ID,
                "choices": [
                    {"index": 0, "delta": delta, "finish_reason": finish_reason}
                ],
            },
            ensure_ascii=False,
        )
        + "\n\n"
    )


def usage_frame():
    return (
        "data: "
        + json.dumps(
            {
                "id": "mock-1",
                "object": "chat.completion.chunk",
                "model": MODEL_ID,
                "choices": [],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                },
            }
        )
        + "\n\n"
    )


def pieces(text, size=6):
    return [text[i : i + size] for i in range(0, len(text), size)]


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": MODEL_ID, "object": "model"}]}


@app.post("/v1/chat/completions")
async def completions(request: Request):
    body = await request.json()
    is_followup = any(m.get("role") == "tool" for m in body.get("messages", []))

    async def stream():
        yield chunk({"reasoning_content": None, "role": "assistant", "content": ""})
        text = FINAL_REASONING if is_followup else FIRST_REASONING
        for p in pieces(text):
            yield chunk({"reasoning_content": p})
            await asyncio.sleep(0.06)

        if is_followup:
            # ここが再現の核: content デルタを 1 つも出さずに stop で終える
            yield chunk({"reasoning_content": None}, finish_reason="stop")
        else:
            yield chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "functions.load_skill:0",
                            "type": "function",
                            "function": {
                                "name": "load_skill",
                                "arguments": '{"skill_id": "vault"}',
                            },
                        }
                    ]
                }
            )
            yield chunk({"reasoning_content": None}, finish_reason="tool_calls")

        yield usage_frame()
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=13399)

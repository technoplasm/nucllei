#!/usr/bin/env python3
"""SGLang × Kimi K2.6 の実出力フレームをキャプチャして fixture 化する(issue #29)。

使い方:
    uv run python scripts/capture_model_frames.py [--base-url http://prism-llens:8000/v1]

出力先: backend/open_webui/test/fixtures/model_frames/
- <scenario>.sse   : ストリーミングの生バイト列(SSE をそのまま保存。改変しない)
- <scenario>.json  : 非ストリーミング応答(scenario によっては省略)
- meta.json        : モデル ID・キャプチャ日時・リクエスト payload の記録

fixture は #15/#17/#20/#21 の回帰テストのリプレイ入力として使う。
SGLang / モデル更新時はこのスクリプトを再実行して差分を確認すること
(手順: _memos/model-frames.md)。
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

FIXTURE_DIR = (
    Path(__file__).parent.parent / "backend/open_webui/test/fixtures/model_frames"
)

WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "指定した都市の現在の天気を取得する",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "都市名"},
                "detail": {
                    "type": "string",
                    "description": "知りたい内容の詳細説明(長文可)",
                },
            },
            "required": ["city"],
        },
    },
}

RUN_PYTHON_TOOL = {
    "type": "function",
    "function": {
        "name": "run_python",
        "description": "Python コードをサンドボックスで実行する",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "実行する Python コード"}
            },
            "required": ["code"],
        },
    },
}


def scenarios(model: str) -> dict:
    base = {"model": model, "temperature": 0.0}
    return {
        # 通常テキスト(短い)
        "text_basic": {
            **base,
            "messages": [{"role": "user", "content": "1+1 はいくつ?数字だけ答えて。"}],
        },
        # reasoning が出る想定の問題(reasoning_content / <think> の載り方を観察)
        "reasoning": {
            **base,
            "messages": [
                {
                    "role": "user",
                    "content": "農夫が川を渡る。狼と山羊とキャベツを運ぶ古典パズルの最短手順を考えて、手順だけ簡潔に答えて。",
                }
            ],
        },
        # tool call(引数がチャンク境界で割れるケースを誘発するため detail を長く要求)
        "tool_call": {
            **base,
            "messages": [
                {
                    "role": "user",
                    "content": "札幌の天気を調べて。detail 引数には、知りたい観点(気温・湿度・降雪・風速・週間予報など)を日本語で 200 字程度で具体的に書いて。",
                }
            ],
            "tools": [WEATHER_TOOL],
        },
        # 並列 tool call(2 都市を同時に)
        "tool_call_parallel": {
            **base,
            "messages": [
                {
                    "role": "user",
                    "content": "札幌と福岡の天気を同時に調べて。必ず 2 都市分のツール呼び出しを並列で発行して。",
                }
            ],
            "tools": [WEATHER_TOOL],
        },
        # 長い code 引数(fence 表示・引数パーサの回帰用)
        "tool_call_long_code": {
            **base,
            "messages": [
                {
                    "role": "user",
                    "content": "FizzBuzz を class ベースで実装し、docstring・型ヒント・pytest テスト 3 本まで含めた 60 行程度の Python コードを run_python で実行して。",
                }
            ],
            "tools": [RUN_PYTHON_TOOL],
        },
        # reasoning → tool call の連続(交互パターンの観察)
        "reasoning_then_tool": {
            **base,
            "messages": [
                {
                    "role": "user",
                    "content": "旭川と那覇、今の時期に雪が降っている可能性が高いのはどちらかをまず論理的に考えてから、その都市の天気をツールで調べて。",
                }
            ],
            "tools": [WEATHER_TOOL],
        },
    }


def capture_stream(client: httpx.Client, url: str, payload: dict, out: Path):
    """SSE の生バイト列を到着チャンク単位で保存する(境界を --- で可視化はしない。
    受信したままを連結保存し、チャンク境界は .chunks.json に記録)。"""
    boundaries = []
    received = b""
    with client.stream(
        "POST",
        url,
        json={**payload, "stream": True, "stream_options": {"include_usage": True}},
    ) as r:
        r.raise_for_status()
        for chunk in r.iter_raw():
            boundaries.append(len(received))
            received += chunk
    out.write_bytes(received)
    out.with_suffix(".chunks.json").write_text(
        json.dumps({"chunk_offsets": boundaries, "total_bytes": len(received)})
    )


def capture_stream_aborted(
    client: httpx.Client, url: str, payload: dict, out: Path, max_frames: int = 5
):
    """クライアント側から途中切断したときの部分キャプチャ。"""
    received = b""
    frames = 0
    with client.stream("POST", url, json={**payload, "stream": True}) as r:
        r.raise_for_status()
        for chunk in r.iter_raw():
            received += chunk
            frames += chunk.count(b"data:")
            if frames >= max_frames:
                break  # 接続を閉じる(with を抜けると close)
    out.write_bytes(received)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        default=os.environ.get("LLENS_OPENAI_BASE_URL", "http://prism-llens:8000/v1"),
    )
    parser.add_argument(
        "--api-key", default=os.environ.get("LLENS_OPENAI_API_KEY", "EMPTY")
    )
    args = parser.parse_args()

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(
        timeout=httpx.Timeout(300.0, connect=10.0),
        headers={"Authorization": f"Bearer {args.api_key}"},
    )

    models = client.get(f"{args.base_url}/models").json()
    model = models["data"][0]["id"]
    url = f"{args.base_url}/chat/completions"

    meta = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "model": model,
        "server": models["data"][0],
        "scenarios": {},
    }

    for name, payload in scenarios(model).items():
        print(f"== {name}", flush=True)
        meta["scenarios"][name] = payload

        # 非ストリーミング
        try:
            r = client.post(url, json=payload)
            r.raise_for_status()
            (FIXTURE_DIR / f"{name}.json").write_text(
                json.dumps(r.json(), ensure_ascii=False, indent=1)
            )
        except Exception as e:
            print(f"  non-stream FAILED: {e}", file=sys.stderr)

        # ストリーミング(生 SSE)
        try:
            capture_stream(client, url, payload, FIXTURE_DIR / f"{name}.sse")
        except Exception as e:
            print(f"  stream FAILED: {e}", file=sys.stderr)

    # 途中切断(text_basic ベース)
    print("== aborted", flush=True)
    try:
        capture_stream_aborted(
            client,
            url,
            scenarios(model)["reasoning"],
            FIXTURE_DIR / "aborted_client_cut.sse",
        )
        meta["scenarios"]["aborted_client_cut"] = {
            "note": "reasoning シナリオを 5 frame 受信後にクライアント側から切断",
        }
    except Exception as e:
        print(f"  aborted FAILED: {e}", file=sys.stderr)

    (FIXTURE_DIR / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1)
    )
    print("done ->", FIXTURE_DIR)


if __name__ == "__main__":
    main()

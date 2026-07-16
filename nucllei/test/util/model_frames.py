"""実キャプチャ済みモデル出力フレーム(issue #29)のリプレイ用ローダ。

fixture(nucllei/test/fixtures/model_frames/)の生 SSE を
フレーム列に展開する。#15/#17/#20/#21 の回帰テストはこれを入力に使う。
仕様の観察結果: _memos/model-frames.md
"""

import json
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures/model_frames"


def fixture_names() -> list[str]:
    return sorted(p.stem for p in FIXTURE_DIR.glob("*.sse"))


def load_raw(name: str) -> bytes:
    """生 SSE バイト列(受信したまま)。"""
    return (FIXTURE_DIR / f"{name}.sse").read_bytes()


def load_frames(name: str) -> list[dict]:
    """SSE を data フレーム(dict)の列に展開する。[DONE] は含まない。"""
    frames = []
    for line in load_raw(name).decode("utf-8").split("\n"):
        if line.startswith("data: ") and line.strip() != "data: [DONE]":
            frames.append(json.loads(line[len("data: ") :]))
    return frames


def has_done(name: str) -> bool:
    return b"data: [DONE]" in load_raw(name)


def load_response(name: str) -> dict:
    """対応する非ストリーミング応答(存在する場合)。"""
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


def iter_deltas(frames: list[dict]):
    """choices[0].delta を (delta, finish_reason) で列挙する。usage フレームは除く。"""
    for frame in frames:
        choices = frame.get("choices") or []
        if not choices:
            continue
        yield choices[0].get("delta") or {}, choices[0].get("finish_reason")

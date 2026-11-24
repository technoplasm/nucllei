"""OpenAI 形の body → Ollama の /api/chat の body (utils/payload.convert_payload_openai_to_ollama)。

出力の上限: Ollama の options.num_predict に載る。トップレベルの max_completion_tokens /
max_tokens (タイトル生成の 4000・carried_tokens 計測の 1・API 利用者の指定) も、
options (チャットの params) の max_tokens も届く。トップレベルが勝つ。
"""

import copy

from nucllei.utils.payload import convert_payload_openai_to_ollama

MESSAGES = [{"role": "user", "content": "札幌の天気は?"}]


def test_top_level_max_tokens_becomes_num_predict():
    out = convert_payload_openai_to_ollama(
        {"model": "m", "messages": MESSAGES, "stream": False, "max_tokens": 4000}
    )
    assert out["options"] == {"num_predict": 4000}
    assert "max_tokens" not in out


def test_title_body_reaches_num_predict():
    # タイトル生成 (routers/tasks.generate_title) は max_completion_tokens で送る
    out = convert_payload_openai_to_ollama(
        {
            "model": "m",
            "messages": MESSAGES,
            "stream": False,
            "max_completion_tokens": 4000,
            "metadata": {"task": "title_generation"},
        }
    )
    assert out["options"] == {"num_predict": 4000}
    assert "max_completion_tokens" not in out


def test_measure_body_wins_over_chat_params_and_keeps_caller_options():
    # carried_tokens の計測: 初回の form_data の options (チャットの params) を
    # 共有して載せ、max_tokens / max_completion_tokens を 1 で上書きする
    chat_options = {"max_tokens": 2000, "temperature": 0.2, "keep_alive": "5m"}
    body = {
        "model": "m",
        "messages": MESSAGES,
        "stream": False,
        "options": chat_options,
        "max_tokens": 1,
        "max_completion_tokens": 1,
    }
    before = copy.deepcopy(body)
    out = convert_payload_openai_to_ollama(body)

    assert out["options"] == {"num_predict": 1, "temperature": 0.2}
    assert out["keep_alive"] == "5m"
    # 呼び手の dict (共有された options) は書き換えない
    assert body == before


def test_max_completion_tokens_wins_over_max_tokens():
    out = convert_payload_openai_to_ollama(
        {
            "model": "m",
            "messages": MESSAGES,
            "max_tokens": 10,
            "max_completion_tokens": 20,
        }
    )
    assert out["options"]["num_predict"] == 20


def test_chat_params_max_tokens_still_maps():
    out = convert_payload_openai_to_ollama(
        {
            "model": "m",
            "messages": MESSAGES,
            "options": {"max_tokens": 300, "system": "S"},
            "stop": ["。"],
        }
    )
    assert out["options"] == {"num_predict": 300, "stop": ["。"]}
    assert out["system"] == "S"


def test_no_limit_no_options():
    out = convert_payload_openai_to_ollama({"model": "m", "messages": MESSAGES})
    assert "options" not in out
    out = convert_payload_openai_to_ollama(
        {"model": "m", "messages": MESSAGES, "max_tokens": None}
    )
    assert "options" not in out

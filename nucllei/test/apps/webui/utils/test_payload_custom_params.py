"""custom_params(モデル設定の任意 KV)の body 素通しマージ(utils/payload.py)。

モデルエディタ Advanced の Custom KV が OpenAI 互換 body のトップレベルに乗ることを固定する。
SGLang の chat_template_kwargs({"thinking": true} 等)が動機の CORE(OpenAI 互換接続)回帰。
"""

from nucllei.utils.payload import apply_model_params_to_body_openai


def test_custom_params_merged_top_level():
    params = {
        "temperature": 0.5,
        "custom_params": {
            "chat_template_kwargs": {"thinking": True, "preserve_thinking": False},
            "repetition_penalty": 1.05,
        },
    }
    body = apply_model_params_to_body_openai(params, {"model": "m", "messages": []})

    assert body["chat_template_kwargs"] == {
        "thinking": True,
        "preserve_thinking": False,
    }
    assert body["repetition_penalty"] == 1.05  # custom のキーもトップレベルに乗る
    assert body["temperature"] == 0.5  # 既存 whitelist は従来どおり
    # コンテナキー自体は body に漏れない
    assert "custom_params" not in body


def test_custom_params_absent_or_null_is_noop():
    assert apply_model_params_to_body_openai({}, {"a": 1}) == {"a": 1}
    assert apply_model_params_to_body_openai({"custom_params": None}, {"a": 1}) == {
        "a": 1
    }


def test_custom_params_wins_over_mapped_param():
    # 同名キーは custom 側が後勝ち(明示上書きの escape hatch として)
    params = {"temperature": 0.5, "custom_params": {"temperature": 1.5}}
    body = apply_model_params_to_body_openai(params, {})
    assert body["temperature"] == 1.5


def test_editor_sampling_params_reach_openai_compatible_body():
    # モデル編集画面で設定できる値が OpenAI 互換接続へ黙って落ちない
    params = {
        "presence_penalty": "0.5",
        "top_k": "20",
        "min_p": 0.05,
        "repeat_penalty": 1.1,
    }
    body = apply_model_params_to_body_openai(params, {})
    assert body["presence_penalty"] == 0.5
    assert body["top_k"] == 20
    assert body["min_p"] == 0.05
    # Ollama 名 repeat_penalty は互換エンジン(SGLang / vLLM)の名前へ
    assert body["repetition_penalty"] == 1.1
    assert "repeat_penalty" not in body
    assert params == {  # 呼び出し側の params は変えない
        "presence_penalty": "0.5",
        "top_k": "20",
        "min_p": 0.05,
        "repeat_penalty": 1.1,
    }


def test_stop_string_is_not_split_into_characters():
    from nucllei.utils.payload import apply_model_params_to_body_ollama

    assert apply_model_params_to_body_openai({"stop": "###"}, {})["stop"] == ["###"]
    assert apply_model_params_to_body_ollama({"stop": "###"}, {})["stop"] == ["###"]
    body = apply_model_params_to_body_openai({"stop": ["###", "END"]}, {})
    assert body["stop"] == ["###", "END"]


def test_stop_escapes_are_decoded_without_mangling_non_ascii():
    body = apply_model_params_to_body_openai({"stop": ["\\n\\n", "。", "終了"]}, {})
    assert body["stop"] == ["\n\n", "。", "終了"]

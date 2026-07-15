"""実測 usage 要求(stream_options.include_usage)の opt-out 回帰テスト(issue #57 / #70)。

llens は streaming で実測 usage を受け取るため include_usage を backend が注入する(#57)。
横展開先は SGLang/閉域とは限らず、stream_options を未知パラメータとして弾く
OpenAI 互換実装に繋ぐ配備がありうるため、モデルの capabilities.usage=False を
opt-out として注入を止める(2026-07-15 決定)。既定(キー不在)は ON。
"""

from open_webui.utils.middleware import is_usage_requested


def model_with_capabilities(capabilities):
    return {"info": {"meta": {"capabilities": capabilities}}}


class TestIsUsageRequested:
    def test_default_on_when_capabilities_absent(self):
        assert is_usage_requested({}) is True
        assert is_usage_requested(None) is True
        assert is_usage_requested({"info": {}}) is True
        assert is_usage_requested({"info": {"meta": {}}}) is True
        assert is_usage_requested(model_with_capabilities(None)) is True

    def test_default_on_when_usage_key_absent(self):
        assert is_usage_requested(model_with_capabilities({"vision": True})) is True

    def test_explicit_true(self):
        assert is_usage_requested(model_with_capabilities({"usage": True})) is True

    def test_opt_out_with_false(self):
        # 弾く互換 API 向けの opt-out — False のときだけ要求しない
        assert is_usage_requested(model_with_capabilities({"usage": False})) is False

    def test_none_is_not_opt_out(self):
        # 旧データ等で usage: null が入っていても opt-out 扱いにしない(False 限定)
        assert is_usage_requested(model_with_capabilities({"usage": None})) is True

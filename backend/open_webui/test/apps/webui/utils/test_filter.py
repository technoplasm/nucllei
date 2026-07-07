"""Filter パイプライン(inlet / outlet / stream)の回帰テスト。

llens の最重要資産である Filter 実行モデル(fork.md §3.2、issue #3)を固定する。
ここが壊れる変更は de-identification / TTS 注入 / ログ加工の全てを壊す。

- ユニット層: fake の Filter インスタンスを app.state.FUNCTIONS に注入して
  process_filter_functions の挙動を直接検証する(DB 非依存)。
- DB 統合層(TestFilterPipelineDB): 実際に Function レコードを登録し、
  plugin ローダ経由のロード・valves・priority 順序を検証する。
"""

import asyncio
from types import SimpleNamespace

import pytest

from test.util.abstract_integration_test import AbstractPostgresTest


def make_request(functions=None):
    """app.state.FUNCTIONS(モジュールキャッシュ)だけを持つ request もどき。"""
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(FUNCTIONS=dict(functions or {})))
    )


def refs(*ids):
    """process_filter_functions に渡す FunctionModel もどき(.id のみ参照される)。"""
    return [SimpleNamespace(id=i) for i in ids]


def run(coro):
    return asyncio.run(coro)


class TraceFilter:
    """呼び出し順・引数を記録する基本の fake Filter。"""

    def __init__(self, name):
        self.name = name

    def inlet(self, body):
        body.setdefault("trace", []).append(f"{self.name}:inlet")
        return body

    def outlet(self, body):
        body.setdefault("trace", []).append(f"{self.name}:outlet")
        return body

    def stream(self, event):
        event.setdefault("trace", []).append(f"{self.name}:stream")
        return event


class TestProcessFilterFunctionsUnit:
    def test_inlet_modifies_body(self):
        from open_webui.utils.filter import process_filter_functions

        request = make_request({"f1": TraceFilter("f1")})
        body, flags = run(
            process_filter_functions(
                request=request,
                filter_functions=refs("f1"),
                filter_type="inlet",
                form_data={"messages": []},
                extra_params={},
            )
        )
        assert body["trace"] == ["f1:inlet"]
        assert flags == {}

    def test_outlet_modifies_body(self):
        from open_webui.utils.filter import process_filter_functions

        request = make_request({"f1": TraceFilter("f1")})
        body, _ = run(
            process_filter_functions(
                request=request,
                filter_functions=refs("f1"),
                filter_type="outlet",
                form_data={"messages": []},
                extra_params={},
            )
        )
        assert body["trace"] == ["f1:outlet"]

    def test_stream_receives_event(self):
        from open_webui.utils.filter import process_filter_functions

        class StreamFilter:
            # stream フィルタは body ではなく event キーワードで受ける
            def stream(self, event):
                event["touched"] = True
                return event

        request = make_request({"f1": StreamFilter()})
        event, _ = run(
            process_filter_functions(
                request=request,
                filter_functions=refs("f1"),
                filter_type="stream",
                form_data={"choices": []},
                extra_params={},
            )
        )
        assert event["touched"] is True

    def test_filters_chain_in_given_order(self):
        from open_webui.utils.filter import process_filter_functions

        request = make_request({"a": TraceFilter("a"), "b": TraceFilter("b")})
        body, _ = run(
            process_filter_functions(
                request=request,
                filter_functions=refs("a", "b"),
                filter_type="inlet",
                form_data={},
                extra_params={},
            )
        )
        assert body["trace"] == ["a:inlet", "b:inlet"]

    def test_async_handler_supported(self):
        from open_webui.utils.filter import process_filter_functions

        class AsyncFilter:
            async def inlet(self, body):
                body["async"] = True
                return body

        request = make_request({"f1": AsyncFilter()})
        body, _ = run(
            process_filter_functions(
                request=request,
                filter_functions=refs("f1"),
                filter_type="inlet",
                form_data={},
                extra_params={},
            )
        )
        assert body["async"] is True

    def test_module_without_handler_is_skipped(self):
        from open_webui.utils.filter import process_filter_functions

        class OutletOnly:
            def outlet(self, body):
                body["outlet"] = True
                return body

        request = make_request({"f1": OutletOnly(), "f2": TraceFilter("f2")})
        body, _ = run(
            process_filter_functions(
                request=request,
                filter_functions=refs("f1", "f2"),
                filter_type="inlet",
                form_data={},
                extra_params={},
            )
        )
        # f1 は inlet を持たないためスキップされ、f2 だけが実行される
        assert body["trace"] == ["f2:inlet"]

    def test_file_handler_strips_files_on_inlet(self):
        from open_webui.utils.filter import process_filter_functions

        class FileHandlerFilter(TraceFilter):
            file_handler = True

        request = make_request({"f1": FileHandlerFilter("f1")})
        body, _ = run(
            process_filter_functions(
                request=request,
                filter_functions=refs("f1"),
                filter_type="inlet",
                form_data={
                    "files": [{"id": "x"}],
                    "metadata": {"files": [{"id": "x"}]},
                },
                extra_params={},
            )
        )
        # file_handler = True のフィルタが files を処理済みとして本体から除去される
        assert "files" not in body
        assert "files" not in body["metadata"]

    def test_file_handler_false_keeps_files(self):
        from open_webui.utils.filter import process_filter_functions

        class NoFileHandlerFilter(TraceFilter):
            file_handler = False

        request = make_request({"f1": NoFileHandlerFilter("f1")})
        body, _ = run(
            process_filter_functions(
                request=request,
                filter_functions=refs("f1"),
                filter_type="inlet",
                form_data={
                    "files": [{"id": "x"}],
                    "metadata": {"files": [{"id": "x"}]},
                },
                extra_params={},
            )
        )
        assert body["files"] == [{"id": "x"}]

    def test_exception_propagates(self):
        from open_webui.utils.filter import process_filter_functions

        class BrokenFilter:
            def inlet(self, body):
                raise ValueError("boom")

        request = make_request({"f1": BrokenFilter()})
        with pytest.raises(ValueError, match="boom"):
            run(
                process_filter_functions(
                    request=request,
                    filter_functions=refs("f1"),
                    filter_type="inlet",
                    form_data={},
                    extra_params={},
                )
            )

    def test_extra_params_passed_only_if_in_signature(self):
        from open_webui.utils.filter import process_filter_functions

        captured = {}

        class ParamFilter:
            def inlet(self, body, __id__, __event_emitter__):
                captured["id"] = __id__
                captured["emitter"] = __event_emitter__
                return body

        emitter = object()
        request = make_request({"f1": ParamFilter()})
        run(
            process_filter_functions(
                request=request,
                filter_functions=refs("f1"),
                filter_type="inlet",
                form_data={},
                extra_params={
                    "__event_emitter__": emitter,
                    "__not_in_signature__": "ignored",
                },
            )
        )
        assert captured["id"] == "f1"
        assert captured["emitter"] is emitter

    def test_user_valves_injected(self, monkeypatch):
        from open_webui.utils import filter as filter_module
        from open_webui.utils.filter import process_filter_functions

        captured = {}

        class UserValvesFilter:
            from pydantic import BaseModel

            class UserValves(BaseModel):
                mode: str = "default"

            def inlet(self, body, __user__):
                captured["user"] = __user__
                return body

        monkeypatch.setattr(
            filter_module.Functions,
            "get_user_valves_by_id_and_user_id",
            lambda fid, uid: {"mode": "custom"},
        )

        request = make_request({"f1": UserValvesFilter()})
        run(
            process_filter_functions(
                request=request,
                filter_functions=refs("f1"),
                filter_type="inlet",
                form_data={},
                extra_params={"__user__": {"id": "u1"}},
            )
        )
        assert captured["user"]["valves"].mode == "custom"


FILTER_CONTENT_TEMPLATE = """
from pydantic import BaseModel


class Filter:
    class Valves(BaseModel):
        priority: int = 0

    def __init__(self):
        self.valves = self.Valves()

    def inlet(self, body: dict) -> dict:
        body.setdefault("trace", []).append("{fid}:inlet")
        return body

    def outlet(self, body: dict) -> dict:
        body.setdefault("trace", []).append("{fid}:outlet")
        return body

    def stream(self, event: dict) -> dict:
        event.setdefault("trace", []).append("{fid}:stream")
        return event
"""


class TestFilterPipelineDB(AbstractPostgresTest):
    """DB に登録した実 Function を plugin ローダ経由で実行する統合テスト。"""

    BASE_PATH = "/api/v1/functions"

    def setup_class(cls):
        super().setup_class()
        from open_webui.models.functions import Functions

        cls.functions = Functions

    def _insert_filter(self, fid, priority=0):
        # llens(#46): is_active / is_global 撤去。filter はモデルの filterIds 割り当てのみで適用。
        from open_webui.models.functions import FunctionForm

        function = self.functions.insert_new_function(
            user_id="1",
            type="filter",
            form_data=FunctionForm(
                id=fid,
                name=fid,
                description="test filter",
                content=FILTER_CONTENT_TEMPLATE.replace("{fid}", fid),
            ),
        )
        assert function is not None
        if priority:
            self.functions.update_function_valves_by_id(fid, {"priority": priority})
        return function

    def test_get_sorted_filter_ids_priority_and_scope(self):
        from open_webui.utils.filter import get_sorted_filter_ids

        # llens(#46): モデルの filterIds 割り当てのみが priority 昇順で並ぶ。
        self._insert_filter("f_model_a", priority=5)
        self._insert_filter("f_model_b", priority=1)
        # 割り当てられていない filter は出ない
        self._insert_filter("f_unassigned", priority=1)

        model = {
            # "nonexistent" は実在しないので弾かれる(削除済み ID ガード)
            "info": {"meta": {"filterIds": ["f_model_a", "f_model_b", "nonexistent"]}}
        }
        assert get_sorted_filter_ids(model) == ["f_model_b", "f_model_a"]

    def test_pipeline_inlet_llm_outlet_order(self):
        """inlet → (LLM 相当の応答生成) → outlet の一連の流れを固定する。"""
        from open_webui.utils.filter import (
            get_sorted_filter_ids,
            process_filter_functions,
        )

        self._insert_filter("f_first", priority=1)
        self._insert_filter("f_second", priority=2)

        model = {"info": {"meta": {"filterIds": ["f_first", "f_second"]}}}
        sorted_ids = get_sorted_filter_ids(model)
        assert sorted_ids == ["f_first", "f_second"]

        request = make_request()
        filter_functions = [
            self.functions.get_function_by_id(fid) for fid in sorted_ids
        ]

        # inlet: リクエスト body が優先度順に加工される
        body, _ = run(
            process_filter_functions(
                request=request,
                filter_functions=filter_functions,
                filter_type="inlet",
                form_data={"messages": [{"role": "user", "content": "hi"}]},
                extra_params={},
            )
        )
        assert body["trace"] == ["f_first:inlet", "f_second:inlet"]
        assert body["messages"] == [{"role": "user", "content": "hi"}]

        # LLM 相当: 応答(outlet に渡る data)を用意
        response_data = {
            "messages": body["messages"] + [{"role": "assistant", "content": "hello"}],
        }

        # outlet: 応答 body が同じ優先度順に加工される
        out, _ = run(
            process_filter_functions(
                request=request,
                filter_functions=filter_functions,
                filter_type="outlet",
                form_data=response_data,
                extra_params={},
            )
        )
        assert out["trace"] == ["f_first:outlet", "f_second:outlet"]
        assert out["messages"][-1]["role"] == "assistant"

    def test_stream_event_processing(self):
        """stream フィルタが SSE イベント(delta chunk)を加工できることを固定する。"""
        from open_webui.utils.filter import process_filter_functions

        self._insert_filter("f_stream")

        request = make_request()
        filter_functions = [self.functions.get_function_by_id("f_stream")]

        event = {
            "choices": [{"delta": {"content": "chunk"}}],
        }
        processed, _ = run(
            process_filter_functions(
                request=request,
                filter_functions=filter_functions,
                filter_type="stream",
                form_data=event,
                extra_params={},
            )
        )
        assert processed["trace"] == ["f_stream:stream"]
        assert processed["choices"][0]["delta"]["content"] == "chunk"

    def test_db_valves_applied_to_module(self):
        """DB に保存した valves がモジュールインスタンスへ反映される。"""
        from open_webui.utils.filter import process_filter_functions

        self._insert_filter("f_valved")
        self.functions.update_function_valves_by_id("f_valved", {"priority": 7})

        request = make_request()
        run(
            process_filter_functions(
                request=request,
                filter_functions=[self.functions.get_function_by_id("f_valved")],
                filter_type="inlet",
                form_data={},
                extra_params={},
            )
        )
        module = request.app.state.FUNCTIONS["f_valved"]
        assert module.valves.priority == 7

    def test_module_cached_in_app_state(self):
        """一度ロードしたモジュールは app.state.FUNCTIONS から再利用される。"""
        from open_webui.utils.filter import process_filter_functions

        self._insert_filter("f_cache")

        request = make_request()
        filter_functions = [self.functions.get_function_by_id("f_cache")]
        run(
            process_filter_functions(
                request=request,
                filter_functions=filter_functions,
                filter_type="inlet",
                form_data={},
                extra_params={},
            )
        )
        cached = request.app.state.FUNCTIONS["f_cache"]
        run(
            process_filter_functions(
                request=request,
                filter_functions=filter_functions,
                filter_type="inlet",
                form_data={},
                extra_params={},
            )
        )
        assert request.app.state.FUNCTIONS["f_cache"] is cached

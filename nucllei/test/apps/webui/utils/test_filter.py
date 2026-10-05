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
        from nucllei.utils.filter import process_filter_functions

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
        from nucllei.utils.filter import process_filter_functions

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
        from nucllei.utils.filter import process_filter_functions

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
        from nucllei.utils.filter import process_filter_functions

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
        from nucllei.utils.filter import process_filter_functions

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
        from nucllei.utils.filter import process_filter_functions

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

    def test_file_handler_attribute_is_ignored(self):
        """`file_handler = True` は撤去済み(2026-10-05)— 宣言しても添付は残る。

        旧機構は inlet 後に files を丸ごと落とし、添付の無い送信では
        del form_data["files"] の KeyError で 400 になっていた。添付の間引きは
        inlet で body を書き換えて行う(DB 層の README テスト参照)。
        """
        from nucllei.utils.filter import process_filter_functions

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
        assert body["files"] == [{"id": "x"}]
        assert body["metadata"]["files"] == [{"id": "x"}]

        # 添付の無い送信も通る
        body, _ = run(
            process_filter_functions(
                request=request,
                filter_functions=refs("f1"),
                filter_type="inlet",
                form_data={"metadata": {"files": None}},
                extra_params={},
            )
        )
        assert body["trace"] == ["f1:inlet"]

    def test_exception_propagates(self):
        from nucllei.utils.filter import process_filter_functions

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
        from nucllei.utils.filter import process_filter_functions

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

    def test_user_is_a_per_filter_copy_without_valves(self):
        """__user__ は filter ごとのコピー。UserValves は撤去済み(2026-10-05)。

        旧実装は共有の extra_params["__user__"] に UserValves を書き込み、後続の
        filter や同じ dict を使う tool / pipe にも見えていた。filter が __user__ を
        書き換えても呼び手の dict(この後 tool / pipe に渡る)は変わらない。
        """
        from pydantic import BaseModel

        from nucllei.utils.filter import process_filter_functions

        seen = {}

        class Mutating:
            class UserValves(BaseModel):  # 宣言しても注入されない
                secret: str = "f1-default"

            def inlet(self, body, __user__):
                seen["f1"] = dict(__user__)
                __user__["valves"] = "written-by-f1"
                __user__["name"] = "changed"
                return body

        class Reader:
            def inlet(self, body, __user__):
                seen["f2"] = dict(__user__)
                return body

        shared_user = {"id": "u1", "name": "n", "valves": "stale-from-elsewhere"}
        request = make_request({"f1": Mutating(), "f2": Reader()})
        run(
            process_filter_functions(
                request=request,
                filter_functions=refs("f1", "f2"),
                filter_type="inlet",
                form_data={},
                extra_params={"__user__": shared_user},
            )
        )
        assert seen["f1"] == {"id": "u1", "name": "n"}
        assert seen["f2"] == {"id": "u1", "name": "n"}
        assert shared_user == {
            "id": "u1",
            "name": "n",
            "valves": "stale-from-elsewhere",
        }


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


PRIORITY_FILTER_TEMPLATE = """
from pydantic import BaseModel


class Filter:
    class Valves(BaseModel):
        priority: int = {priority}
        label: str = "x"

    def __init__(self):
        self.valves = self.Valves()

    def inlet(self, body: dict) -> dict:
        return body
"""

# README_API §7.1 の例(Valves.priority + inlet(body, __user__))に、同節で案内する
# 添付の間引き(body["files"] / metadata["files"] を inlet で書き換える)を足したもの
README_FILE_FILTER = """
from pydantic import BaseModel, Field


class Filter:
    class Valves(BaseModel):
        priority: int = Field(default=0, description="Lower runs earlier")

    def __init__(self):
        self.valves = self.Valves()

    def inlet(self, body: dict, __user__: dict) -> dict:
        body["seen_files"] = body.get("files")
        keep = lambda fs: [f for f in fs if f.get("id") != "drop"]
        if isinstance(body.get("files"), list):
            body["files"] = keep(body["files"])
        md = body.get("metadata")
        if isinstance(md, dict) and isinstance(md.get("files"), list):
            md["files"] = keep(md["files"])
        return body
"""


class TestFilterPipelineDB(AbstractPostgresTest):
    """DB に登録した実 Function を plugin ローダ経由で実行する統合テスト。"""

    BASE_PATH = "/api/v1/functions"

    def setup_class(cls):
        super().setup_class()
        from nucllei.models.functions import Functions

        cls.functions = Functions

    def _insert_filter(self, fid, priority=0):
        # llens(#46): is_active / is_global 撤去。filter はモデルの filterIds 割り当てのみで適用。
        from nucllei.models.functions import FunctionForm

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
        from nucllei.utils.filter import get_sorted_filter_ids

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

    def _insert_filter_source(self, fid, content):
        from nucllei.models.functions import FunctionForm

        return self.functions.insert_new_function(
            user_id="1",
            type="filter",
            form_data=FunctionForm(
                id=fid, name=fid, description="test filter", content=content
            ),
        )

    def test_priority_uses_code_default(self):
        """保存 valves の無い filter は Valves のコード既定 priority で並ぶ。

        旧実装は保存 dict の "priority" だけを読み、未保存なら一律 0 だった。
        保存値があるフィールドだけが既定を上書きする。
        """
        from nucllei.utils.filter import get_sorted_filter_ids

        for fid, prio in (("f_late", 100), ("f_early", -100), ("f_mid", 0)):
            self._insert_filter_source(
                fid, PRIORITY_FILTER_TEMPLATE.replace("{priority}", str(prio))
            )
        model = {"info": {"meta": {"filterIds": ["f_late", "f_mid", "f_early"]}}}
        request = make_request()
        assert get_sorted_filter_ids(model, request) == ["f_early", "f_mid", "f_late"]

        # 保存値は既定を上書きする(部分保存 — 他フィールドはコード既定のまま)
        self.functions.update_function_valves_by_id("f_late", {"priority": -200})
        assert get_sorted_filter_ids(model, request) == ["f_late", "f_early", "f_mid"]

    def test_valves_save_keeps_only_explicit_fields(self):
        """valves 保存は明示したフィールドだけ — null(UI の「Default」)と未指定は
        保存せず、読み出し時にコード既定が効く(既定を変えれば追従する)。"""
        from test.util.mock_user import mock_webui_user

        self._insert_filter_source(
            "f_saved", PRIORITY_FILTER_TEMPLATE.replace("{priority}", "42")
        )
        with mock_webui_user(id="9", role="admin"):
            res = self.fast_api_client.post(
                self.create_url("/id/f_saved/valves/update"),
                json={"priority": None, "label": "custom"},
            )
        assert res.status_code == 200
        # 応答は実効値(既定込み)、保存は明示分だけ
        assert res.json() == {"priority": 42, "label": "custom"}
        assert self.functions.get_function_valves_by_id("f_saved") == {
            "label": "custom"
        }

    def test_same_priority_keeps_filter_ids_order(self):
        """同 priority は filterIds の並び順(PYTHONHASHSEED 非依存)。

        旧実装は list(set(filterIds)) で順序がハッシュ依存 — 起動ごとに実行順が
        変わっていた。どの並びで渡しても、そのまま(重複は先勝ちで 1 つ)返る。
        """
        import itertools

        from nucllei.utils.filter import get_sorted_filter_ids

        ids = [f"f_same_{c}" for c in "abcde"]
        for fid in ids:
            self._insert_filter(fid)
        request = make_request()
        for perm in itertools.permutations(ids):
            model = {"info": {"meta": {"filterIds": list(perm) + [perm[0]]}}}
            assert get_sorted_filter_ids(model, request) == list(perm)

    def test_readme_filter_thins_attachments(self):
        """README_API §7.1 の書き方の filter が、添付あり・無しの両方で動く。"""
        from nucllei.utils.filter import get_sorted_filter_ids, process_filter_functions

        self._insert_filter_source("f_readme", README_FILE_FILTER)
        request = make_request()
        model = {"info": {"meta": {"filterIds": ["f_readme"]}}}
        filter_functions = [
            self.functions.get_function_by_id(fid)
            for fid in get_sorted_filter_ids(model, request)
        ]

        def inlet(form_data):
            body, _ = run(
                process_filter_functions(
                    request=request,
                    filter_functions=filter_functions,
                    filter_type="inlet",
                    form_data=form_data,
                    extra_params={"__user__": {"id": "1"}},
                )
            )
            return body

        body = inlet(
            {
                "messages": [{"role": "user", "content": "hi"}],
                "metadata": {"files": None},
            }
        )
        assert body["seen_files"] is None
        assert body["metadata"]["files"] is None

        files = [{"id": "keep"}, {"id": "drop"}]
        body = inlet(
            {
                "messages": [{"role": "user", "content": "hi"}],
                "files": list(files),
                "metadata": {"files": list(files)},
            }
        )
        assert body["seen_files"] == [{"id": "keep"}, {"id": "drop"}]
        assert body["files"] == [{"id": "keep"}]
        assert body["metadata"]["files"] == [{"id": "keep"}]

    def test_pipeline_inlet_llm_outlet_order(self):
        """inlet → (LLM 相当の応答生成) → outlet の一連の流れを固定する。"""
        from nucllei.utils.filter import (
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
        from nucllei.utils.filter import process_filter_functions

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
        from nucllei.utils.filter import process_filter_functions

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
        from nucllei.utils.filter import process_filter_functions

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

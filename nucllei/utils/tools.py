import inspect
import logging
import re
import aiohttp
import asyncio
import yaml

from pydantic import BaseModel
from typing import (
    Any,
    Awaitable,
    Callable,
    get_type_hints,
    Dict,
    List,
    Optional,
)
from functools import update_wrapper, partial


from fastapi import Request
from pydantic import Field, create_model

from langchain_core.utils.function_calling import (
    convert_to_openai_function as convert_pydantic_model_to_openai_function_spec,
)


from nucllei.models.tools import Tools
from nucllei.models.users import UserModel
from nucllei.utils.access_control import has_access
from nucllei.utils.plugin import load_tool_module_by_id
from nucllei.utils.tool_render import (
    apply_render_to_spec,
    extract_render_from_function,
    extract_render_from_spec,
)
from nucllei.env import AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA

import copy

log = logging.getLogger(__name__)


def get_async_tool_function_and_apply_extra_params(
    function: Callable, extra_params: dict
) -> Callable[..., Awaitable]:
    sig = inspect.signature(function)
    extra_params = {k: v for k, v in extra_params.items() if k in sig.parameters}
    partial_func = partial(function, **extra_params)

    if inspect.iscoroutinefunction(function):
        update_wrapper(partial_func, function)
        return partial_func
    else:
        # llens(#28): sync の tool 関数はワーカースレッドで実行する。
        # イベントループ上で直接呼ぶと、ブロッキングする tool(sleep / requests /
        # 重い計算)の間サーバ全体のストリーミングが止まる。
        async def new_function(*args, **kwargs):
            return await asyncio.to_thread(partial_func, *args, **kwargs)

        update_wrapper(new_function, function)
        return new_function


# llens(#140): `Tools` クラスの汎用フック名。get_tools がターンごとに呼ぶ予約名で、
# tool 関数としては spec 化しない(get_functions_from_tool が除外する)。
#   available(self, request, user, model, metadata) -> bool
#       False ならこのターン toolkit ごと載せない(read ACL 判定の後に呼ぶ)
#   spec_overrides(self, request, user, model, metadata) -> dict[str, dict]
#       関数名 → spec の部分パッチ。このターンの spec へ deep-merge する
# model は親ターンの preset(metadata["model"]。無ければ __model__)、metadata は
# __metadata__。どちらのフックも同期関数で、get_tools ごとワーカースレッドで走る(#28)。
TOOLKIT_HOOK_NAMES = frozenset({"available", "spec_overrides"})


def _deep_merge_spec(base: dict, patch: dict) -> dict:
    """spec_overrides の適用: dict は再帰マージ、それ以外(str / list / 数値)は置き換え。

    base は get_tools がこの呼び出しで DB から読んだ spec(per-call の新しい
    オブジェクト)なので in-place で変異してよい。
    """
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge_spec(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def _hook_model(extra_params: dict):
    """フックに渡す model: 親ターンの preset(metadata["model"])を優先する。

    tools_extra_params の `__model__` はタスクモデル(get_task_model_id)に
    差し替えられうるので、preset meta(allowlist 等)を読むフックには不向き。
    """
    metadata = extra_params.get("__metadata__") or {}
    return metadata.get("model") or extra_params.get("__model__") or {}


def get_tools(
    request: Request,
    tool_ids: list[str],
    user: UserModel,
    extra_params: dict,
    enforce_access_control: bool = True,
) -> dict[str, dict]:
    """tool_ids → {name: {tool_id, callable, spec, ...}}。

    enforce_access_control=False はサーバー由来の id 専用(#35 step2b: skill の
    tool_ids。skill の read 可視性 + 「tool 紐づき skill は admin のみ作成可」が
    認可の単位)。リクエスト由来の id では必ず True のまま使うこと(GHSA-4pcg)。
    """
    tools_dict = {}

    # 同じ id の重複指定は 1 回だけ読む(自分自身との名前衝突にしない)
    for tool_id in dict.fromkeys(tool_ids):
        tool = Tools.get_tool_by_id(tool_id)
        if tool is None:
            if tool_id.startswith("server:"):
                tool_server_connection, tool_server_data = _resolve_tool_server(
                    request, tool_id
                )
                if tool_server_data is None:
                    continue

                # 実行時も一覧(routers/tools.py の GET /tools)と同じ read ACL を見る。
                # 旧実装は一覧で隠すだけで、meta.toolIds に server:N が載った preset なら
                # ACL 外のユーザーでも実行できた(admin はバイパス。tool server は
                # 所有者を持たない — 一覧の user_id も "server:N")。
                if enforce_access_control and not (
                    user.role == "admin"
                    or has_access(
                        user.id,
                        "read",
                        (tool_server_connection.get("config") or {}).get(
                            "access_control", None
                        ),
                    )
                ):
                    log.warning(
                        f"User {user.id} lacks access to tool server {tool_id}; skipping."
                    )
                    continue

                specs = tool_server_data.get("specs", [])

                for spec in specs:
                    function_name = spec["name"]

                    auth_type = tool_server_connection.get("auth_type", "bearer")
                    token = None

                    if auth_type == "bearer":
                        token = tool_server_connection.get("key", "")
                    elif auth_type == "session":
                        token = request.state.token.credentials

                    def make_tool_function(function_name, token, tool_server_data):
                        async def tool_function(**kwargs):
                            print(
                                f"Executing tool function {function_name} with params: {kwargs}"
                            )
                            return await execute_tool_server(
                                token=token,
                                url=tool_server_data["url"],
                                name=function_name,
                                params=kwargs,
                                server_data=tool_server_data,
                            )

                        return tool_function

                    tool_function = make_tool_function(
                        function_name, token, tool_server_data
                    )

                    callable = get_async_tool_function_and_apply_extra_params(
                        tool_function,
                        {},
                    )

                    tool_dict = {
                        "tool_id": tool_id,
                        "callable": callable,
                        "spec": spec,
                    }
                    # llens(#20): OpenAPI 由来 spec の contentMediaType から
                    # 表示ディスクリプタを抽出(型駆動レンダリング)
                    render = extract_render_from_spec(spec)
                    if render:
                        tool_dict["render"] = render

                    _add_tool(tools_dict, function_name, tool_dict)
            else:
                continue
        else:
            # llens(GHSA-4pcg-253r-rf9w / CVE-2026-45350 → #54 で強化): tool_ids は
            # チャット経路ではサーバー側解決(model.meta.toolIds — クライアント申告は
            # 廃止)。ここでの read ACL 検証は per-user のゲート(#66)として維持し、
            # 権限の無い tool は読み込まず無視する(admin / 所有者はバイパス)。
            # skill 由来の id は enforce_access_control=False で呼ばれる(#35 step2b)。
            if enforce_access_control and not (
                user.role == "admin"
                or tool.user_id == user.id
                or has_access(user.id, "read", tool.access_control)
            ):
                log.warning(f"User {user.id} lacks access to tool {tool_id}; skipping.")
                continue

            module = request.app.state.TOOLS.get(tool_id, None)
            if module is None:
                module, _ = load_tool_module_by_id(tool_id)
                request.app.state.TOOLS[tool_id] = module

            # Set valves for the tool
            if hasattr(module, "valves") and hasattr(module, "Valves"):
                valves = Tools.get_tool_valves_by_id(tool_id) or {}
                module.valves = module.Valves(**valves)

            # toolkit ごとに新しい extra_params / __user__ を作って束縛する。
            # 旧実装は呼び手の __user__ dict に UserValves を書き込み、partial がその
            # 参照を持ったままだったため、全 toolkit が最後に読んだ toolkit の
            # UserValves を見ていた。UserValves は撤去済み(2026-10-05、feature-triage
            # 「Filter / tool 機構の縮小」)— pipe 由来などの "valves" キーも落とす。
            tool_extra_params = {**extra_params, "__id__": tool_id}
            if isinstance(extra_params.get("__user__"), dict):
                tool_extra_params["__user__"] = {
                    k: v for k, v in extra_params["__user__"].items() if k != "valves"
                }

            # llens(#140): toolkit の汎用フック(TOOLKIT_HOOK_NAMES)。read ACL の後に
            # 評価する — ACL は「使えるか」、available は「このターンで意味があるか」
            # (subagent なら委任先 allowlist・depth・chat 文脈)。フックの例外は
            # available が fail-closed(載せない)、spec_overrides は静的 spec のまま。
            hook_model = _hook_model(extra_params)
            hook_metadata = extra_params.get("__metadata__") or {}
            available = getattr(module, "available", None)
            if inspect.isroutine(available):
                try:
                    if not available(request, user, hook_model, hook_metadata):
                        continue
                except Exception as e:
                    log.exception(f"tool {tool_id}: available() failed; skipping: {e}")
                    continue
            spec_overrides = {}
            overrides_hook = getattr(module, "spec_overrides", None)
            if inspect.isroutine(overrides_hook):
                try:
                    spec_overrides = (
                        overrides_hook(request, user, hook_model, hook_metadata) or {}
                    )
                except Exception as e:
                    log.exception(
                        f"tool {tool_id}: spec_overrides() failed; using static spec: {e}"
                    )
                    spec_overrides = {}

            # llens(#110 P4): toolkit 共通注記 — `Tools` **クラス docstring** を toolkit の
            # **先頭 spec の description に 1 回だけ**前置する。関数 docstring がモデル向け
            # (spec description)であるのと同じ規約をクラス docstring にも適用する —
            # tool ファイル内の docstring はモデルに見える文面、開発者メモは # コメント
            # (ken 決定 2026-08-16)。全関数に共通する注意(直列化・実行環境の性質等)を
            # 関数ごとに複製しないための口。文言は tool 作者(配備側)の持ち物。
            toolkit_note = type(module).__doc__
            toolkit_note = (
                inspect.cleandoc(toolkit_note).strip()
                if isinstance(toolkit_note, str) and toolkit_note.strip()
                else None
            )

            for spec in tool.specs:
                # TODO: Fix hack for OpenAI API
                # Some times breaks OpenAI but others don't. Leaving the comment
                for val in spec.get("parameters", {}).get("properties", {}).values():
                    # llens(#18-3): Optional 引数の spec は anyOf 形で "type" キーを
                    # 持たない(sandbox tool の timeout_s で KeyError になった実例)
                    if val.get("type") == "str":
                        val["type"] = "string"

                # Remove internal reserved parameters (e.g. __id__, __user__)
                spec["parameters"]["properties"] = {
                    key: val
                    for key, val in spec["parameters"]["properties"].items()
                    if not key.startswith("__")
                }

                # convert to function that takes only model params and inserts custom params
                function_name = spec["name"]
                tool_function = getattr(module, function_name)
                callable = get_async_tool_function_and_apply_extra_params(
                    tool_function, tool_extra_params
                )

                # TODO: Support Pydantic models as parameters
                # 2026-10-04: 切り出しは保存時 spec(get_tool_specs)と同じ helper —
                # ソースのインデントを落とす(毎ターン送る token の節約)。
                spec["description"] = (
                    tool_description_from_docstring(callable.__doc__) or function_name
                )

                # llens(#140): ターン依存の spec パッチ(docstring 由来の description の
                # 後・toolkit 注記の前置の前 — description の置き換えにも注記は付く)
                patch = spec_overrides.get(function_name)
                if isinstance(patch, dict):
                    _deep_merge_spec(spec, patch)

                if toolkit_note and spec is tool.specs[0]:
                    spec["description"] = (
                        f"【{tool.name} ツール群 共通】{toolkit_note.strip()}\n\n"
                        f"{spec['description']}"
                    )

                # llens(#20): 関数の Annotated 宣言(生きた型が一次情報)から表示
                # ディスクリプタを抽出し、spec にも contentMediaType として反映する
                # (DB 保存済み spec が宣言追加前に作られていても、モデルへ渡る spec
                # には実行時に必ず載る)
                render = extract_render_from_function(tool_function)
                apply_render_to_spec(spec, render)

                tool_dict = {
                    "tool_id": tool_id,
                    "callable": callable,
                    "spec": spec,
                    # Misc info
                    "metadata": {
                        "citation": hasattr(module, "citation") and module.citation,
                    },
                }
                if render:
                    tool_dict["render"] = render

                # llens(#110): 非同期実行(background 昇格)への opt-in(設計 D4)。
                # tool 作者が `Tools` クラス属性 `backgroundable` で宣言する
                # (tool CRUD は admin 専用 = admin の明示指定): True で全関数、
                # 関数名の list/tuple/set で個別指定。`serial` / `timeouts` と同じ
                # クラス属性の流儀で、per-entry キー(`timeout` と同型)へ落とす。
                # 衝突ガード(自前 background 引数・恒久不適格名)は呼び出し面
                # (utils/exec_tools.entry_is_backgroundable)が判定する。
                backgroundable = getattr(module, "backgroundable", None)
                if backgroundable is True or (
                    isinstance(backgroundable, (list, tuple, set, frozenset))
                    and function_name in backgroundable
                ):
                    tool_dict["backgroundable"] = True

                # llens(#110 P4): per-entry timeout の宣言(backgroundable と対称の
                # `Tools` クラス属性 `timeouts` — 関数名 → 秒の dict)。同期は
                # execute_tool_calls が TOOL_EXEC_TIMEOUT_S より優先し、
                # background は exec runner が ASYNC_EXEC_TIMEOUT_S より優先する
                # (builtin エントリの timeout キーと同じ意味)。長時間 tool
                # (STT の長尺音声等)が同期既定 300 秒で死ぬのを tool 側で救う口。
                # 非正値(0 / -1)は「システム側上限なし」(#140 — SUBAGENT_TURN_TIMEOUT_S
                # の -1 = 無効をクラス属性でも表せるように。execute_tool_calls は
                # `timeout > 0` のときだけ wait_for する)。
                timeouts = getattr(module, "timeouts", None)
                if isinstance(timeouts, dict):
                    timeout = timeouts.get(function_name)
                    if isinstance(timeout, (int, float)) and not isinstance(
                        timeout, bool
                    ):
                        tool_dict["timeout"] = timeout

                # 2026-10-04: 並行実行の opt-out。execute_tool_calls は 1 応答の
                # 同期 call を並行に走らせるが、`Tools` クラス属性 `serial = True`
                # の toolkit は toolkit(tool_id)単位で応答順に 1 本ずつ走る
                # (実行系が直列 / stateful で順序に意味がある tool — sandbox)。
                if getattr(module, "serial", False) is True:
                    tool_dict["serial"] = True

                _add_tool(tools_dict, function_name, tool_dict)

    return tools_dict


def _resolve_tool_server(request: Request, tool_id: str) -> tuple[dict, dict | None]:
    """`server:<idx>` → (接続設定, 取得済みサーバーデータ)。解決できなければ data=None。

    旧実装は idx の parse 失敗・範囲外・未取得を IndexError / assert で落としていた
    (preset に古い server:N が残っているだけでターン全体が 500)。
    """
    try:
        server_idx = int(tool_id.split(":", 1)[1])
        connection = request.app.state.config.TOOL_SERVER_CONNECTIONS[server_idx]
    except (ValueError, IndexError) as e:
        log.warning(f"Unknown tool server {tool_id}; skipping: {e}")
        return {}, None
    for server in request.app.state.TOOL_SERVERS or []:
        if server["idx"] == server_idx:
            return connection, server
    log.warning(f"Tool server {tool_id} is not loaded; skipping.")
    return connection, None


def _add_tool(tools_dict: dict, function_name: str, tool_dict: dict) -> None:
    """関数名の衝突は先勝ち(tool_ids の並び = preset の meta.toolIds 順)。

    捨てた側と残した側の両方をログに出す(旧実装は捨てた側しか出さなかった)。
    """
    kept = tools_dict.get(function_name)
    if kept is None:
        tools_dict[function_name] = tool_dict
        return
    log.warning(
        f"Tool name collision on '{function_name}': keeping "
        f"{kept['tool_id']}.{function_name} (earlier in tool_ids), discarding "
        f"{tool_dict['tool_id']}.{function_name}"
    )


def tool_description_from_docstring(docstring: str | None) -> str:
    """
    The model-facing function description: the docstring text before the first
    `:param` / `:return` (a plain text split — README_API §5.1), with the source
    indentation removed by `inspect.cleandoc` (relative indentation and line
    breaks are kept). Empty string when there is no docstring.

    llens(2026-10-04): turn-time (get_tools) and sync-time (get_tool_specs) specs
    both go through this, so the stored spec equals what the model gets. Before
    this the turn-time description carried the docstring's source indentation
    (8 spaces per line — tokens sent every turn), and the stored one was cut
    by a different (line-based, every line stripped) rule.
    """
    if not docstring or not docstring.strip():
        return ""
    head = re.split(":(param|return)", docstring, 1)[0]
    return inspect.cleandoc(head).strip()


def parse_description(docstring: str | None) -> str:
    """
    Parse a function's docstring to extract the description.

    Args:
        docstring (str): The docstring to parse.

    Returns:
        str: The description.
    """
    return tool_description_from_docstring(docstring)


def parse_docstring(docstring):
    """
    Parse a function's docstring to extract parameter descriptions in reST format.

    Args:
        docstring (str): The docstring to parse.

    Returns:
        dict: A dictionary where keys are parameter names and values are descriptions.
    """
    if not docstring:
        return {}

    # Regex to match `:param name: description` format
    param_pattern = re.compile(r":param (\w+):\s*(.+)")
    param_descriptions = {}

    for line in docstring.splitlines():
        match = param_pattern.match(line.strip())
        if not match:
            continue
        param_name, param_description = match.groups()
        if param_name.startswith("__"):
            continue
        param_descriptions[param_name] = param_description

    return param_descriptions


def convert_function_to_pydantic_model(func: Callable) -> type[BaseModel]:
    """
    Converts a Python function's type hints and docstring to a Pydantic model,
    including support for nested types, default values, and descriptions.

    Args:
        func: The function whose type hints and docstring should be converted.
        model_name: The name of the generated Pydantic model.

    Returns:
        A Pydantic model class.
    """
    # include_extras: Annotated の表示マーカー(RenderAs、#20)を落とさず pydantic に
    # 渡す(pydantic は未知メタデータを無視するのでバリデーション/スキーマは不変)
    type_hints = get_type_hints(func, include_extras=True)
    signature = inspect.signature(func)
    parameters = signature.parameters

    docstring = func.__doc__

    description = parse_description(docstring)
    function_descriptions = parse_docstring(docstring)

    field_defs = {}
    for name, param in parameters.items():

        type_hint = type_hints.get(name, Any)
        default_value = param.default if param.default is not param.empty else ...

        # llens(2026-10-04): 別名にする — v0.6.5 はここで関数の `description` を
        # 上書きしており、保存時 spec の description が「最後の引数の説明」になっていた
        param_description = function_descriptions.get(name, None)

        if param_description:
            field_defs[name] = type_hint, Field(
                default_value, description=param_description
            )
        else:
            field_defs[name] = type_hint, default_value

    model = create_model(func.__name__, **field_defs)
    model.__doc__ = description

    return model


def get_functions_from_tool(tool: object) -> list[Callable]:
    return [
        getattr(tool, func)
        for func in dir(tool)
        if callable(
            getattr(tool, func)
        )  # checks if the attribute is callable (a method or function).
        and not func.startswith(
            "_"
        )  # llens(#18-3): `_` 始まりは tool 作者のプライベートヘルパ(dunder 含む)として
        # モデルに露出させない。v0.6.5 は `__` のみ除外で、メソッド形のヘルパが全部
        # spec 化されてしまう(sandbox tool の _post/_exec が manifest に並んだ実例)。
        and not inspect.isclass(
            getattr(tool, func)
        )  # ensures that the callable is not a class itself, just a method or function.
        # llens(#140): get_tools の汎用フック(available / spec_overrides)は tool ではない
        and func not in TOOLKIT_HOOK_NAMES
    ]


def get_tool_specs(tool_module: object) -> list[dict]:
    specs = []
    for function in get_functions_from_tool(tool_module):
        spec = convert_pydantic_model_to_openai_function_spec(
            convert_function_to_pydantic_model(function)
        )
        # 保存時 spec の description はターン時(get_tools)と同じ規則・同じ fallback
        # (変換器が __doc__ をどう整形するかに依存しない)
        spec["description"] = (
            tool_description_from_docstring(function.__doc__) or function.__name__
        )
        # llens(#20): Annotated の表示宣言を標準 JSON Schema(contentMediaType)
        # として保存時 spec にも反映する(strip しない方針)
        apply_render_to_spec(spec, extract_render_from_function(function))
        specs.append(spec)

    return specs


def resolve_schema(schema, components):
    """
    Recursively resolves a JSON schema using OpenAPI components.
    """
    if not schema:
        return {}

    if "$ref" in schema:
        ref_path = schema["$ref"]
        ref_parts = ref_path.strip("#/").split("/")
        resolved = components
        for part in ref_parts[1:]:  # Skip the initial 'components'
            resolved = resolved.get(part, {})
        return resolve_schema(resolved, components)

    resolved_schema = copy.deepcopy(schema)

    # Recursively resolve inner schemas
    if "properties" in resolved_schema:
        for prop, prop_schema in resolved_schema["properties"].items():
            resolved_schema["properties"][prop] = resolve_schema(
                prop_schema, components
            )

    if "items" in resolved_schema:
        resolved_schema["items"] = resolve_schema(resolved_schema["items"], components)

    return resolved_schema


def convert_openapi_to_tool_payload(openapi_spec):
    """
    Converts an OpenAPI specification into a custom tool payload structure.

    Args:
        openapi_spec (dict): The OpenAPI specification as a Python dict.

    Returns:
        list: A list of tool payloads.
    """
    tool_payload = []

    for path, methods in openapi_spec.get("paths", {}).items():
        for method, operation in methods.items():
            tool = {
                "type": "function",
                "name": operation.get("operationId"),
                "description": operation.get(
                    "description", operation.get("summary", "No description available.")
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            }

            # Extract path and query parameters
            for param in operation.get("parameters", []):
                param_name = param["name"]
                param_schema = param.get("schema", {})
                tool["parameters"]["properties"][param_name] = {
                    "type": param_schema.get("type"),
                    "description": param_schema.get("description", ""),
                    # llens(#20): 型駆動レンダリングの宣言(contentMediaType)を
                    # query/path パラメータでも落とさない(body 側は wholesale コピー)
                    **(
                        {"contentMediaType": param_schema["contentMediaType"]}
                        if param_schema.get("contentMediaType")
                        else {}
                    ),
                }
                if param.get("required"):
                    tool["parameters"]["required"].append(param_name)

            # Extract and resolve requestBody if available
            request_body = operation.get("requestBody")
            if request_body:
                content = request_body.get("content", {})
                json_schema = content.get("application/json", {}).get("schema")
                if json_schema:
                    resolved_schema = resolve_schema(
                        json_schema, openapi_spec.get("components", {})
                    )

                    if resolved_schema.get("properties"):
                        tool["parameters"]["properties"].update(
                            resolved_schema["properties"]
                        )
                        if "required" in resolved_schema:
                            tool["parameters"]["required"] = list(
                                set(
                                    tool["parameters"]["required"]
                                    + resolved_schema["required"]
                                )
                            )
                    elif resolved_schema.get("type") == "array":
                        tool["parameters"] = resolved_schema  # special case for array

            tool_payload.append(tool)

    return tool_payload


async def get_tool_server_data(token: str, url: str) -> Dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    error = None
    try:
        timeout = aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    error_body = await response.json()
                    raise Exception(error_body)

                # Check if URL ends with .yaml or .yml to determine format
                if url.lower().endswith((".yaml", ".yml")):
                    text_content = await response.text()
                    res = yaml.safe_load(text_content)
                else:
                    res = await response.json()
    except Exception as err:
        log.exception(f"Could not fetch tool server spec from {url}")
        if isinstance(err, dict) and "detail" in err:
            error = err["detail"]
        else:
            error = str(err)
        raise Exception(error)

    data = {
        "openapi": res,
        "info": res.get("info", {}),
        "specs": convert_openapi_to_tool_payload(res),
    }

    print("Fetched data:", data)
    return data


async def get_tool_servers_data(
    servers: List[Dict[str, Any]], session_token: Optional[str] = None
) -> List[Dict[str, Any]]:
    # Prepare list of enabled servers along with their original index
    server_entries = []
    for idx, server in enumerate(servers):
        if server.get("config", {}).get("enable"):
            url_path = server.get("path", "openapi.json")
            full_url = f"{server.get('url')}/{url_path}"

            auth_type = server.get("auth_type", "bearer")
            token = None

            if auth_type == "bearer":
                token = server.get("key", "")
            elif auth_type == "session":
                token = session_token
            server_entries.append((idx, server, full_url, token))

    # Create async tasks to fetch data
    tasks = [get_tool_server_data(token, url) for (_, _, url, token) in server_entries]

    # Execute tasks concurrently
    responses = await asyncio.gather(*tasks, return_exceptions=True)

    # Build final results with index and server metadata
    results = []
    for (idx, server, url, _), response in zip(server_entries, responses):
        if isinstance(response, Exception):
            print(f"Failed to connect to {url} OpenAPI tool server")
            continue

        results.append(
            {
                "idx": idx,
                "url": server.get("url"),
                "openapi": response.get("openapi"),
                "info": response.get("info"),
                "specs": response.get("specs"),
            }
        )

    return results


async def execute_tool_server(
    token: str, url: str, name: str, params: Dict[str, Any], server_data: Dict[str, Any]
) -> Any:
    error = None
    try:
        openapi = server_data.get("openapi", {})
        paths = openapi.get("paths", {})

        matching_route = None
        for route_path, methods in paths.items():
            for http_method, operation in methods.items():
                if isinstance(operation, dict) and operation.get("operationId") == name:
                    matching_route = (route_path, methods)
                    break
            if matching_route:
                break

        if not matching_route:
            raise Exception(f"No matching route found for operationId: {name}")

        route_path, methods = matching_route

        method_entry = None
        for http_method, operation in methods.items():
            if operation.get("operationId") == name:
                method_entry = (http_method.lower(), operation)
                break

        if not method_entry:
            raise Exception(f"No matching method found for operationId: {name}")

        http_method, operation = method_entry

        path_params = {}
        query_params = {}
        body_params = None

        # path / query 以外の引数が body。header / cookie 宣言の引数も body には入れない。
        non_body_names = set()
        for param in operation.get("parameters", []):
            param_name = param["name"]
            param_in = param["in"]
            non_body_names.add(param_name)
            if param_name in params:
                if param_in == "path":
                    path_params[param_name] = params[param_name]
                elif param_in == "query":
                    query_params[param_name] = params[param_name]

        final_url = f"{url}{route_path}"
        for key, value in path_params.items():
            final_url = final_url.replace(f"{{{key}}}", str(value))

        if query_params:
            query_string = "&".join(f"{k}={v}" for k, v in query_params.items())
            final_url = f"{final_url}?{query_string}"

        # requestBody: 引数が 1 つでもあれば送る。無いときは required の body だけ
        # 空 object で送り、任意(required が無い / false)の body は省く。
        # 旧実装は任意 body でも引数ゼロを例外にしていた(引数を省けない)上、
        # path / query の引数まで body に重ねて送っていた。
        request_body = operation.get("requestBody") or {}
        if request_body.get("content"):
            body = {k: v for k, v in params.items() if k not in non_body_names}
            if body or request_body.get("required"):
                body_params = body

        headers = {"Content-Type": "application/json"}

        if token:
            headers["Authorization"] = f"Bearer {token}"

        async with aiohttp.ClientSession() as session:
            request_method = getattr(session, http_method.lower())

            if http_method in ["post", "put", "patch"]:
                async with request_method(
                    final_url, json=body_params, headers=headers
                ) as response:
                    if response.status >= 400:
                        text = await response.text()
                        raise Exception(f"HTTP error {response.status}: {text}")
                    return await response.json()
            else:
                async with request_method(final_url, headers=headers) as response:
                    if response.status >= 400:
                        text = await response.text()
                        raise Exception(f"HTTP error {response.status}: {text}")
                    return await response.json()

    except Exception as err:
        error = str(err)
        print("API Request Error:", error)
        return {"error": error}

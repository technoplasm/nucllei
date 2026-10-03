"""組み込みツール: サブエージェント `run_subagent`(issue #140、2026-10-03)。

#107 以来 middleware が擬似 tool としてリクエストごとに合成・直接注入していたものを、
Tools テーブルの builtin 行にした。理由は ACL: 擬似 tool は `get_tools` の外に居たので
tool 行の read ACL(AccessControlModal)も `meta.toolIds` の割り当ても効かず、
ユーザー/グループ単位で止める面が無かった(「機能として提供されるものは ACL が付けられない」
— ken 2026-09-06)。

二層:
- **使えるか** = この builtin 行の access_control + 親 preset の `meta.toolIds`
  (get_tools の per-user enforce)。旧 `meta.features.subagent` はここへ吸収して撤去
  (migration `e7b3c90d2f14`)
- **どこへ飛べるか** = 親 preset の `meta.subagentPresetIds` ∩ アクセス可 preset
  (`utils/subagent.py` — 変更なし)

ターン依存の部分は `get_tools` の汎用フックで持つ:
- `available`: allowlist 空 / アクセス可 preset ゼロ / chat 文脈なし / depth 超過なら載せない
- `spec_overrides`: preset_id の enum と description 末尾の preset 一覧(user × preset 依存)
sync 時の静的 spec(このファイルの docstring 由来)は enum 無しの汎用形で、
実行時の認可は enum に委ねず `run_subagent_turn` が同一述語で再評価する。

実行本体は `utils/subagent.py`(run_subagent_turn)。`utils.subagent` は
`utils.chat`(推論経路)を引くので、import は呼び出し時に遅延させる
(builtin_tools は utils.plugin から引かれる — 循環回避)。

クラス docstring はモデル向けの運用規律(get_tools が toolkit 先頭 spec に 1 回だけ
前置する)。開発者メモはこのモジュール docstring と # コメントに書く。
"""

import asyncio
import inspect
import re
from typing import Annotated, Optional, Union

from nucllei.env import SUBAGENT_MAX_CONCURRENCY, SUBAGENT_TURN_TIMEOUT_S
from nucllei.utils.tool_render import RenderAs


def _model_from(metadata: Optional[dict]):
    """親ターンの preset(model dict)。

    `__model__` はタスクモデル(get_task_model_id)に差し替えられうるので使わない —
    allowlist を持つのはチャット経路が metadata["model"] に載せる親 preset。
    """
    return (metadata or {}).get("model") or {}


class Tools:
    """サブエージェント = 別の context で 1 ターン働く子。返るのは最終回答だけで、子が読んだ資料や途中経過は自分の context に載らない。
    - 使う: 長大な資料・多数のページを読んで結論だけ欲しいとき / 互いに独立した調べものを並行させたいとき
    - 使わない: 自分で数手で済む作業、会話の流れに強く依存する判断(子はこの会話を見ていない)
    - instruction は単独で完結させる: 目的・対象(file_id / url / path 等は引数名を付けて literal のまま)・返してほしい形を書く
    - 並行させるなら 1 回の応答の中に tool call を並べる(同時に走り、全部そろってから次の手。1 本ずつ別の手で出すと直列になる)。待たずに別の作業を進めたいときだけ background: true(結果は await_task で回収)。同時 4 本まで・超過は即エラー — 5 本以上なら 4 本ずつ、先の分がそろってから次
    - 結果は子のまとめであって原文ではない。根拠が要る点・食い違いは自分で確かめる。追加の質問は session_id で同じ子に続ける
    """

    # #110(K2/D4): background 昇格への opt-in(get_tools が per-entry キーへ落とす)。
    # 共有 background 引数は middleware が spec のコピーへ注入する — ここで宣言すると
    # 恒久不適格の衝突ガードに掛かる。
    backgroundable = ("run_subagent",)

    # #107 決定 7: 子ターン全体(モデル呼び出しと tool 実行の反復)を包むので
    # TOOL_EXEC_TIMEOUT_S より長い。-1 等の非正値は「システム側上限なし」
    # (get_tools が非正値も per-entry timeout として通す)。
    timeouts = {"run_subagent": SUBAGENT_TURN_TIMEOUT_S}

    # ---- get_tools の汎用フック(#140)— spec 化されない予約名 ----

    def available(self, request, user, model, metadata) -> bool:
        from nucllei.utils.subagent import subagent_available

        return subagent_available(model, user, metadata)

    def spec_overrides(self, request, user, model, metadata) -> dict:
        from nucllei.utils.subagent import build_spec_overrides

        return build_spec_overrides(model, user, _base_description())

    # ---- tool ----

    async def run_subagent(
        self,
        *,
        preset_id: str = "",
        instruction: str,
        session_id: str = "",
        __request__=None,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
    ) -> Annotated[Union[dict, str], RenderAs(kind="subagent")]:
        """サブエージェント(agent session)を起動し、instruction を 1 ターン実行させる。返るのは最終回答(answer)のみ — 実行過程(transcript)は親には来ない。会話を継続するには、結果の session_id を session_id 引数に渡して追いの instruction を送る。

        :param preset_id: 起動する preset の id。新規セッション時は必須(session_id 継続時は不要)。
        :param instruction: サブエージェントへの指示。
        :param session_id: 継続する既存 agent session の id(省略で新規セッション)。
        :return: {ok, session_id, answer, truncated?, usage?, error?}
        """
        from nucllei.models.users import Users
        from nucllei.utils.subagent import _error, run_subagent_turn

        user_id = (__user__ or {}).get("id")
        # 子ターンの 3 段(process_chat_payload 等)は UserModel を要求する —
        # __user__ は dict 射影なので行から引き直す
        user = (
            await asyncio.to_thread(Users.get_user_by_id, user_id) if user_id else None
        )
        if user is None:
            return _error("ユーザー文脈が無い(__user__ 未注入)。")

        metadata = __metadata__ or {}
        return await run_subagent_turn(
            __request__,
            _model_from(metadata),
            user,
            metadata.get("chat_id"),
            metadata.get("message_id"),
            preset_id=preset_id,
            instruction=instruction,
            session_id=session_id,
        )


def _base_description() -> str:
    """run_subagent の静的説明文(関数 docstring の :param 前)— spec_overrides が
    preset 一覧を足す土台。get_tools の docstring 切り出しと同じ規則で取る。"""
    doc = Tools.run_subagent.__doc__ or ""
    return inspect.cleandoc(re.split(":(param|return)", doc, 1)[0]).strip()


# 同時数上限の文言は docstring(モデル向け)に数値で書いている。env で変えた配備で
# 食い違わないよう、既定値以外ならクラス docstring の数値を差し替える。
if SUBAGENT_MAX_CONCURRENCY != 4:
    Tools.__doc__ = (Tools.__doc__ or "").replace(
        "同時 4 本まで・超過は即エラー — 5 本以上なら 4 本ずつ",
        f"同時 {SUBAGENT_MAX_CONCURRENCY} 本まで・超過は即エラー — "
        f"{SUBAGENT_MAX_CONCURRENCY + 1} 本以上なら {SUBAGENT_MAX_CONCURRENCY} 本ずつ",
    )

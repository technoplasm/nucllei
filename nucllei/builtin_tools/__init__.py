"""組み込みツール(issue #42)。

リポジトリのコードで定義され、Tools テーブルに builtin 行として起動時 sync される。
カスタム tool と同列(toolIds picker / skill の tool_ids / access_control / 4pcg ガードが
そのまま効く)で、違いは content が DB でなく repo モジュールから来ること
(_memos/builtin-tools.md 1c: 境界基準は「出荷単位」)。

- 一覧 UI は組み込み/カスタムをセクション分けして見せる(meta.builtin で判別)
- content 更新・削除は拒否(access_control の編集のみ可)。定義の真実源は常に repo
- 同名関数の衝突は先勝ち discard(#18-3 の教訓)— builtin の関数名は他とぶつけない

登録は 2 種類: **常に登録**(history / chat_files / attachment_text / context_budget /
feed_image / fetch_url / subagent — nucllei 自身の DB・Storage・共有 resolver・推論経路
だけで動く)と、
**env ゲート付き**(sandbox は SANDBOX_URL)。ゲートの理屈は 1 つ: 外部サービスが無いと**どの呼び出しも必ず失敗する** builtin を
一覧に生やさない(builtin の性質 = 常に動く、に反する)。feed_image / fetch_url は配備層
(prism-hu/llens)から移設した(#141、2026-10-03)。fetch_url は同日中に env ゲート
(当時の EXT_UPSTREAMS / EXT_PUBLIC_BASES / EXT_INTRANET_CIDRS のいずれか — 同日に設定ごと
撤去)を外して常に登録に
した — `url=` はスキーム付きの http(s) URL ならどこでも取りに行く(ken 決定)ので、env が
空でも「必ず失敗する」ことは無い。
"""

import logging

from nucllei.env import SANDBOX_TOKEN, SANDBOX_URL
from nucllei.builtin_tools import (
    attachment_text,
    chat_files,
    context_budget,
    feed_image,
    fetch_url,
    history,
    sandbox,
    subagent,
)

log = logging.getLogger(__name__)

# builtin tool の id 名前空間。この prefix はユーザー作成 tool から予約する
# (create で拒否 / sync で未定義の builtin 行を prune する基準)。
BUILTIN_ID_PREFIX = "builtin_"

# tool_id → 定義。id は BUILTIN_ID_PREFIX 始まりでユーザー作成 tool と衝突させない
BUILTIN_TOOLS = {
    "builtin_history": {
        "module": history,
        "name": "History Management",
        "description": (
            "List, read and search your own chat history "
            "(list_history / read_history / grep_history). Owner-only"
        ),
    },
    "builtin_chat_files": {
        "module": chat_files,
        "name": "Chat Files",
        "description": (
            "List and search file attachments issued in the current chat "
            "(list_chat_files / grep_chat_files). Metadata only, owner-only"
        ),
    },
    "builtin_attachment_text": {
        "module": attachment_text,
        "name": "Attachment Text",
        "description": (
            "Read the extracted text of an attachment by file_id "
            "(read_attachment). Owner-only"
        ),
    },
    # context_budget は常に登録: nucllei 自身がモデル呼び出しごとに記録する実測 usage
    # (chat.meta.usage)を読むだけで外部依存ゼロ。llens の tools/context_budget.py から
    # 移設(2026-10-03、ken 決定)— 記録が呼び出しごとになった(#102)ので、tool ループの
    # 途中で呼べば今ターンの最新値が読める。
    "builtin_context_budget": {
        "module": context_budget,
        "name": "Context Budget",
        "description": (
            "Report the current chat's measured context usage and remaining budget "
            "(get_context_budget), from the engine's usage of the latest model call. "
            "Owner-only"
        ),
    },
    # feed_image は常に登録: file_id 経路は nucllei 自身の Files / Storage だけで動き、
    # url / path 経路は共有 resolver(utils/image_refs)が解決する(配信元の無い配備でも
    # file_id の添付 PDF を見る用途は残る)。
    "builtin_feed_image": {
        "module": feed_image,
        "name": "Feed Image",
        "description": (
            "Show an image or PDF pages to the model's own eyes (VLM) in the same turn, "
            "by file_id, URL or sandbox path (feed_image). Issues no File; owner-only"
        ),
    },
    # fetch_url も常に登録(2026-10-03、ken 決定): url= はスキーム付きの http(s) URL なら
    # どこでも取りに行く(届くかは配備のネットワーク次第。取りに行かないのはこのサーバ自身
    # だけ)ので、EXT_MOUNTS が空でも「必ず失敗する」builtin ではない。同日の朝まで当時の
    # EXT_UPSTREAMS / EXT_PUBLIC_BASES / EXT_INTRANET_CIDRS のいずれかでゲートしていたが、行き先で機能を
    # 絞らない決定と一緒に外した。
    "builtin_fetch_url": {
        "module": fetch_url,
        "name": "Fetch URL",
        "description": (
            "Fetch one web page by URL (any http(s) URL reachable from this deployment, "
            "or a same-server /ext/ URL) as plain text with a numbered link list "
            "(fetch_url). PDFs return their text layer"
        ),
    },
    # subagent も常に登録(#140、2026-10-03): 子ターンは nucllei 自身の推論経路で回る。
    # 旧 meta.features.subagent は「この行を preset の toolIds に入れるか」に吸収した。
    # 委任先(meta.subagentPresetIds)が空・アクセス可 preset ゼロ・depth 超過のターンは
    # Tools.available が落とすので、割り当てても「呼ぶと必ず失敗する」ことは無い。
    "builtin_subagent": {
        "module": subagent,
        "name": "Subagent",
        "description": (
            "Delegate a task to a subagent (agent session) running one of the presets "
            "listed in the model's subagent presets, and get back only its final answer "
            "(run_subagent). Owner-only sessions"
        ),
    },
}

# sandbox だけ env ゲート付き。他の 7 本は nucllei 自身の DB / Storage / 共有 resolver で動き
# 外部依存ゼロ = どの配備でも必ず動くのに対し、sandbox は外部サービス
# (sandbox コンテナ) が要る。居ない配備で一覧に生やすと「見えているのに呼ぶと
# 必ずエラー」になり、builtin の性質(常に動く)に反する。
# 向き先 env(SANDBOX_URL)が設定されている配備でだけ BUILTIN_TOOLS に載せ、
# 未設定なら定義そのものが無い扱いにする — 撤去時の後片付けは下の prune
# (`builtin_` prefix かつ BUILTIN_TOOLS 未定義の行を削除)がそのまま担う。
# 帰結として **env を外すと access_control 設定は失われる**(行ごと消えるため)。
# 再設定は運用の手間だが、専用の無効化状態を DB に持つより素直と判断した
# (2026-09-03)。
#
# **共有トークン(SANDBOX_TOKEN)はゲートに含めない**(#132、2026-09-05 に
# 2026-09-04 の AND ゲートから戻した): sandbox 側の認証が opt-in
# (トークン未設定なら認証なしで動く)になったため、「URL はあるがトークンが無い」配備は
# 401 で必ず失敗する状態ではなく**普通に動く状態**になった。ゲートは向き先 env
# (`SANDBOX_URL`)ただ一つに戻す。設定漏れに気付けるよう、URL はあるのにトークンが
# 無いケースだけ sync 時に warning を出す(下の sync_builtin_tools)。
if SANDBOX_URL:
    BUILTIN_TOOLS["builtin_sandbox"] = {
        "module": sandbox,
        "name": "Sandbox",
        "description": (
            "Run Python / shell in a server-side isolated environment (nsjail) "
            "with a persistent /workspace "
            "(run_python / run_python_once / run_shell / send_to_workspace / "
            "write_file / read_file / list_workspace / fetch_from_workspace)"
        ),
    }

# 「URL はあるのにトークンが無い」= sandbox に**認証なしで**繋ぐ配備。動きはするが、
# 意図した状態かは配備者にしか分からないので warning を 1 行出す。import 時点ではまだ
# logging の設定が済んでいない可能性があるので、フラグだけ立てて sync_builtin_tools()
# で吐く(起動ログに必ず載る)。
_SANDBOX_TOKEN_MISSING = bool(SANDBOX_URL) and not SANDBOX_TOKEN


def is_builtin_tool_id(tool_id: str) -> bool:
    """現に登録されている builtin か(update/delete 保護・ロード分岐用)。"""
    return tool_id in BUILTIN_TOOLS


def is_reserved_builtin_id(tool_id: str) -> bool:
    """builtin 用に予約された id 名前空間か(create 拒否用)。
    未定義でも `builtin_` prefix は一律予約 — ユーザーに shadow させない。"""
    return tool_id.startswith(BUILTIN_ID_PREFIX)


def load_builtin_tool(tool_id: str):
    """plugin.load_tool_module_by_id と同じ外形(Tools インスタンス, frontmatter)。"""
    return BUILTIN_TOOLS[tool_id]["module"].Tools(), {}


def sync_builtin_tools() -> None:
    """起動時に builtin 行を Tools テーブルへ同期する(upsert + prune)。

    - 定義済み: specs / name / description を毎回 repo から再生成(コード更新の反映)。
      access_control は admin の運用設定なので既存値を保持する。
    - 未定義: `builtin_` prefix かつ BUILTIN_TOOLS に居ない行は DB から削除する。
      定義の真実源は常に repo なので orphan を残さない(builtin を削除したら次回起動で
      DB からも消える)。**env ゲートが閉じた builtin もここに落ちる** —
      builtin_sandbox は SANDBOX_URL を外して再起動すると同じ経路で消える。
    """
    if _SANDBOX_TOKEN_MISSING:
        log.warning(
            "builtin_sandbox は認証なしで sandbox に接続します: SANDBOX_URL は "
            "設定されていますが SANDBOX_TOKEN が未設定です。sandbox コンテナと同じ値を "
            "nucllei にも SANDBOX_TOKEN として渡すと共有トークン認証が有効になります"
        )

    # 循環 import 回避(utils.tools → utils.plugin → builtin_tools)のため遅延 import
    from nucllei.models.tools import ToolForm, ToolMeta, Tools
    from nucllei.utils.tools import get_tool_specs

    # 不変条件: 全 builtin id は予約 prefix を持つ。破れると prune(prefix 基準)が
    # 撤去時に orphan を掃除できなくなる(#89 review nit)。開発時に早期検知する。
    _bad = [k for k in BUILTIN_TOOLS if not k.startswith(BUILTIN_ID_PREFIX)]
    if _bad:
        raise ValueError(
            f"builtin id は '{BUILTIN_ID_PREFIX}' 始まりが必須(prune 前提): {_bad}"
        )

    for tool_id, entry in BUILTIN_TOOLS.items():
        try:
            specs = get_tool_specs(entry["module"].Tools())
            existing = Tools.get_tool_by_id(tool_id)
            if existing is None:
                Tools.insert_new_tool(
                    # 所有者は実ユーザーでない固定値(操作権は admin ロールで担保)
                    "system",
                    ToolForm(
                        id=tool_id,
                        name=entry["name"],
                        description=entry["description"],
                        content="",
                        meta=ToolMeta(builtin=True),
                        access_control=None,  # 既定 public read(露出は紐づけで制御)
                    ),
                    specs,
                )
                log.info(f"builtin tool registered: {tool_id}")
            else:
                Tools.update_tool_by_id(
                    tool_id,
                    {
                        "name": entry["name"],
                        "description": entry["description"],
                        "content": "",
                        "specs": specs,
                        "meta": {"builtin": True},
                    },
                )
        except Exception as e:
            log.error(f"builtin tool sync failed: {tool_id}: {e!r}")

    # 現定義に無い builtin 行を prune。予約 prefix を持つのに BUILTIN_TOOLS に居ない
    # ものは DB に残しておく理由が無い(旧 builtin_view_image 等の orphan 掃除)。
    # 理由は 2 通りあり、**ログでは区別できない**: コード側で撤去された(定義ごと消えた)か、
    # env ゲートが閉じた(builtin_sandbox の SANDBOX_URL 未設定)か。後者は配備の設定変更で
    # 日常的に起こりうるので、「repo から消えた」と断定する文言にはしない。
    try:
        for tool in Tools.get_tools():
            if tool.id.startswith(BUILTIN_ID_PREFIX) and tool.id not in BUILTIN_TOOLS:
                Tools.delete_tool_by_id(tool.id)
                log.info(f"builtin tool pruned (not defined in this build): {tool.id}")
    except Exception as e:
        log.error(f"builtin tool prune failed: {e!r}")

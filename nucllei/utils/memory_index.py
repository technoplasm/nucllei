"""会話の前置きの変数 {{USER_MEMORY_INDEX}} — 利用者の記憶の目次。

目次のファイル (env `USER_MEMORY_INDEX_PATH`、既定 `/workspace/memory/MEMORY.md`。nucllei
自身には記憶の決まりが無いので、置き場は設定で変えられる・空で無効) の中身を sandbox から読み、見出しと枠を付けて
返す。読むのは前置き (utils/conversation_context) を組むときだけ (task.fetch_context_sections が {{USER_RECIPES}} と
並べて取る)。

- 無い・空 → 空文字列 (変数ごと消える)。sandbox の無い配備も空。sandbox が応答しない・読めない → 1 行で知らせる
  ({{USER_RECIPES}} と同じ扱い)
- 枠: 見出し (何の目次かと置き場) + `<memory-index>` … `</memory-index>`。中身から枠を
  閉じたり偽の枠を作ったりできないよう、枠の名前 (この枠と、前置き・更新・`<source>` の枠) に見える箇所の
  `<` だけを `&lt;` にする (utils/frames)。制御文字・向きを変える文字は `\\uXXXX` の見える形に。それ以外は
  1 字も書き換えない (edit_file の old_text に目次の行をそのまま写せるように)
- 長さ: 4,000 字まで (行の切れ目で切る)。残りがあれば枠の外に「(残り N 行は read_file("<path>") で)」
"""

from __future__ import annotations

import base64
import logging
import re

from nucllei.utils.frames import defuse_line

log = logging.getLogger(__name__)

MEMORY_INDEX_MAX_CHARS = 4000
# 目次のファイルを読む上限 (これより大きければ残りの行数は数えず「残りは」と言う)
MEMORY_INDEX_READ_BYTES = 256 * 1024
INDEX_TIMEOUT_S = 5
MEMORY_INDEX_UNREACHABLE = "(記憶の目次を取れない: sandbox が応答しない)"

_CONTROL = re.compile("[\x00-\x08\x0b-\x1f\x7f\u202a-\u202e\u2066-\u2069]")


def memory_index_path() -> str:
    """目次のファイルの絶対パス (`/workspace/…` だけ。それ以外・空は無効 = "")。"""
    from nucllei.env import USER_MEMORY_INDEX_PATH

    path = (USER_MEMORY_INDEX_PATH or "").strip()
    if not path.startswith("/workspace/"):
        return ""
    return path


def _defuse_line(line: str) -> str:
    return _CONTROL.sub(
        lambda m: f"\\u{ord(m.group()):04x}", defuse_line(line, memory_index=True)
    )


def render_memory_index(path: str, text: str, truncated_file: bool = False) -> str:
    """目次の中身を見出し + 枠にする (4,000 字まで。空なら "")。`truncated_file` は読む上限で切れたか。"""
    text = text.lstrip("\ufeff")
    if not text.strip():
        return ""
    # 改行は \n だけで切る (read_file・edit_file と同じ数え方。行末の \r は見せない)
    lines = [ln[:-1] if ln.endswith("\r") else ln for ln in text.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    shown: list[str] = []
    used = 0
    for line in lines:
        cost = len(line) + 1
        if used + cost > MEMORY_INDEX_MAX_CHARS:
            break
        shown.append(line)
        used += cost
    cut_line = False
    if not shown:
        # 1 行目だけで上限を超える — 文字で切る (その行の残りも read_file で)
        shown = [lines[0][: MEMORY_INDEX_MAX_CHARS - 1] + "…"]
        cut_line = True
    rest = len(lines) - len(shown) + (1 if cut_line else 0)
    body = "\n".join(_defuse_line(ln) for ln in shown)
    out = (
        f"利用者の記憶の目次 ({path} の中身):\n"
        f"<memory-index>\n{body}\n</memory-index>"
    )
    if truncated_file:
        out += f'\n(残りは read_file("{path}") で)'
    elif rest > 0:
        out += f'\n(残り {rest} 行は read_file("{path}") で)'
    return out


async def user_memory_index_text(user) -> str:
    """{{USER_MEMORY_INDEX}} の中身 (見出し + 枠、無ければ "")。sandbox に 1 回聞く。"""
    from nucllei.env import SANDBOX_URL

    path = memory_index_path()
    if not SANDBOX_URL or not path:
        return ""
    uid = user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
    if not uid:
        return ""
    from nucllei.builtin_tools.sandbox import SandboxHTTPError, sandbox_post

    try:
        res = await sandbox_post(
            "/files/read",
            {
                "user_id": uid,
                "path": path[len("/workspace/") :],
                "nofollow": True,
                "offset": 0,
                "length": MEMORY_INDEX_READ_BYTES,
            },
            timeout_s=INDEX_TIMEOUT_S,
        )
        data = base64.b64decode(res["b64"])[:MEMORY_INDEX_READ_BYTES]
    except SandboxHTTPError as e:
        if e.status in (400, 404):
            return ""  # 無い・ファイルでない (まだ記憶が無い)
        log.warning(f"[memory] {path} を読めない: {e!r}")
        return MEMORY_INDEX_UNREACHABLE
    except Exception as e:
        log.warning(f"[memory] {path} を読めない: {e!r}")
        return MEMORY_INDEX_UNREACHABLE
    size = res.get("size") if isinstance(res.get("size"), int) else len(data)
    truncated = size > len(data)
    if truncated:
        # 途中で切れた最後の行は捨てる (文字の途中で切らない)
        data = data[: data.rfind(b"\n") + 1] or data
    from nucllei.utils.recipes import decode_text

    decoded = decode_text(data)  # UTF-8 (BOM 可) → cp932。バイナリは入れない
    if decoded is None:
        return ""
    return render_memory_index(path, decoded[0], truncated_file=truncated)

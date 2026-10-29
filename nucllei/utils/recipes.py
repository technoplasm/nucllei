"""レシピ (利用者が残す手順) の判定 — 置き場・名前・frontmatter・on_load・患者データ・層 (2026-10-30)。

llens 依頼・ken 指示「ぜんぶ nuc にやらせて」で、llens の jail のコマンド `/ext/bin/recipe` (list / check /
promote) が持っていた判定をここへ移した。**判定の実装はここ 1 つ**で、使う側は 3 つ:

- builtin の `save_recipe` / `edit_recipe` (書く前の検査 = `check_folder`。個人の層も含む)
- 一覧 `{{USER_RECIPES}}` と `load_recipe` (軽い検査 = `assess_recipe`。「壊れている」は保存せず、
  毎回その場で判定する — 参照先のファイルを戻せばレシピ側は何もしなくても直る)
- ホストの lint `nucllei recipe-check` (共有の層 `/ext/groups` `/ext/public` だけ。個人の層は見ない)

I/O はしない (フォルダの中身・参照先の有無・skill の可視は呼び出し側が渡す)。nucllei の `env.py` も
import しない (CLI がサーバの設定無しで動くように)。配備層の知識は env で受ける:

- `RECIPE_RESTRICTED_TOOLS` — 使える人が限られる関数の表 (JSON object: `{"query_emr": "EMR",
  "monitoring_now": null}`。値はその関数を置いてよいグループ名、`null` は個人だけ)。既定は空
- `RECIPE_MEMO_KEYS` — 付属の先頭に残る「由来メモ」の行 (`-- <key>:`) のキー (カンマ区切り。例
  `executed_at,csv,params`)。患者番号が入る行として止める。既定は空

層と置き場 (jail のパス = URL の `/ext/…` と同じ文字列):

    個人      /workspace                 -> /workspace/recipes/<名前>/RECIPE.md
    グループ  /ext/groups/<グループ名>   -> /ext/groups/<グループ名>/recipes/<名前>/RECIPE.md
    全体      /ext/public                -> /ext/public/recipes/<名前>/RECIPE.md
"""

from __future__ import annotations

import datetime as dt
import io
import json
import logging
import os
import posixpath
import re
import shlex
import unicodedata
import zipfile
from dataclasses import dataclass, field
from typing import Optional

import yaml

log = logging.getLogger(__name__)

WORKSPACE = "/workspace"
EXT = "/ext"
GROUPS = EXT + "/groups"
PUBLIC = EXT + "/public"
RECIPES = "recipes"
RECIPE_MD = "RECIPE.md"
TRASH = "trash"

REQUIRED_KEYS = ("title", "description", "author", "updated")
NAME_MAX_BYTES = 200
# 一覧の description の上限 (1 行に畳んでから)
INDEX_DESCRIPTION_CHARS = 120

# 壊れたレシピの次の手 (一覧の印・load_recipe の ERROR で同じ文面 — ken 2026-10-30)
BROKEN_NEXT = "edit_recipe で直す (丸ごと書き直すなら save_recipe で上書き)"


# ---------------------------------------------------------------------------
# 配備層の設定 (env)
# ---------------------------------------------------------------------------


def restricted_tools() -> dict[str, Optional[str]]:
    """使える人が限られる関数 -> 置いてよいグループ (None は個人だけ)。env `RECIPE_RESTRICTED_TOOLS`。"""
    raw = os.environ.get("RECIPE_RESTRICTED_TOOLS", "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        log.warning("RECIPE_RESTRICTED_TOOLS が JSON として読めないので空として扱う")
        return {}
    if not isinstance(value, dict):
        log.warning("RECIPE_RESTRICTED_TOOLS が JSON object でないので空として扱う")
        return {}
    return {
        str(k): (v if isinstance(v, str) and v else None)
        for k, v in value.items()
        if isinstance(k, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k)
    }


def memo_keys() -> tuple[str, ...]:
    """由来メモの行のキー。env `RECIPE_MEMO_KEYS` (カンマ区切り)。"""
    raw = os.environ.get("RECIPE_MEMO_KEYS", "")
    return tuple(
        k.strip()
        for k in raw.split(",")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k.strip())
    )


# ---------------------------------------------------------------------------
# 層とパス
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Layer:
    kind: str  # "personal" | "group" | "public"
    path: str  # 層の jail のパス
    group: str = ""

    @property
    def recipes(self) -> str:
        return f"{self.path}/{RECIPES}"

    @property
    def trash(self) -> str:
        return f"{self.path}/{TRASH}"

    @property
    def shared(self) -> bool:
        return self.kind != "personal"

    @property
    def label(self) -> str:
        return {"personal": "個人", "public": "全体"}.get(
            self.kind
        ) or f"グループ「{self.group}」"

    @property
    def audience(self) -> str:
        return {
            "personal": "本人だけが見られる",
            "public": "全員が見られる。直せるのは管理者だけ",
        }.get(self.kind) or f"グループ「{self.group}」のメンバー全員が見て直せる"


PERSONAL = Layer("personal", WORKSPACE)
PUBLIC_LAYER = Layer("public", PUBLIC)


def group_layer(group: str) -> Layer:
    return Layer("group", f"{GROUPS}/{group}", group)


BAD_CATEGORIES = {"Cc", "Cf", "Cs", "Zl", "Zp"}


def bad_segment(n: str) -> Optional[str]:
    """パスの 1 セグメントとして使えない理由 (使えるなら None)。"""
    if not n or n.strip() != n:
        return "空か、前後に空白がある"
    if "/" in n or "\\" in n:
        return "/ を含む"
    if n.startswith("."):
        return ". で始まる"
    if any(unicodedata.category(c) in BAD_CATEGORIES for c in n):
        return "制御文字・書式文字を含む"
    if len(n.encode("utf-8", errors="surrogatepass")) > NAME_MAX_BYTES:
        return f"長すぎる (UTF-8 で {NAME_MAX_BYTES} バイトまで)"
    return None


def name_problem(n: str) -> Optional[str]:
    """レシピのフォルダ名の規則に合わない理由。`trash` はゴミ箱 (`<層>/trash/`) と紛れるので断る。"""
    why = bad_segment(n)
    if why:
        return why
    if unicodedata.normalize("NFC", n) != n:
        return "NFC でない"
    if n == TRASH:
        return "trash はゴミ箱 (<層>/trash/) と紛れる"
    return None


def layer_of_place(place: str) -> Optional[Layer]:
    """層の jail のパス (`/workspace` / `/ext/groups/<g>` / `/ext/public`) から層。"""
    place = place.rstrip("/")
    if place == WORKSPACE:
        return PERSONAL
    if place == PUBLIC:
        return PUBLIC_LAYER
    if place.startswith(GROUPS + "/"):
        g = place[len(GROUPS) + 1 :]
        if bad_segment(g) is None:
            return group_layer(g)
    return None


class RecipePathError(ValueError):
    """レシピのパスとして読めない。str は事実の文、`next` は次の手。"""

    def __init__(self, message: str, next_step: str):
        super().__init__(message)
        self.next = next_step


PATH_FORM = "`<層>/recipes/<名前>` の絶対パス (層は /workspace・/ext/groups/<グループ名>・/ext/public) にする"


@dataclass(frozen=True)
class RecipeRef:
    layer: Layer
    name: str

    @property
    def folder(self) -> str:
        return f"{self.layer.recipes}/{self.name}"

    @property
    def md(self) -> str:
        return f"{self.folder}/{RECIPE_MD}"


def parse_recipe_path(path: str) -> RecipeRef:
    """`<層>/recipes/<名前>` (と `…/RECIPE.md`) を読む。名前の規則も見る。"""
    if not isinstance(path, str) or not path.strip():
        raise RecipePathError("path が空", PATH_FORM + "。")
    p = unicodedata.normalize("NFC", path.strip())
    if not p.startswith("/"):
        raise RecipePathError(f"{path!r} は絶対パスでない", PATH_FORM + "。")
    p = p.rstrip("/")
    if p.endswith("/" + RECIPE_MD):
        p = p[: -len(RECIPE_MD) - 1]
    if any(seg in (".", "..") for seg in p.split("/")):
        raise RecipePathError(f"{path!r} に . や .. がある", PATH_FORM + "。")
    parent, _, name = p.rpartition("/")
    if not parent.endswith("/" + RECIPES):
        raise RecipePathError(
            f"{path!r} はレシピのフォルダ (`<層>/recipes/<名前>`) の形でない",
            PATH_FORM + "。",
        )
    layer = layer_of_place(parent[: -len(RECIPES) - 1])
    if layer is None:
        raise RecipePathError(f"{path!r} の層が分からない", PATH_FORM + "。")
    why = name_problem(name)
    if why:
        raise RecipePathError(
            f"レシピの名前に使えない ({why}): {name!r}",
            "内容が分かる短い語 (日本語可) の名前にする。",
        )
    return RecipeRef(layer, name)


def is_recipe_md_path(path: str) -> bool:
    """`<層>/recipes/<名前>/RECIPE.md` そのものか (write_file / edit_file の抜け道を塞ぐ判定)。"""
    p = posixpath.normpath(unicodedata.normalize("NFC", path or "").strip())
    if not p.endswith("/" + RECIPE_MD):
        return False
    try:
        parse_recipe_path(p)
    except RecipePathError:
        # 名前の規則外でも、置き場の形ならレシピの本体として扱う
        parent = p[: -len(RECIPE_MD) - 1].rpartition("/")[0]
        return parent.endswith("/" + RECIPES) and (
            layer_of_place(parent[: -len(RECIPES) - 1]) is not None
        )
    return True


# ---------------------------------------------------------------------------
# テキスト
# ---------------------------------------------------------------------------

TEXT_SNIFF = 8192
RE_OOXML_PART = re.compile(
    r"^(word/document\.xml|xl/sharedStrings\.xml|xl/worksheets/[^/]+\.xml)$"
)


def decode_text(data: bytes) -> Optional[tuple[str, str]]:
    """テキストなら (str, encoding)、バイナリなら None。UTF-8 → CP932 の順。"""
    if b"\0" in data[:TEXT_SNIFF]:
        return None
    for enc in ("utf-8-sig", "cp932"):
        try:
            return data.decode(enc), ("utf-8" if enc == "utf-8-sig" else enc)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace"), "utf-8"


def office_text(data: bytes) -> Optional[str]:
    """docx / xlsx なら本文 (タグを除いたもの)。それ以外は None。"""
    if not data.startswith(b"PK"):
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            parts = [n for n in z.namelist() if RE_OOXML_PART.match(n)]
            if not parts:
                return None
            out = []
            for n in sorted(parts):
                if z.getinfo(n).file_size > 50_000_000:
                    continue
                xml = z.read(n).decode("utf-8", errors="replace")
                xml = re.sub(r"</(?:w:p|c|si|row)>", "\n", xml)
                out.append(re.sub(r"<[^>]+>", "", xml))
            return "\n".join(out)
    except (zipfile.BadZipFile, OSError, KeyError, RuntimeError):
        return None


def file_text(data: bytes) -> Optional[str]:
    r = decode_text(data)
    if r is not None:
        return r[0]
    return office_text(data)


# ---------------------------------------------------------------------------
# 患者データの目安 (単純な正規表現。取りこぼしも誤検出もある前提)。行は NFKC にしてから見る
# ---------------------------------------------------------------------------

RE_ID = re.compile(r"(?<![0-9])[0-9]{9,10}(?![0-9])")
_A = r"(?<![A-Za-z0-9_])"
_Z = r"(?![A-Za-z0-9_])"
RE_ID_LABELED = re.compile(
    rf"(?:患者\s*(?:番号|ID)|カルテ\s*番号|診察券\s*番号|ID\s*番号|{_A}pid{_Z}|{_A}patient_?id{_Z}|{_A}ID{_Z})"
    r"""\s*["'」]?\s*[:：=＝は]?\s*["'「]?\s*([0-9]{5,})""",
    re.I,
)
_MD = r"(?:0?[1-9]|1[0-2])"
_DD = r"(?:0?[1-9]|[12][0-9]|3[01])"
RE_DATES = [
    re.compile(rf"(?<!\d)(?:19|20)\d{{2}}[-/]{_MD}[-/]{_DD}(?!\d)"),
    re.compile(rf"(?<![\d.])(?:19|20)\d{{2}}\.{_MD}\.{_DD}(?![\d.])"),
    re.compile(r"(?<!\d)(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?!\d)"),
    re.compile(rf"(?<!\d)(?:19|20)\d{{2}}\s*年\s*{_MD}\s*月\s*{_DD}\s*日"),
    re.compile(
        rf"(?:令和|平成|昭和|[RHS])\s*(?:元|\d{{1,2}})\s*年\s*{_MD}\s*月\s*{_DD}\s*日"
    ),
    re.compile(rf"(?<![A-Za-z0-9])[RHS]\d{{1,2}}\.{_MD}\.{_DD}(?![\d.])"),
]
RE_NAME = re.compile(r"([一-鿿々]{2,4})(様|さん|氏|殿)")
NAME_STOP = {
    "患者", "患者家族", "家族", "ご家族", "御家族", "利用者", "皆", "皆様", "各位", "担当者",
    "主治医", "医師", "先生", "看護師", "本人", "ご本人", "御本人", "相手", "相手方", "依頼者",
    "受診者", "対象者", "申請者", "入院患者", "外来患者", "お客", "当事者", "同", "同氏",
}  # fmt: skip
NAME_STOP_TAIL = tuple("長医局課部科室")
STOP_KINDS = ("id", "name", "memo")
NOTE_KINDS = ("date",)


def mask(s: str) -> str:
    """マッチ部分を一部だけ残して伏せる (出力が人目やログに残っても値が戻らないように)。"""
    n = len(s)
    if n <= 3:
        return "*" * n
    keep_head = 2 if n >= 6 else 1
    return s[:keep_head] + "*" * (n - keep_head - 1) + s[-1]


def mask_name(s: str, honorific: str) -> str:
    return s[0] + "*" * (len(s) - 1) + honorific


def scan_line(text: str) -> list[tuple[str, str]]:
    """1 行から (種別, 伏せ字) を拾う (NFKC にしてから)。日付の 8 桁を ID と二重に出さない。"""
    text = unicodedata.normalize("NFKC", text)
    hits: list[tuple[str, str]] = []
    spans: list[tuple[int, int]] = []

    def taken(a: int) -> bool:
        return any(x <= a < y for x, y in spans)

    for rx in RE_DATES:
        for m in rx.finditer(text):
            if taken(m.start()):
                continue
            spans.append(m.span())
            hits.append(("date", mask(m.group(0))))
    for m in RE_ID_LABELED.finditer(text):
        if taken(m.start(1)):
            continue
        spans.append(m.span(1))
        hits.append(("id", mask(m.group(1))))
    for m in RE_ID.finditer(text):
        if taken(m.start()):
            continue
        hits.append(("id", mask(m.group(0))))
    for m in RE_NAME.finditer(text):
        word, hon = m.group(1), m.group(2)
        if word.endswith(NAME_STOP_TAIL) or any(
            word == w or (len(w) >= 2 and word.endswith(w)) for w in NAME_STOP
        ):
            continue
        hits.append(("name", mask_name(word, hon)))
    return hits


RE_FM = re.compile(r"\A﻿?---\s*\n(.*?)\n---\s*(?:\n|$)", re.S)


def scan_text(text: str, skip_updated: bool = False) -> list[tuple[int, str, str]]:
    """テキスト全体から (行番号, 種別, 伏せ字)。種別は date / id / name / memo。
    `skip_updated`: frontmatter の `updated:` 行は見ない (日付は updated にだけ書く約束)。"""
    fm_end = 0
    if skip_updated:
        m = RE_FM.search(text)
        fm_end = text[: m.end()].count("\n") if m else 0
    keys = memo_keys()
    re_memo = (
        re.compile(r"^\s*--\s*(" + "|".join(map(re.escape, keys)) + r")\s*:")
        if keys
        else None
    )
    out: list[tuple[int, str, str]] = []
    for i, line in enumerate(text.splitlines(), 1):
        if skip_updated and i <= fm_end and re.match(r"^\s*updated\s*:", line):
            continue
        if re_memo is not None:
            mm = re_memo.match(line)
            if mm:
                out.append(
                    (i, "memo", f"-- {mm.group(1)}: の行 (由来メモ。患者番号が入る)")
                )
                continue
        for kind, masked in scan_line(line):
            out.append((i, kind, masked))
    return out


def scan_file(rel: str, data: bytes) -> Optional[list[tuple[int, str, str]]]:
    """1 ファイルの患者データの検査。中身を検査できない (バイナリ) なら None。
    `.md` は frontmatter の `updated:` 行を見ない。"""
    t = file_text(data)
    if t is None:
        return None
    return scan_text(t, skip_updated=rel.endswith(".md"))


# ---------------------------------------------------------------------------
# frontmatter
# ---------------------------------------------------------------------------


def _scalar(v) -> Optional[str]:
    """frontmatter の値を文字列に (YAML が日付・数値に読んだものも)。リスト・マッピングは None。"""
    if v is None:
        return ""
    if isinstance(v, dt.datetime):
        return v.isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (str, int, float)):
        return str(v)
    return None


@dataclass
class Frontmatter:
    data: dict  # YAML として読んだもの
    body: str  # frontmatter の後ろ
    end_line: int  # frontmatter の最後の行 (`---`) の行番号

    def get(self, key: str) -> str:
        v = _scalar(self.data.get(key))
        return (v or "").strip()


def read_frontmatter(text: str) -> tuple[Optional[Frontmatter], Optional[str]]:
    """(frontmatter, 読めない理由)。読めたら理由は None。"""
    m = RE_FM.search(text)
    if not m:
        if text.lstrip("﻿").startswith("---"):
            return None, "frontmatter が閉じていない (2 つ目の --- の行が無い)"
        return None, "frontmatter (--- で囲んだ先頭ブロック) が無い"
    block = m.group(1)
    lines = block.splitlines()
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None) or getattr(e, "context_mark", None)
        if mark is not None and 0 <= mark.line < len(lines):
            return None, (
                f"frontmatter が YAML として読めない ({mark.line + 2} 行目: {lines[mark.line]!r})"
            )
        return None, "frontmatter が YAML として読めない"
    if not isinstance(data, dict):
        return None, "frontmatter が YAML のマッピング (key: value の並び) でない"
    return Frontmatter(data, text[m.end() :], text[: m.end()].count("\n")), None


def _yaml_str(s: str) -> str:
    """1 行の YAML のスカラー (必要ならクォート)。"""
    out = yaml.safe_dump(
        s, allow_unicode=True, default_flow_style=True, width=1_000_000
    ).strip()
    if out.endswith("\n..."):
        out = out[: -len("\n...")]
    if out.endswith("..."):
        out = out[: -len("...")].rstrip()
    return out


def build_recipe_md(
    *,
    title: str,
    description: str,
    author: str,
    updated: dt.date,
    body: str,
    tools: Optional[list[str]] = None,
    on_load: Optional[list] = None,
) -> str:
    """RECIPE.md を組み立てる (frontmatter は tool が作る — 壊れた md を作らせない)。"""
    lines = [
        "---",
        f"title: {_yaml_str(' '.join(title.split()))}",
        f"description: {_yaml_str(' '.join(description.split()))}",
        f"author: {_yaml_str(' '.join(author.split()))}",
        f"updated: {updated.isoformat()}",
    ]
    if tools:
        lines.append(f"tools: {_yaml_str(', '.join(tools))}")
    if on_load:
        dumped = yaml.safe_dump(
            {"on_load": on_load},
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
            width=1_000_000,
        ).rstrip("\n")
        lines.extend(dumped.splitlines())
    lines.append("---")
    text = "\n".join(lines) + "\n"
    body = body.strip("\n")
    if body:
        text += "\n" + body + "\n"
    return text


def set_updated(text: str, d: dt.date) -> str:
    """frontmatter の `updated` を `d` にする (無ければ足す)。frontmatter が無ければそのまま。"""
    m = RE_FM.search(text)
    if not m:
        return text
    block = m.group(1)
    if re.search(r"^updated\s*:", block, re.M):
        block = re.sub(
            r"^updated\s*:.*$", f"updated: {d.isoformat()}", block, count=1, flags=re.M
        )
    else:
        block = block + f"\nupdated: {d.isoformat()}"
    return text[: m.start(1)] + block + text[m.end(1) :]


def plain(v) -> str:
    """利用者が書いた frontmatter の値を一覧の 1 行に出す形にする。改行を潰し、tool の文が
    モデルへの指示に使う語 (`返答への指示:` `次の手:` `中止:`) は区切りを変える。"""
    t = " ".join(str(v).split()) if v else ""
    return re.sub(r"(返答への指示|次の手|中止|ERROR)\s*[:：]", r"\1 -", t)


# ---------------------------------------------------------------------------
# on_load (レシピは短い記法だけ。skill の on_load の API も skill / recipe / read を受ける)
# ---------------------------------------------------------------------------

ON_LOAD_KINDS = ("skill", "recipe", "read", "shell", "python")
ON_LOAD_FORM = (
    "on_load は `- skill: <id>` `- recipe: <レシピのフォルダの絶対パス>` `- read: <パス>` "
    "`- shell: <付属のファイル> [引数…]` `- python: <付属の .py> [引数…]` の並び"
)


@dataclass
class OnLoadItem:
    """レシピの on_load の 1 要素 (短い記法を読んだもの)。`tool` / `args` は実行する形。"""

    kind: str
    tool: str
    args: dict
    ref_path: Optional[str] = None  # read / recipe の参照先 (絶対パス)
    skill_id: Optional[str] = None  # skill
    attachment: Optional[str] = None  # shell / python の付属のファイル名 (フォルダ直下)


def _has_control(s: str) -> bool:
    return any(unicodedata.category(c) in BAD_CATEGORIES for c in s)


def _resolve_in_folder(folder: str, rel: str) -> Optional[str]:
    """レシピのフォルダ基準の相対パス (`..` で外へ出ない) を絶対パスに。出るなら None。"""
    if rel.startswith("/") or not rel:
        return None
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return folder + "/" + "/".join(parts)


def _attachment_command(
    kind: str, value: str, folder: str
) -> tuple[str, str, list[str]]:
    """shell / python の値を (付属のファイル名, run_shell の command, 引数) に。不正なら ValueError。"""
    if _has_control(value):
        raise ValueError("制御文字を含む")
    try:
        words = shlex.split(value)
    except ValueError as e:
        raise ValueError(f"引数が読めない ({e})")
    if not words:
        raise ValueError("付属のファイル名が無い")
    head, args = words[0], words[1:]
    if head.startswith("./"):
        head = head[2:]
    if "/" in head or head in ("", ".", "..") or head.startswith("."):
        raise ValueError(
            f"実行できるのはこのレシピのフォルダの直下の付属だけ (相対のファイル名で書く): {words[0]!r}"
        )
    if head == RECIPE_MD:
        raise ValueError("RECIPE.md は実行しない")
    if kind == "python" and not head.endswith(".py"):
        raise ValueError(f"python で実行できるのは .py の付属だけ: {head!r}")
    runner = "bash" if kind == "shell" else "python3"
    command = " ".join(
        [f"cd {shlex.quote(folder)}", "&&", runner, shlex.quote("./" + head)]
        + [shlex.quote(a) for a in args]
    )
    return head, command, args


def parse_recipe_on_load(value, folder: str) -> tuple[list[OnLoadItem], list[str]]:
    """レシピの frontmatter の on_load を読む。(要素, 読めない理由)。読めない要素は落とす。"""
    if value is None or value == "":
        return [], []
    if not isinstance(value, list):
        return [], [f"{ON_LOAD_FORM} (リスト) で書く"]
    items: list[OnLoadItem] = []
    errors: list[str] = []
    for n, entry in enumerate(value):
        where = f"on_load の {n + 1} 番目"
        if not isinstance(entry, dict) or len(entry) != 1:
            errors.append(f"{where} は `- <種類>: <値>` の 1 行で書く")
            continue
        ((kind, raw),) = entry.items()
        if kind not in ON_LOAD_KINDS:
            errors.append(
                f"{where} の {kind!r} は使えない ({', '.join(ON_LOAD_KINDS)} のどれか)"
            )
            continue
        if not isinstance(raw, str) or not raw.strip():
            errors.append(f"{where} ({kind}) の値が空か文字列でない")
            continue
        raw = unicodedata.normalize("NFC", raw.strip())
        if kind == "skill":
            sid = raw.lstrip("/").strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]+", sid):
                errors.append(f"{where} (skill) の id が読めない: {raw!r}")
                continue
            items.append(
                OnLoadItem("skill", "load_skill", {"skill_id": sid}, skill_id=sid)
            )
        elif kind == "recipe":
            if not raw.startswith("/"):
                errors.append(
                    f"{where} (recipe) はレシピのフォルダの絶対パスで書く: {raw!r}"
                )
                continue
            try:
                ref = parse_recipe_path(raw)
            except RecipePathError as e:
                errors.append(f"{where} (recipe): {e}")
                continue
            if ref.folder == folder:
                errors.append(f"{where} (recipe) に自分自身は書けない")
                continue
            items.append(
                OnLoadItem(
                    "recipe", "load_recipe", {"path": ref.folder}, ref_path=ref.md
                )
            )
        elif kind == "read":
            if raw.startswith("/"):
                target = posixpath.normpath(raw)
                if ".." in raw.split("/"):
                    errors.append(f"{where} (read) に .. は書けない: {raw!r}")
                    continue
            else:
                target = _resolve_in_folder(folder, raw)
                if target is None:
                    errors.append(
                        f"{where} (read) の相対パスがこのレシピのフォルダの外を指す: {raw!r}"
                    )
                    continue
            if _has_control(target):
                errors.append(f"{where} (read) に制御文字がある")
                continue
            items.append(
                OnLoadItem("read", "read_file", {"path": target}, ref_path=target)
            )
        else:
            try:
                head, command, _ = _attachment_command(kind, raw, folder)
            except ValueError as e:
                errors.append(f"{where} ({kind}): {e}")
                continue
            items.append(
                OnLoadItem(kind, "run_shell", {"command": command}, attachment=head)
            )
    return items, errors


def normalize_skill_on_load_shorthand(entry):
    """skill の on_load の 1 要素の短い記法を正規形 `{tool, args}` にする (それ以外はそのまま)。
    skill の `recipe` / `read` は絶対パスだけ。`shell` / `python` は付属の概念が無いので ValueError。"""
    if not isinstance(entry, dict) or "tool" in entry or len(entry) != 1:
        return entry
    ((kind, raw),) = entry.items()
    if kind not in ON_LOAD_KINDS:
        return entry
    if kind in ("shell", "python"):
        raise ValueError(
            f"skill の on_load では {kind}: は使えない (付属のファイルが無い)。"
            '{"tool": "run_shell", "args": {"command": …}} と書く (管理者のみ)'
        )
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"on_load の {kind}: の値が空か文字列でない")
    raw = raw.strip()
    if kind == "skill":
        return {"tool": "load_skill", "args": {"skill_id": raw}}
    if not raw.startswith("/"):
        raise ValueError(f"skill の on_load の {kind}: は絶対パスで書く: {raw!r}")
    if kind == "recipe":
        try:
            ref = parse_recipe_path(raw)
        except RecipePathError as e:
            raise ValueError(f"on_load の recipe: {e}")
        return {"tool": "load_recipe", "args": {"path": ref.folder}}
    return {"tool": "read_file", "args": {"path": posixpath.normpath(raw)}}


# ---------------------------------------------------------------------------
# 軽い検査 (一覧と load_recipe — 「壊れている」の判定。保存しない、毎回その場で)
# ---------------------------------------------------------------------------


@dataclass
class RecipeRefs:
    """存在を確かめたい参照先 (呼び出し側がまとめて 1 回で調べる)。"""

    paths: set = field(default_factory=set)  # sandbox で stat する絶対パス
    skills: set = field(default_factory=set)  # 可視を DB で見る skill id


def recipe_refs(md_text: Optional[str], folder: str, refs: RecipeRefs) -> None:
    """RECIPE.md の on_load の参照先を `refs` に足す (読めなければ何もしない)。"""
    if md_text is None:
        return
    fm, problem = read_frontmatter(md_text)
    if fm is None:
        return
    items, _ = parse_recipe_on_load(fm.data.get("on_load"), folder)
    for item in items:
        if item.ref_path:
            refs.paths.add(item.ref_path)
        if item.skill_id:
            refs.skills.add(item.skill_id)


def assess_recipe(
    folder: str,
    md: Optional[dict],
    entries: Optional[dict],
    path_types: dict,
    visible_skills: set,
) -> list[str]:
    """レシピが壊れている理由 (壊れていなければ [])。一覧と load_recipe が同じこれを通る。

    - `md`: `{"type": "file"|"symlink"|…, "text": str|None}` (RECIPE.md が無ければ None)
    - `entries`: フォルダ直下の {名前: 種類} (shell / python の付属の有無。分からなければ None)
    - `path_types`: 参照先の絶対パス -> 種類 ("file" / "dir" / "symlink" / None = 無い)。
      キーが無いパスは「分からない」(sandbox に聞けなかった) で、壊れているとは言わない
    - `visible_skills`: この利用者に見える skill id

    見るのは置き場の形と frontmatter と on_load の参照先だけ (患者データ・層の判定は書く前の
    `check_folder` — 一覧は毎リクエスト走るので軽くする)。"""
    if md is None:
        return ["RECIPE.md が無い"]
    if md.get("type") != "file":
        return [f"RECIPE.md が普通のファイルでない ({md.get('type')})"]
    text = md.get("text")
    if text is None:
        return ["RECIPE.md がテキストでない"]
    fm, problem = read_frontmatter(text)
    if fm is None:
        return [problem]
    reasons = []
    missing = [k for k in REQUIRED_KEYS if not fm.get(k)]
    if missing:
        reasons.append("frontmatter の必須キーが無い・空: " + ", ".join(missing))
    items, errors = parse_recipe_on_load(fm.data.get("on_load"), folder)
    reasons += errors
    for item in items:
        if item.kind == "skill":
            if item.skill_id not in visible_skills:
                reasons.append(
                    f"on_load の skill {item.skill_id} が使えない (見えないか、無い)"
                )
        elif item.kind == "recipe":
            if item.ref_path in path_types and path_types[item.ref_path] != "file":
                reasons.append(f"on_load の recipe {item.args['path']} が無い")
        elif item.kind == "read":
            if item.ref_path not in path_types:
                continue
            kind = path_types[item.ref_path]
            if kind is None:
                reasons.append(f"read の {item.ref_path} が無い")
            elif kind != "file":
                reasons.append(
                    f"read の {item.ref_path} が普通のファイルでない ({kind})"
                )
        else:
            kind = (
                (entries or {}).get(item.attachment) if entries is not None else "file"
            )
            if kind is None:
                reasons.append(f"{item.kind} の付属 {item.attachment} がフォルダに無い")
            elif kind != "file":
                reasons.append(
                    f"{item.kind} の付属 {item.attachment} が普通のファイルでない ({kind})"
                )
    return reasons


def broken_line(reasons: list[str]) -> str:
    """一覧の印の中身 (`(壊れている: …)`)。理由は 2 つまで。"""
    shown = "、".join(reasons[:2]) + (
        f" ほか {len(reasons) - 2} 件" if len(reasons) > 2 else ""
    )
    return f"(壊れている: {shown}。{BROKEN_NEXT})"


def index_line(folder: str, description: str) -> str:
    d = plain(description)
    if len(d) > INDEX_DESCRIPTION_CHARS:
        d = d[: INDEX_DESCRIPTION_CHARS - 1] + "…"
    return f"- {folder} — {d or '(description なし)'}"


# ---------------------------------------------------------------------------
# 書く前の検査 (save_recipe / edit_recipe、ホストの lint)
# ---------------------------------------------------------------------------

BASIC_TOOLS = (
    "run_shell",
    "read_file",
    "write_file",
    "edit_file",
    "list_workspace",
    "load_skill",
    "load_recipe",
)


def fn_pattern(name: str) -> re.Pattern:
    """関数名の境界は ASCII の識別子だけで切る (和文と続けて書いても拾う)。"""
    return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])")


_PATH_END = r"(?=/|$|\.(?![\w])|[^\w.\-])"
# 行き先の人には見えないパス (個人の置き場の別の場所・相対の recipes/)
RE_PRIVATE_PATH = re.compile(
    r"(?:/workspace|~|\$HOME|\$\{HOME\})/(?:recipes|imports|exports|trash|memory)(?:/|"
    + _PATH_END
    + ")"
    r"|(?<![\w/.~}$-])recipes/"
)

FIX_PATIENT = (
    "患者の氏名・番号・検査値は {患者番号} {CRP} のような記号に、SQL の条件はバインド変数に置き換え、"
    "由来メモの行は消す (患者データでない語が引っかかったら言い換える)"
)
FIX_META = "frontmatter の値を直す"
FIX_ON_LOAD = ON_LOAD_FORM + "で直す (shell / python はこのフォルダの直下の付属だけ)"
FIX_LAYER = (
    "その層を見る全員が使える形に直す — 使える人が限られる関数を使うレシピはその関数を使える層に置き、"
    "付属はレシピのフォルダに入れて相対のファイル名で指す (個人の置き場の別の場所・相対の recipes/ は指さない)"
)
FIX_LINKS = "symlink・普通のファイルでないもの・入れ子の RECIPE.md をフォルダから外す (置き場では使わない)"


@dataclass
class CheckResult:
    stops: list = field(default_factory=list)  # 止める所見
    notes: list = field(default_factory=list)  # 注意 (止めない: 具体的な年月日)
    unchecked: list = field(default_factory=list)  # 中身を検査できなかった (バイナリ)
    fixes: list = field(default_factory=list)  # 直し方 (「次の手」)

    @property
    def ok(self) -> bool:
        return not self.stops


def _tools_list(v) -> Optional[list[str]]:
    if isinstance(v, list):
        return [str(x).strip() for x in v]
    s = _scalar(v)
    if s is None:
        return None
    return [x.strip() for x in s.split(",")] if s.strip() else []


def meta_findings(
    text: str, today: dt.date, folder: str, entries: Optional[dict] = None
) -> list[tuple[str, str]]:
    """RECIPE.md の frontmatter の崩れ。[(種別 meta|on_load, 文)]。"""
    fm, problem = read_frontmatter(text)
    if fm is None:
        return [("meta", problem)]
    out: list[tuple[str, str]] = []
    missing = [k for k in REQUIRED_KEYS if not fm.get(k)]
    if missing:
        out.append(("meta", "必須キーが無い・空: " + ", ".join(missing)))
    for k in ("title", "description", "author"):
        if k in fm.data and _scalar(fm.data[k]) is None:
            out.append(("meta", f"{k} は 1 行の文字列で書く"))
    upd = fm.get("updated")
    if upd:
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", upd):
                raise ValueError
            d = dt.date.fromisoformat(upd)
        except ValueError:
            out.append(("meta", f"updated が YYYY-MM-DD でない: {upd!r}"))
        else:
            if d > today:
                out.append(("meta", f"updated が未来: {upd}"))
    if "tool_ids" in fm.data:
        out.append(
            ("meta", "tool_ids は使わない。tools に変える (関数名のカンマ区切り)")
        )
    if "tools" in fm.data:
        names = _tools_list(fm.data["tools"])
        if names is None or not all(
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", x) for x in names
        ):
            out.append(
                ("meta", "tools は関数名のカンマ区切り (例 `grep_doc, read_doc`)")
            )
        else:
            unused = [x for x in names if not fn_pattern(x).search(fm.body)]
            if unused:
                out.append(
                    (
                        "meta",
                        "tools にあるのに本文が呼んでいない: "
                        + ", ".join(unused)
                        + " (本文で呼んだ関数だけを書く)",
                    )
                )
    items, errors = parse_recipe_on_load(fm.data.get("on_load"), folder)
    out += [("on_load", e) for e in errors]
    for item in items:
        if item.attachment and entries is not None:
            kind = entries.get(item.attachment)
            if kind is None:
                out.append(
                    (
                        "on_load",
                        f"{item.kind} の付属 {item.attachment} がフォルダに無い",
                    )
                )
            elif kind != "file":
                out.append(
                    (
                        "on_load",
                        f"{item.kind} の付属 {item.attachment} が普通のファイルでない",
                    )
                )
    return out


def restricted_used(files: dict[str, bytes]) -> dict[str, Optional[str]]:
    table = restricted_tools()
    used: dict[str, Optional[str]] = {}
    if not table:
        return used
    for data in files.values():
        r = decode_text(data)
        if r is None:
            continue
        for fn, group in table.items():
            if fn_pattern(fn).search(r[0]):
                used[fn] = group
    return used


def misplaced_restricted(
    files: dict[str, bytes], layer: Layer
) -> dict[str, Optional[str]]:
    """共有の層 `layer` に置くと使えない人が出る関数 -> 置いてよいグループ (None は個人だけ)。"""
    if not layer.shared:
        return {}
    return {
        fn: g
        for fn, g in restricted_used(files).items()
        if g is None or layer.path != f"{GROUPS}/{g}"
    }


def private_paths(files: dict[str, bytes]) -> list[tuple[str, int, str]]:
    """(相対パス, 行番号, 当たった部分) — 行き先の人には見えないパス。"""
    hits = []
    for rel, data in sorted(files.items()):
        r = decode_text(data)
        if r is None:
            continue
        for i, line in enumerate(r[0].splitlines(), 1):
            m = RE_PRIVATE_PATH.search(line)
            if m:
                hits.append((rel, i, m.group(0)))
    return hits


def check_folder(
    ref: RecipeRef,
    files: dict[str, bytes],
    skipped: Optional[dict[str, str]] = None,
    *,
    today: dt.date,
    md_mtime: Optional[dt.date] = None,
) -> CheckResult:
    """書いた後のフォルダ全体の検査 (save_recipe / edit_recipe が書く前に、lint が置いてあるものに)。

    見るもの: RECIPE.md が直下に 1 つ (入れ子の RECIPE.md は不可)、frontmatter (閉じている・YAML・
    必須キー・updated・tools)、on_load の形と付属の有無、患者データらしきもの (番号・氏名・由来メモ。
    年月日は注意で止めない)、symlink・普通のファイルでないもの、共有の層なら使える人が限られる関数と
    行き先の人に見えないパス。`md_mtime` (lint) を渡すと `updated` が最後に書いた日より前かも見る。"""
    res = CheckResult()
    entries = {rel: "file" for rel in files if "/" not in rel}
    for rel, why in (skipped or {}).items():
        if "/" not in rel:
            entries[rel] = why
    md = files.get(RECIPE_MD)
    if md is None:
        res.stops.append("RECIPE.md が無い")
    else:
        r = decode_text(md)
        if r is None:
            res.stops.append("RECIPE.md がテキストでない")
        else:
            for _kind, msg in meta_findings(r[0], today, ref.folder, entries):
                res.stops.append(f"RECIPE.md: {msg}")
            if md_mtime is not None:
                fm, _ = read_frontmatter(r[0])
                upd = fm.get("updated") if fm else ""
                try:
                    d = (
                        dt.date.fromisoformat(upd)
                        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", upd)
                        else None
                    )
                except ValueError:
                    d = None
                if d is not None and md_mtime > d and d != today:
                    res.stops.append(
                        f"RECIPE.md: updated ({upd}) が最後に書いた日 ({md_mtime}) より前"
                    )
    for rel in sorted(files):
        if rel.endswith("/" + RECIPE_MD):
            res.stops.append(
                f"{rel}: RECIPE.md はフォルダの直下に置く (下の階層には置かない)"
            )
    for rel, why in sorted((skipped or {}).items()):
        label = "symlink" if why == "symlink" else "普通のファイルでない"
        res.stops.append(f"{rel}: {label} (置き場では使わない)")
    for rel, data in sorted(files.items()):
        hits = scan_file(rel, data)
        if hits is None:
            res.unchecked.append(rel)
            continue
        for line, kind, masked in hits:
            if kind in NOTE_KINDS:
                res.notes.append(
                    f"{rel}:{line}: 具体的な年月日らしきもの: {masked} — 患者の日付なら {{入院日}} の"
                    "ような記号に (改定日・発行日ならそのまま)"
                )
                continue
            label = "由来メモ" if kind == "memo" else f"患者データらしきもの ({kind})"
            res.stops.append(f"{rel}:{line}: {label}: {masked}")
    if ref.layer.shared:
        for fn, group in misplaced_restricted(files, ref.layer).items():
            where = f"個人か {GROUPS}/{group}/" if group else "個人"
            res.stops.append(
                f"本文が {fn} を使うので{ref.layer.label}には置けない ({fn} を使うレシピは{where}だけ。"
                "その層を見る全員が使えるとは限らない)"
            )
        for rel, i, m in private_paths(files):
            res.stops.append(f"{rel}:{i}: 行き先の人には見えないパス: {m}…")
    ps = res.stops
    if any("患者データ" in p or "由来メモ" in p for p in ps):
        res.fixes.append(FIX_PATIENT)
    if any(
        p.startswith("RECIPE.md: on_load") or "on_load の" in p or "の付属" in p
        for p in ps
    ):
        res.fixes.append(FIX_ON_LOAD)
    if any(
        p.startswith("RECIPE.md: ") and "on_load" not in p and "付属" not in p
        for p in ps
    ):
        res.fixes.append(FIX_META)
    if any("置けない" in p or "見えないパス" in p for p in ps):
        res.fixes.append(FIX_LAYER)
    if skipped or any("直下に置く" in p for p in ps):
        res.fixes.append(FIX_LINKS)
    return res


def unchecked_marks(unchecked: list[str]) -> list[str]:
    return [
        f"※ {rel}: バイナリなので中身を検査していない。患者データが写っていないか、ユーザーに確かめてもらう"
        for rel in unchecked
    ]


# ---------------------------------------------------------------------------
# ホストの lint (`nucllei recipe-check`)。共有の層だけ (個人の /workspace は見ない)
# ---------------------------------------------------------------------------


def walk_local_folder(real: str) -> tuple[dict[str, bytes], dict[str, str]]:
    """ホストのフォルダ (symlink を辿らない) を (files, skipped) に。"""
    files: dict[str, bytes] = {}
    skipped: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(real, followlinks=False):
        rel_dir = os.path.relpath(dirpath, real)
        for name in sorted(dirnames):
            if os.path.islink(os.path.join(dirpath, name)):
                skipped[os.path.normpath(os.path.join(rel_dir, name))] = "symlink"
        dirnames[:] = sorted(
            d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))
        )
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.normpath(os.path.join(rel_dir, name))
            if os.path.islink(full):
                skipped[rel] = "symlink"
            elif not os.path.isfile(full):
                skipped[rel] = "other"
            else:
                with open(full, "rb") as f:
                    files[rel] = f.read()
    return files, skipped


def lint_targets(path: str, ext_root: str) -> list[tuple[str, str]]:
    """lint する (jail のパス, ホストのパス)。`path` は jail のパスで、`/ext`・`/ext/groups`・
    `/ext/groups/<g>`・`/ext/public` (層ごと)・レシピのフォルダのどれでも。"""

    def real(p: str) -> str:
        return ext_root + p[len(EXT) :]

    p = path.rstrip("/") or "/"
    if p == WORKSPACE or p.startswith(WORKSPACE + "/"):
        raise RecipePathError(
            f"{path} は個人の層で、lint の対象外",
            "共有の層 (/ext/groups・/ext/public) を指定する。",
        )
    if not (p == EXT or p.startswith(EXT + "/")):
        raise RecipePathError(
            f"{path} は /ext の下のパスでない", "/ext の下の jail のパスを指定する。"
        )
    layers: list[str] = []
    if p == EXT:
        layers = [PUBLIC] + _local_groups(real(GROUPS))
    elif p == GROUPS:
        layers = _local_groups(real(GROUPS))
    elif layer_of_place(p) is not None:
        layers = [p]
    else:
        try:
            ref = parse_recipe_path(p)
        except RecipePathError:
            # 規則外の名前のフォルダも lint では見る (名前の所見として出す)
            return [(p, real(p))]
        return [(ref.folder, real(ref.folder))]
    out = []
    for lp in layers:
        d = real(f"{lp}/{RECIPES}")
        if not os.path.isdir(d) or os.path.islink(d):
            continue
        for name in sorted(os.listdir(d)):
            if name.startswith("."):
                continue
            out.append((f"{lp}/{RECIPES}/{name}", os.path.join(d, name)))
    return out


def _local_groups(real_groups: str) -> list[str]:
    if not os.path.isdir(real_groups):
        return []
    return [
        f"{GROUPS}/{g}"
        for g in sorted(os.listdir(real_groups))
        if not g.startswith(".") and os.path.isdir(os.path.join(real_groups, g))
    ]


def lint_folder(jail_path: str, real_path: str, today: dt.date) -> list[str]:
    """1 つのレシピのフォルダの lint の所見 (1 行ずつ)。止める所見は `stop`、注意は `note`、
    検査できなかったファイルは `unchecked` を行頭に付ける (機械が読めるように)。"""
    if os.path.islink(real_path):
        return [f"stop\t{jail_path}\tsymlink (置き場では使わない)"]
    if not os.path.isdir(real_path):
        return [
            f"stop\t{jail_path}\tフォルダでない (レシピはフォルダで、RECIPE.md はその直下)"
        ]
    parent, _, name = jail_path.rpartition("/")
    why = name_problem(name)
    layer = (
        layer_of_place(parent[: -len(RECIPES) - 1])
        if parent.endswith("/" + RECIPES)
        else None
    )
    lines = []
    if why:
        lines.append(f"stop\t{jail_path}\tフォルダ名が名前規則に合わない ({why})")
    if layer is None:
        lines.append(f"stop\t{jail_path}\t<層>/recipes/<名前>/ の形でない")
        return lines
    files, skipped = walk_local_folder(real_path)
    md_real = os.path.join(real_path, RECIPE_MD)
    md_mtime = (
        dt.date.fromtimestamp(os.lstat(md_real).st_mtime)
        if os.path.isfile(md_real) and not os.path.islink(md_real)
        else None
    )
    res = check_folder(
        RecipeRef(layer, name), files, skipped, today=today, md_mtime=md_mtime
    )
    lines += [f"stop\t{jail_path}\t{s}" for s in res.stops]
    lines += [f"note\t{jail_path}\t{s}" for s in res.notes]
    lines += [
        f"unchecked\t{jail_path}\t{rel}: バイナリなので中身を検査していない"
        for rel in res.unchecked
    ]
    return lines

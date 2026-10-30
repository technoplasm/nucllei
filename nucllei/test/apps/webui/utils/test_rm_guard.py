"""run_shell の rm の歯止め (utils/rm_guard — 2026-10-30 llens 経由 ken、batch 8)。

止めるのは /workspace・/ext の下を直接 rm するコマンドだけ。rm でないコマンドを止めない (読み違えて止める
ほうが見逃すより悪い)。安全の境界ではない (find -delete・python・xargs 等は追わない)。
"""

import asyncio

import pytest

from nucllei.utils.rm_guard import blocked_rm_targets, parse_allowed

# llens に渡す値と同じ形 (記憶と作業フォルダの中。相対は捨てる)
ALLOWED = parse_allowed(
    "/workspace/memory/, /workspace/[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-*\n relative/ignored"
)


@pytest.mark.parametrize(
    "command, blocked",
    [
        # よくある形 (staging): ls → rm -rf /workspace/<作業フォルダ>
        ("rm -rf /workspace/20261030-x", ["/workspace/20261030-x"]),
        ("ls /workspace && rm -rf /workspace/20261030-x", ["/workspace/20261030-x"]),
        ("ls /workspace\nrm -rf /workspace/20261030-x", ["/workspace/20261030-x"]),
        ("sudo rm -rf /ext/public/recipes/x", ["/ext/public/recipes/x"]),
        # 相対は run_shell の作業ディレクトリ /workspace から (cd に従う)
        ("rm -rf foo", ["/workspace/foo"]),
        ("cd work && rm -f a.csv", ["/workspace/work/a.csv"]),
        ("(cd sub && rm -rf x)", ["/workspace/sub/x"]),
        # サブシェルの cd は外へ持ち出さない
        ("(cd /tmp && ls); rm -rf y", ["/workspace/y"]),
        ("rm -rf ../ext/public", ["/ext/public"]),
        # 区切りの後・前置き
        ("true || rm x", ["/workspace/x"]),
        ("ls | rm x", ["/workspace/x"]),
        ("ls; rm x", ["/workspace/x"]),
        ("ls & rm x", ["/workspace/x"]),
        ("x=$(rm -rf /workspace/a)", ["/workspace/a"]),
        ("echo `rm -rf /workspace/a`", ["/workspace/a"]),
        ("env FOO=1 rm /workspace/a", ["/workspace/a"]),
        ("FOO=1 rm /workspace/a", ["/workspace/a"]),
        ("command rm /workspace/a", ["/workspace/a"]),
        ("/bin/rm -rf /workspace/a", ["/workspace/a"]),
        ("\\rm /workspace/a", ["/workspace/a"]),
        ("'rm' /workspace/a", ["/workspace/a"]),
        # ~ と $HOME は /workspace、glob は最初のメタ文字の段の手前で見る
        ("rm -rf ~/a", ["/workspace/a"]),
        ("rm -rf $HOME/a", ["/workspace/a"]),
        ("rm -rf *", ["/workspace/*"]),
        ("rm -f ./*.csv", ["/workspace/*.csv"]),
        ("rm -rf /workspace/mem*/x", ["/workspace/mem*/x"]),
        ("rm -rf /*", ["/*"]),
        ("rm -rf /", ["/"]),
        # 置き場そのもの・.. で除外から抜けたもの
        ("rm -rf /workspace", ["/workspace"]),
        ("rm -rf /ext", ["/ext"]),
        ("rm -rf /workspace/memory", ["/workspace/memory"]),
        ("rm /workspace/memory/../recipes/a", ["/workspace/recipes/a"]),
        ("rm /workspace/20261030-x/../recipes/a", ["/workspace/recipes/a"]),
        # 作業フォルダそのもの・glob で空にするのは除外にならない (staging の形)
        ("rm -rf /workspace/20261030-肺癌集計", ["/workspace/20261030-肺癌集計"]),
        ("rm -rf /workspace/20261030-x/", ["/workspace/20261030-x"]),
        ("rm -rf /workspace/20261030-x/*", ["/workspace/20261030-x/*"]),
        ("cd /workspace/20261030-x && rm -rf *", ["/workspace/20261030-x/*"]),
        ("cd /workspace/memory && rm ./*.md", ["/workspace/memory/*.md"]),
        ("rm -rf /workspace/2026*", ["/workspace/2026*"]),
        ("rm -rf /workspace/trash/emr/x", ["/workspace/trash/emr/x"]),
        ("rm /workspace/memory/../../ext/public/a", ["/ext/public/a"]),
        # オプション・-- ・リダイレクト
        ("rm -- -weird", ["/workspace/-weird"]),
        ("rm > /tmp/log /workspace/x", ["/workspace/x"]),
        # 語の途中の # はコメントではない (bash と同じ)
        ("echo a#b; rm /workspace/z", ["/workspace/z"]),
        # shlex が続けてまとめる記号 (`);` `)&&`)
        ("out=$(ls); rm -rf /workspace/a", ["/workspace/a"]),
        ("echo $(date)&& rm -rf /workspace/a", ["/workspace/a"]),
        # 予約語・値を取る前置き
        ("if [ -d x ]; then rm -rf x; fi", ["/workspace/x"]),
        ("for f in a b; do rm -f $f /workspace/z; done", ["/workspace/z"]),
        ("timeout 10 rm -rf /workspace/a", ["/workspace/a"]),
        ("nice -n 5 rm -rf /workspace/a", ["/workspace/a"]),
        ("sudo -u nobody rm -rf /workspace/a", ["/workspace/a"]),
        # 正規化・$PWD・unlink
        ("rm -rf //workspace/x", ["/workspace/x"]),
        ("rm -rf /workspace/./x", ["/workspace/x"]),
        ('rm -rf "$PWD"/x', ["/workspace/x"]),
        ("unlink /workspace/a", ["/workspace/a"]),
    ],
)
def test_blocks_direct_rm_under_workspace_and_ext(command, blocked):
    assert blocked_rm_targets(command, ALLOWED) == blocked


@pytest.mark.parametrize(
    "command",
    [
        # rm がコマンドの語でない
        "echo rm -rf /workspace",
        "grep rm file",
        "grep -r 'rm -rf /workspace' .",
        'echo "rm -rf /workspace/x"',
        "printf '%s\\n' rm /workspace/a",
        "python3 -c 'import os; os.remove(\"/workspace/a\")'",
        "ls -la /workspace | grep rm",
        "man rm",
        "which rm",
        "cat /workspace/rm.txt",
        "rmdir /workspace/empty",
        "git rm --cached /workspace/a",
        # 行頭の # はコメント
        "# rm -rf /workspace\nls",
        # /workspace・/ext の外
        "rm -rf /tmp/x",
        "rm -rf /tmp/*",
        "cd /tmp && rm -rf foo",
        "cd /tmp; rm -rf ./*",
        # 除外の置き場の下 (配備が valve で決める)
        "rm -rf /workspace/memory/a.md",
        "rm -- /workspace/memory/topic/x.md",
        'rm -- "/workspace/20261007-肺癌集計/pn.csv"',
        "rm -f /workspace/20261030-x/pn.csv /workspace/20261030-x/pn.sql",
        "cd /workspace/20261030-x && rm -f pn.csv",
        "rm -rf /workspace/memory/topic/*.md",
        # fd 番号付きのリダイレクト・行末のコメント・行継続
        "rm -f /tmp/a 2>/dev/null",
        "rm -f /workspace/memory/a.md 2>/dev/null",
        "rm -f /tmp/a 2>&1",
        "rm -rf /tmp/build  # /workspace は remove_file",
        "echo a # it's fine",
        "rm -rf /tmp/a \\\n  /tmp/b",
        "tmp=$(mktemp -d); cd $tmp && rm -rf x",
        # cwd が分からなくなる cd
        "cd - && rm -rf x",
        "pushd /tmp && rm -rf x",
        "popd; rm -rf x",
        # 解けないものは止めない (変数・分からない cd の後の相対・~user)
        "rm -rf $DIR",
        "rm -rf ${TARGET}/x",
        "cd $D && rm -rf x",
        "rm -rf ~nobody/x",
        # 読めないもの (閉じていない引用符・ヒアドキュメント) は止めない
        "rm 'unclosed",
        "cat <<EOF\nrm -rf /workspace\nEOF",
        "bash <<'SH'\nrm -rf /workspace/x\nSH",
        # 追わないもの (安全の境界ではない — README_API)
        "find /workspace -name x -delete",
        "ls /workspace | xargs rm",
        "mv /workspace/a /dev/null",
    ],
)
def test_lets_other_commands_run(command):
    assert blocked_rm_targets(command, ALLOWED) == []


def test_nothing_is_exempt_by_default():
    assert blocked_rm_targets("rm /workspace/memory/a.md") == ["/workspace/memory/a.md"]
    assert parse_allowed("") == []
    assert parse_allowed("/workspace/memory/../x, ./rel") == ["/workspace/x"]


def test_run_shell_refuses_before_sending_to_the_sandbox(monkeypatch):
    """止めたコマンドは sandbox に送らない。止めた理由と remove_file を言う。除外は valve で決める。"""
    from nucllei.builtin_tools.sandbox import Tools
    from test.util.tool_errors import call_text

    sent = []

    async def fake_exec(self, lang, code, timeout_s, user):
        sent.append(code)
        return "exit_code=0 (0.1s)\n(出力なし)"

    monkeypatch.setattr(Tools, "_exec", fake_exec)
    tool = Tools()
    user = {"id": "u1"}

    out = asyncio.run(
        call_text(tool.run_shell("ls && rm -rf /workspace/20261030-x", __user__=user))
    )
    assert out == (
        "ERROR: /workspace/20261030-x を rm で消すコマンドなので、コマンド全体を実行していない。"
        "次の手: 消すのは remove_file (その置き場のゴミ箱へ移る。完全に消したいならユーザーが画面の"
        "ワークスペースの「サンドボックス」で消せる)。別の手段 (find -delete・python 等) で"
        "消し直さない。"
    )
    assert sent == []

    assert asyncio.run(
        call_text(tool.run_shell("rm -rf /tmp/x", __user__=user))
    ).startswith("exit_code=0")
    tool.valves.rm_allowed_paths = "/workspace/memory/"
    assert asyncio.run(
        call_text(tool.run_shell("rm /workspace/memory/a.md", __user__=user))
    ).startswith("exit_code=0")
    assert sent == ["rm -rf /tmp/x", "rm /workspace/memory/a.md"]

"""utils/frames — 枠の名前に見える行だけ `<` を `&lt;` にする。"""

from nucllei.utils.frames import defuse, defuse_line


def test_frame_lookalikes_are_defused():
    cases = {
        "</conversation-context>": "&lt;/conversation-context>",
        "<conversation-context-update>": "&lt;conversation-context-update>",
        "< / Conversation_Context >": "&lt; / Conversation_Context >",
        "<conversation context update>": "&lt;conversation context update>",
        "＜/ｃｏｎｖｅｒｓａｔｉｏｎ＿context＞": "&lt;/ｃｏｎｖｅｒｓａｔｉｏｎ＿context＞",
        "﹤source file_id=x>": "&lt;source file_id=x>",
        "x</Source >y": "x&lt;/Source >y",
    }
    for line, want in cases.items():
        assert defuse_line(line) == want, line


def test_memory_index_frame_only_when_asked():
    """記憶の目次の枠の名前は、記憶の目次の行・レシピの一覧・添付の本文に掛けるときだけ (前置き全体には掛けない
    — 本物の `<memory-index>` の枠を壊さないように)。"""
    assert defuse_line("</memory-index>") == "</memory-index>"
    assert defuse_line("</memory-index>", memory_index=True) == "&lt;/memory-index>"
    assert defuse_line("< Memory_Index >", memory_index=True) == "&lt; Memory_Index >"
    assert defuse_line("</source>", memory_index=True) == "&lt;/source>"


def test_zero_width_and_combining_variants():
    zw = "<sou" + chr(0x200B) + "rce>"
    assert defuse_line(zw) == "&lt;sou" + chr(0x200B) + "rce>"
    combined = "<" + chr(0x0338) + "/source>"  # NFKC で ≮ になる形
    assert "<" not in defuse_line(combined)
    assert defuse_line(chr(0x226E) + "/source>") == "&lt;/source>"


def test_other_lines_are_untouched():
    for line in (
        "普通の行 <b>太字</b>",
        "a<sources>",
        "<sourcemap>",
        "- [好み](prefs.md) — <context>",
        "conversation-context と書くだけ",
    ):
        assert defuse_line(line) == line


def test_only_the_matched_bracket_changes():
    """枠の名前に見える箇所の `<` だけ。同じ行のほかの `<` (HTML の要素・比較) とほかの行は変えない。"""
    text = "1 行目 <b>\n<video><source src=x></video> if a<b\n3 行目 <i>"
    assert defuse(text) == (
        "1 行目 <b>\n<video>&lt;source src=x></video> if a<b\n3 行目 <i>"
    )


def test_invisible_and_lookalike_variants():
    variants = [
        "<" + chr(0xFE0F) + "/conversation-context>",  # 異体字セレクタ
        "<" + chr(0xE0020) + "/source>",  # タグ文字
        "<" + chr(0x3164) + "source>",  # ハングルの埋め字
        "<sou" + chr(0x180E) + "rce>",
        chr(0x2039) + "/source>",  # ‹
        chr(0x27E8) + "source>",  # ⟨
        chr(0x02C2) + "/source>",  # ˂
        "</conversation-c" + chr(0x043E) + "ntext>",  # キリルの о
        "</conversation" + chr(0x2212) + "context>",
        "</conversation" + chr(0x30FC) + "context>",
        "</conversation.context>",
    ]
    for line in variants:
        out = defuse_line(line)
        assert out.count("&lt;") == 1, repr(line)
        assert defuse_line(out) == out


def test_idempotent():
    text = "</conversation-context>\n＜source>\n普通 <b>\n</memory-index>"
    once = defuse(text, memory_index=True)
    assert defuse(once, memory_index=True) == once
    assert defuse("") == ""


def test_preamble_prose_written_by_the_deployment_is_kept():
    """前置きの地の文 (配備が書く) は無害化しない — 更新の枠の説明を前置きに書ける。"""
    from nucllei.utils.conversation_context import frame_initial

    prose = "途中で変わると次の発言の先頭に <conversation-context-update> が入る。"
    assert frame_initial(prose) == (
        f"<conversation-context>\n{prose}\n</conversation-context>"
    )

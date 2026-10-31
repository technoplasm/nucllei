"""STT の幻聴の後処理(nucllei/utils/stt_filter.py)の単体テスト。

定型句は「区間 / 転写全体が定型句だけ」かつ無音由来と言える位置にあるときだけ落とし、
発話の途中で言ったものは残す。繰り返しループは 1 回に畳み、ふつうの文には触らない。
"""

from types import SimpleNamespace

from nucllei.utils.stt_filter import (
    DEFAULT_HALLUCINATION_PHRASES,
    SttFilterOptions,
    collapse_repetitions,
    compression_ratio,
    filter_segments,
    filter_text,
    is_hallucination_phrase,
    parse_phrases,
)

OPTS = SttFilterOptions()
NORMAL = "68歳男性。3日前からの発熱と咳嗽を主訴に来院した。白血球が12000、CRPが8.6と上昇している。"


def seg(text, start, end, no_speech_prob=0.0, avg_logprob=-0.2):
    """faster-whisper の Segment の外形だけを持つモック。"""
    return SimpleNamespace(
        text=text,
        start=start,
        end=end,
        no_speech_prob=no_speech_prob,
        avg_logprob=avg_logprob,
    )


# --- 定型句の判定 ---------------------------------------------------------


def test_phrase_match_ignores_punctuation_width_and_case():
    phrases = DEFAULT_HALLUCINATION_PHRASES
    assert is_hallucination_phrase("ご視聴ありがとうございました", phrases)
    assert is_hallucination_phrase(" ご視聴ありがとうございました。", phrases)
    assert is_hallucination_phrase("THANK YOU FOR WATCHING!", phrases)
    assert is_hallucination_phrase("Thanks for watching.", phrases)
    # 定型句の連なりだけでできていれば同じ扱い
    assert is_hallucination_phrase(
        "ご視聴ありがとうございました。チャンネル登録よろしくお願いします。", phrases
    )


def test_phrase_match_requires_whole_text():
    phrases = DEFAULT_HALLUCINATION_PHRASES
    assert not is_hallucination_phrase(
        "ご視聴ありがとうございましたという言葉は動画の最後に出てきます。", phrases
    )
    assert not is_hallucination_phrase("ありがとうございました。", phrases)
    assert not is_hallucination_phrase("", phrases)
    assert not is_hallucination_phrase("。", phrases)


def test_parse_phrases():
    assert parse_phrases(None) == DEFAULT_HALLUCINATION_PHRASES
    assert parse_phrases("") == ()
    assert parse_phrases(" 字幕作成 | おわり \n Bye ") == ("字幕作成", "おわり", "Bye")
    assert not is_hallucination_phrase(
        "ご視聴ありがとうございました", parse_phrases("")
    )


# --- テキストだけのエンジン -----------------------------------------------


def test_filter_text_drops_only_whole_transcript_phrase():
    assert filter_text("ご視聴ありがとうございました。", OPTS) == ""
    assert filter_text("Thank you for watching!", OPTS) == ""
    # 発話の途中・文の一部なら残す
    text = "ご視聴ありがとうございましたという言葉は動画の最後によく出てきます。"
    assert filter_text(text, OPTS) == text
    text = "血圧は120の80です。ご視聴ありがとうございました。"
    assert filter_text(text, OPTS) == text


def test_filter_text_leaves_normal_text_untouched():
    assert filter_text(NORMAL, OPTS) == NORMAL
    assert filter_text("", OPTS) == ""


def test_filter_text_disabled_is_passthrough():
    opts = SttFilterOptions(enabled=False)
    assert (
        filter_text("ご視聴ありがとうございました", opts)
        == "ご視聴ありがとうございました"
    )
    assert filter_text("5、" * 20, opts) == "5、" * 20


# --- 繰り返しの畳み込み ---------------------------------------------------


def test_collapse_repeated_number_and_phrase():
    assert collapse_repetitions("5、5、5、5、5、5、5、5、", 5, 50) == "5、"
    assert (
        collapse_repetitions("所見なし。" + "以上です。" * 12, 5, 50)
        == "所見なし。以上です。"
    )
    assert collapse_repetitions("the the the the the end", 5, 50) == "the end"
    assert collapse_repetitions("あ" * 30, 5, 50) == "あ"


def test_collapse_keeps_short_or_natural_repeats():
    # 回数が足りない繰り返し・数字だけの並びはそのまま
    assert collapse_repetitions("はいはいはいはい", 5, 50) == "はいはいはいはい"
    assert collapse_repetitions("1000000円", 5, 50) == "1000000円"
    assert collapse_repetitions("11111", 5, 50) == "11111"
    assert collapse_repetitions(NORMAL, 5, 50) == NORMAL


def test_collapse_respects_knobs():
    assert collapse_repetitions("はい" * 4, 4, 50) == "はい"
    assert collapse_repetitions("はい" * 10, 0, 50) == "はい" * 10
    # 単位の最大長より長い句は畳まない
    assert (
        collapse_repetitions("診察を終了します。" * 6, 5, 5) == "診察を終了します。" * 6
    )


def test_filter_text_collapses_loop():
    assert filter_text("CRPは8.6です。" + "8.6、" * 20, OPTS) == "CRPは8.6です。8.6、"


def test_compression_ratio():
    assert compression_ratio("") == 0.0
    assert compression_ratio(NORMAL) < 2.4
    assert compression_ratio("ありがとう" * 40) > 2.4


# --- 内蔵 faster-whisper の区間 -------------------------------------------


def test_segments_normal_speech_untouched():
    segments = [
        seg("68歳男性。", 0.3, 2.1),
        seg("3日前からの発熱と咳嗽を主訴に来院した。", 2.1, 5.9),
        seg("白血球が12000、CRPが8.6と上昇している。", 6.0, 10.2),
    ]
    assert filter_segments(segments, 10.7, OPTS) == "".join(s.text for s in segments)


def test_segments_whole_transcript_phrase_dropped():
    assert (
        filter_segments([seg("ご視聴ありがとうございました", 0.6, 1.6)], 12.0, OPTS)
        == ""
    )
    # 定型句だけの区間が 2 つでも、全体が定型句だけなら空
    segments = [
        seg("ご視聴ありがとうございました。", 0.6, 2.0),
        seg("チャンネル登録お願いします。", 2.0, 3.5),
    ]
    assert filter_segments(segments, 12.0, OPTS) == ""


def test_segments_phrase_past_end_of_audio_dropped():
    """Whisper が 30 秒窓の終わりの時刻を付けた(音声の外まで続く)定型句は無音由来。"""
    segments = [
        seg("血圧は120の80です。", 0.4, 3.1),
        seg("ご視聴ありがとうございました", 3.5, 30.6),
    ]
    assert filter_segments(segments, 12.0, OPTS) == "血圧は120の80です。"


def test_segments_phrase_at_silent_tail_dropped():
    # 末尾の区間で、前に 2 秒以上の無音
    segments = [
        seg("次の患者さんを呼んでください。", 0.4, 2.6),
        seg("ご視聴ありがとうございました。", 9.0, 10.5),
    ]
    assert filter_segments(segments, 12.0, OPTS) == "次の患者さんを呼んでください。"
    # 末尾の区間で、無音らしさが高い
    segments = [
        seg("次の患者さんを呼んでください。", 0.4, 2.6),
        seg("Thank you for watching.", 2.7, 4.0, no_speech_prob=0.7, avg_logprob=-0.5),
    ]
    assert filter_segments(segments, 12.0, OPTS) == "次の患者さんを呼んでください。"


def test_segments_phrase_said_in_speech_kept():
    # 話の途中の区間(後ろに発話が続く)
    segments = [
        seg("動画の最後は", 0.4, 1.5),
        seg("ご視聴ありがとうございました。", 1.5, 3.0),
        seg("で終わることが多い。", 3.0, 4.5),
    ]
    assert filter_segments(segments, 5.0, OPTS) == "".join(s.text for s in segments)
    # 末尾でも、直前の発話から間が無く、音声の中に収まっていれば残す
    segments = [
        seg("本日の配信は以上です。", 0.4, 2.0),
        seg("ご視聴ありがとうございました。", 2.2, 4.0),
    ]
    assert filter_segments(segments, 4.5, OPTS) == "".join(s.text for s in segments)
    # 文の一部(区間全体が定型句ではない)
    segments = [seg("ご視聴ありがとうございましたという言葉。", 0.4, 30.0)]
    assert (
        filter_segments(segments, 5.0, OPTS)
        == "ご視聴ありがとうございましたという言葉。"
    )


def test_segments_no_speech_and_low_logprob_dropped():
    segments = [
        seg("血圧は120の80です。", 0.4, 3.1),
        seg("うーん", 5.0, 6.0, no_speech_prob=0.9, avg_logprob=-1.5),
    ]
    assert filter_segments(segments, 8.0, OPTS) == "血圧は120の80です。"
    # 片方だけなら残す(Whisper と同じ AND 条件)
    segments = [
        seg("血圧は120の80です。", 0.4, 3.1, no_speech_prob=0.9, avg_logprob=-0.3),
        seg("はい", 5.0, 6.0, no_speech_prob=0.1, avg_logprob=-1.5),
    ]
    assert filter_segments(segments, 8.0, OPTS) == "血圧は120の80です。はい"


def test_segments_loop_collapsed_and_cross_segment_loop():
    segments = [seg("CRPは8.6です。", 0.4, 2.0), seg("8.6、" * 20, 2.0, 8.0)]
    assert filter_segments(segments, 8.0, OPTS) == "CRPは8.6です。8.6、"
    # 同じ区間が何度も繰り返される
    segments = [seg("所見なし。", 0.4, 1.0)] + [
        seg("以上です。", 1.0 + i, 2.0 + i) for i in range(8)
    ]
    assert filter_segments(segments, 10.0, OPTS) == "所見なし。以上です。"


def test_segments_high_compression_after_collapse_dropped():
    # 単位が長くて畳めない繰り返し(交互のパターン)は圧縮率で落とす
    loop = "右肺に陰影あり、左肺に陰影なし、" * 8
    segments = [seg("胸部レントゲンを撮影した。", 0.4, 2.0), seg(loop, 2.0, 9.0)]
    opts = SttFilterOptions(repeat_max_unit_chars=10)
    assert filter_segments(segments, 10.0, opts) == "胸部レントゲンを撮影した。"


def test_segments_missing_signals_and_duration():
    """信号の無い区間(テスト用のモック等)・長さ不明でも組み立てられる。"""
    segments = [SimpleNamespace(text=" こんにちは"), SimpleNamespace(text="")]
    assert filter_segments(segments, None, OPTS) == "こんにちは"
    assert filter_segments([], None, OPTS) == ""


def test_segments_disabled_is_plain_join():
    segments = [seg("ご視聴ありがとうございました", 0.6, 30.6)]
    opts = SttFilterOptions(enabled=False)
    assert filter_segments(segments, 12.0, opts) == "ご視聴ありがとうございました"

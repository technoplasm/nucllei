"""
title: Doc
author: Ken Enda
description: cage API 経由でドキュメントツリーの目次 (list_doc), 本文 (read_doc), 全文検索 (grep_doc) を提供するツール
requirements: requests
"""

from typing import Any, Dict

import requests
from pydantic import BaseModel, Field


class Tools:
    """ドキュメントツリーの目次 (list_doc) / 本文 (read_doc) / 全文検索 (grep_doc) を提供するツール。

    cage API (`POST /doc/list` / `POST /doc/read` / `POST /doc/grep`) を叩く薄い HTTP クライアント。
    """

    class Valves(BaseModel):
        api_base: str = Field(
            default="http://172.17.0.1:13303",
            description="cage API server base URL (e.g. http://localhost:13303)",
        )
        timeout_sec: float = Field(
            default=15.0,
            description="HTTP request timeout in seconds",
        )

    def __init__(self) -> None:
        self.valves = self.Valves()

    def list_doc(self, path: str = "/") -> Dict[str, Any]:
        """指定 path の目次 (children 一覧) を取得する。

        :param path: ツリー上の位置を表す `/` 始まりの文字列。省略時は root (`"/"`)。
        :return: 成功時 {path, title, meta, body_chars, child_count,
                          children: [{path, title, body_chars, child_count}]}
                 `body_chars` は read_doc で返る本文の文字数 (0 = 本文なし)。
                 read 自体はどのノードでも呼んでよい (中間ノードでも本文を持つ場合がある)。
                 読む前にペイロードの大きさが分かるので、通常サイズなら read_doc、
                 数万字級の長大ノードは run_subagent に読ませる、の判断に使う。
                 not_found 時 {error: "not_found", missing, valid_path, siblings_at_parent}
        """
        r = requests.post(
            f"{self.valves.api_base}/doc/list",
            json={"path": path},
            timeout=self.valves.timeout_sec,
        )
        if r.status_code in (400, 404):
            return r.json()
        r.raise_for_status()
        return r.json()

    def read_doc(self, path: str) -> str:
        """指定 path の本文 (Markdown) を取得する。

        list_doc と同じ path を渡せる (両者は同じノードに対する別操作)。
        本文を持たないノードに呼んだ場合は空文字列が返る (エラーにはしない)。
        list_doc 戻り値の `body_chars` で事前に本文の有無とサイズを判別可能。

        :param path: `/` 始まりの path 文字列。
        :return: 成功時は本文 Markdown 文字列 (空ノードは "")。
                 失敗時 (path 誤り) は `"ERROR: ..."` で始まる誘導メッセージ
                 (missing / valid_path / siblings_at_parent を含む)。
        """
        r = requests.post(
            f"{self.valves.api_base}/doc/read",
            json={"path": path},
            timeout=self.valves.timeout_sec,
        )
        if r.status_code == 404:
            info = r.json()
            return (
                f"ERROR: not_found. "
                f"missing segment: {info.get('missing')!r}. "
                f"valid path so far: {info.get('valid_path', '/')!r}. "
                f"available children at that level: {info.get('siblings_at_parent', [])}. "
                f"→ list_doc({info.get('valid_path', '/')!r}) で目次を取り直してから再試行する。"
            )
        if r.status_code == 400:
            info = r.json()
            return (
                f"ERROR: bad_path. {info.get('message', '')}. "
                f"path は `/` 始まりの文字列で指定する。"
            )
        r.raise_for_status()
        return r.text

    def grep_doc(
        self,
        query: str,
        path: str = "/",
        regex: bool = False,
    ) -> Dict[str, Any]:
        """指定 path の subtree 全体に対して query で全文検索する。

        各ノードで title / description (front matter の description) / body の 3 field を
        独立に評価し、いずれかに 1 件以上ヒットしたノードを 1 entry として返す。
        case-insensitive + NFKC 正規化 (substring 時) で全角/半角・大文字小文字の
        揺れは吸収される。

        注意: entry 数 (= マッチしたノード数) は 30 で打ち切られる。スニペットは
        body にヒットしたときの最初の 1 箇所分 (±40字) しか返さないので、grep だけで
        回答を組み立てず、必ず read_doc で本文を引く。

        - 範囲は path で絞る (例: 質問が CTCAE 関連なら path="/診療ガイドライン/CTCAEv5_JCOG_2025")。
        - query は具体的に (例: "EGFR" より "オシメルチニブ"、"治療" のような広語は避ける)。
        - 全文検索 (path="/") は最後の手段。
        - 30 件で truncated になるが、それ以前にマッチ件数を抑えるのが原則。
        - entry の match_count.body が大きいノードほど「主題そのもの」の可能性が高い。
          1 や 2 は通りすがり言及の可能性があるので、ノイズかどうかは
          body_first_match_snippet で軽く判定 → 怪しければ read_doc で確定。

        :param query: 検索文字列。`regex=False` のときは substring 一致、
                      `regex=True` のときは Python の `re.compile(query, re.IGNORECASE)`
                      で正規表現マッチ。
        :param path: 検索範囲を限定する path。`"/"` / `""` / `"*"` / `"/*"` は
                     いずれも root subtree 全体を意味する (= 全カテゴリ横断)。
        :param regex: True で正規表現マッチに切り替える。デフォルト False。
        :return: 成功時 {path, query, regex, match_count, truncated, matches: [entry...]}
                 - `match_count` は truncate 前の総 entry 数 (= マッチしたノード数)
                 - `truncated` は max_results=30 を超えたか
                 - entry の shape:
                     {
                       "path": str,
                       "title": str,
                       "body_chars": int,                  # 本文の文字数 (0 = 本文なし)
                       "child_count": int,
                       "match_count": {"title": 0|1, "description": 0|1, "body": int},
                       "description"?: str,                # description ヒット時のみ (全文)
                       "body_first_match_snippet"?: str,   # body ヒット時のみ (最初の ±40字)
                     }
                 - 同じノードで title/description/body 複数 field 同時ヒットも match_count
                   に分解して表現される (旧仕様の title-first 優先採用は廃止)。
                 path 不正 時 {error: "not_found", missing, valid_path, siblings_at_parent}
                 regex 不正 時 {error: "bad_regex", message}
                 query 空    時 {error: "bad_query", message}
        """
        r = requests.post(
            f"{self.valves.api_base}/doc/grep",
            json={"query": query, "path": path, "regex": regex},
            timeout=self.valves.timeout_sec,
        )
        if r.status_code in (400, 404):
            return r.json()
        r.raise_for_status()
        return r.json()

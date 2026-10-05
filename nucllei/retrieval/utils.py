import json
import logging

from nucllei.models.files import Files
from nucllei.env import SRC_LOG_LEVELS

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["RAG"])


def _dictation_marks(file_object) -> dict:
    """音声入力の通し録音(meta.dictation)なら、`<source>` に載せる印を返す。

    載せるのは印と「何本中の何本目か」だけ(区間一覧・本文は prompt に入れない)。
    """
    dictation = (file_object.meta or {}).get("dictation")
    if not dictation:
        return {}
    return {
        "dictation": True,
        "dictation_part": dictation.get("part"),
        "dictation_parts": dictation.get("parts"),
    }


def _file_identity(file) -> str:
    """重複判定の鍵: file id(無ければ entry の JSON 全体)。"""
    if isinstance(file, dict):
        file_id = file.get("id") or (file.get("file") or {}).get("id")
        if file_id:
            return f"id:{file_id}"
    return "json:" + json.dumps(file, sort_keys=True, default=str, ensure_ascii=False)


def dedupe_files_by_id(files: list) -> list:
    """添付 entry を file id で一意にする(先勝ち・順序維持)。

    id が同じでメタデータ(status / itemId / 表示名 等)だけ違う entry は同じファイル —
    JSON 全体の一致で比べると両方残り、本文が system に 2 回入る(監査 2026-10-05:
    2,031 → 3,529 字)。id の無い entry だけ従来どおり JSON 全体で比べる。
    """
    seen = set()
    result = []
    for file in files or []:
        key = _file_identity(file)
        if key in seen:
            continue
        seen.add(key)
        result.append(file)
    return result


def get_sources_from_files(request, files):
    # RAG 撤去(#52): ベクタ検索・チャンク・reranking を廃止。添付は data.content を全文注入する。
    log.debug(f"files: {files}")

    # 呼び手が渡した files が重複していても 1 ファイル 1 source(id 基準)
    files = dedupe_files_by_id(files)

    relevant_contexts = []

    for file in files:

        context = None
        if file.get("docs"):
            # インライン docs(サーバー生成の content 配列をそのまま渡す経路)
            context = {
                "documents": [[doc.get("content") for doc in file.get("docs")]],
                "metadatas": [[doc.get("metadata") for doc in file.get("docs")]],
            }
        else:
            # 全文注入(唯一経路): 添付ファイルの data.content をそのまま積む
            if file.get("type") == "collection":
                file_ids = file.get("data", {}).get("file_ids", [])

                documents = []
                metadatas = []
                for file_id in file_ids:
                    file_object = Files.get_file_by_id(file_id)

                    if file_object:
                        data = file_object.data or {}
                        documents.append(data.get("content", ""))
                        metadatas.append(
                            {
                                "file_id": file_id,
                                "name": file_object.filename,
                                "source": file_object.filename,
                                # llens(#52): 抽出状態(attachment が制御)を注入側へ渡す
                                "extraction_status": data.get("status"),
                                **_dictation_marks(file_object),
                            }
                        )

                context = {
                    "documents": [documents],
                    "metadatas": [metadatas],
                }

            elif file.get("id"):
                file_object = Files.get_file_by_id(file.get("id"))
                if file_object:
                    data = file_object.data or {}
                    context = {
                        "documents": [[data.get("content", "")]],
                        "metadatas": [
                            [
                                {
                                    "file_id": file.get("id"),
                                    "name": file_object.filename,
                                    "source": file_object.filename,
                                    # llens(#52): 抽出状態を注入側へ渡す(成功/失敗/未抽出)
                                    "extraction_status": data.get("status"),
                                    **_dictation_marks(file_object),
                                }
                            ]
                        ],
                    }
            elif file.get("file").get("data"):
                context = {
                    "documents": [[file.get("file").get("data", {}).get("content")]],
                    "metadatas": [
                        [file.get("file").get("data", {}).get("metadata", {})]
                    ],
                }

        if context:
            if "data" in file:
                del file["data"]

            relevant_contexts.append({**context, "file": file})

    sources = []
    for context in relevant_contexts:
        try:
            if "documents" in context:
                if "metadatas" in context:
                    source = {
                        "source": context["file"],
                        "document": context["documents"][0],
                        "metadata": context["metadatas"][0],
                    }
                    if "distances" in context and context["distances"]:
                        source["distances"] = context["distances"][0]

                    sources.append(source)
        except Exception as e:
            log.exception(e)

    return sources

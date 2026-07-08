import logging

from open_webui.models.files import Files
from open_webui.env import SRC_LOG_LEVELS

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["RAG"])


def get_sources_from_files(request, files):
    # RAG 撤去(#52): ベクタ検索・チャンク・reranking を廃止。添付は data.content を全文注入する。
    log.debug(f"files: {files}")

    relevant_contexts = []

    for file in files:

        context = None
        if file.get("docs"):
            # BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL(Web 検索の bypass 経路)
            context = {
                "documents": [[doc.get("content") for doc in file.get("docs")]],
                "metadatas": [[doc.get("metadata") for doc in file.get("docs")]],
            }
        elif file.get("type") != "web_search":
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

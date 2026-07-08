"""既定 builtin Attachment(#52 プランB)。

アップロードされたファイルをテキスト化する既定のフック。従来 `routers/files.py` の
upload endpoint にハードコードされていた content_type 分岐(audio→STT / 非画像→docling /
画像→skip)を、そのまま Attachment Function 種別に relocate したもの。判定に使う
content_type のリストは **valves に格下げ**して設定可能にした。

出荷/登録は builtin tool(#18)を参考。Function テーブルには meta 列が無い(#45/#43)ため、
builtin 判別は id 接頭辞 `builtin_`(`builtin_functions/__init__.py` の BUILTIN_FUNCTIONS
が真実源)。抽出の実体は内部関数(`retrieval.extract.extract_file_text` / audio の
`transcribe`)に委譲する = プランBの「hook はルータ、抽出は内部関数」の骨格。
"""

from pydantic import BaseModel


class Attachment:
    class Valves(BaseModel):
        # STT に回す content_type(音声 → 文字列)
        stt_content_types: list[str] = [
            "audio/mpeg",
            "audio/wav",
            "audio/ogg",
            "audio/x-m4a",
        ]
        # 抽出せず素通しする content_type(画像等。VLM 注入は別 filter の仕事)
        skip_content_types: list[str] = [
            "image/png",
            "image/jpeg",
            "image/gif",
        ]

    def __init__(self):
        self.valves = self.Valves()

    def process(self, file, __request__, __user__=None):
        """アップロード直後に1ファイルを処理して file.data.content を埋める。

        content_type で種別を判定し、対応する内部関数へ振り分ける(ルータ)。
        戻り値は同じ file(呼び出し側が再取得する)。
        """
        # 循環 import 回避のため遅延 import(builtin は起動時 sync でも触られる)。
        from open_webui.storage.provider import Storage
        from open_webui.routers.audio import transcribe
        from open_webui.routers.retrieval import process_file, ProcessFileForm

        content_type = (file.meta or {}).get("content_type")

        if content_type in self.valves.stt_content_types:
            # 音声 → STT → 文字列を content として保存
            file_path = Storage.get_file(file.path)
            result = transcribe(__request__, file_path)
            process_file(
                __request__,
                ProcessFileForm(file_id=file.id, content=result.get("text", "")),
                user=__user__,
            )
        elif content_type not in self.valves.skip_content_types:
            # 文書 → docling 等で抽出(content_type 不明もこちら=従来挙動)
            process_file(
                __request__,
                ProcessFileForm(file_id=file.id),
                user=__user__,
            )
        # skip_content_types(画像等)は何もしない

        return file

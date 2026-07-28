"""llens(#53): STT / TTS の内部関数。

hook / filter / tool のコードから呼ぶ向き(tool としてモデルへ直接露出しない)。
sync で提供する — 主な caller は sync tool(ワーカースレッド実行・イベントループなし)。
async 文脈(route / hook)からは asyncio.to_thread で呼ぶ(#28 のパターン)。
ACL は見ない(caller は admin 作の filter / tool で信頼)。

エンジン範囲: STT = 内蔵 faster-whisper + openai 互換(routers/audio.transcribe に委譲)/
TTS = openai 互換のみ(llens の tts-bridge がこれ)。他エンジンは HTTP 経路に温存。
"""

import hashlib
import json
import logging
import os
import shutil
import tempfile
import uuid
from pathlib import Path

import requests

from nucllei.config import CACHE_DIR
from nucllei.env import AIOHTTP_CLIENT_TIMEOUT, SRC_LOG_LEVELS
from nucllei.models.files import Files

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["AUDIO"])

SPEECH_CACHE_DIR = CACHE_DIR / "audio" / "speech"
SPEECH_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def transcribe_audio(
    request,
    file_path: str | None = None,
    *,
    file_id: str | None = None,
) -> str:
    """音声 → 文字列。file_path か file_id のどちらか一方を渡す。

    - file_id: data.content があればそれを返す(冪等・文書抽出と同型。
      engine/model を変えても再文字起こしはしない — 変えたければ再アップロード)。
      無ければ文字起こしして {content, status: "success"} を保存(attachment core と同じ形)。
    - file_path: キャッシュなしの都度実行(#56 のチャンク呼びが乗る形)。
    - 言語は指定できない(WHISPER_LANGUAGE のグローバル設定のみ)。
    """
    if (file_path is None) == (file_id is None):
        raise ValueError(
            "transcribe_audio: file_path か file_id のどちらか一方を指定する"
        )

    # 循環回避: routers/audio が本モジュールの synthesize_speech を import するため、
    # 逆向きの import は呼び出し時に解決する
    from nucllei.routers.audio import transcribe
    from nucllei.storage.provider import Storage

    if file_id is not None:
        file = Files.get_file_by_id(file_id)
        if file is None:
            raise ValueError(f"transcribe_audio: file が見つからない: {file_id}")

        content = (file.data or {}).get("content")
        if content:
            return content

        src_path = Storage.get_file(file.path)
    else:
        src_path = file_path
        if not os.path.isfile(src_path):
            raise ValueError(f"transcribe_audio: ファイルが存在しない: {src_path}")

    # transcribe(openai 経路)は WAV 変換で入力ファイルを in-place 書き換えするため、
    # 元ファイル(Storage 実体・caller のファイル)を守る目的で必ず作業コピーを切る。
    # 作業コピーは一時ディレクトリに置き、派生物({id}.json 等)ごと自動削除する —
    # CACHE_DIR 配下は /cache として無認証配信されるため、purge 済み音声の実体コピーを
    # そこに残さない(#94)
    ext = Path(src_path).suffix or ".wav"
    with tempfile.TemporaryDirectory() as tmp_dir:
        work_path = os.path.join(tmp_dir, f"{uuid.uuid4()}{ext}")
        shutil.copyfile(src_path, work_path)
        text = transcribe(request, work_path)["text"]

    if file_id is not None:
        Files.update_file_data_by_id(file_id, {"content": text, "status": "success"})

    return text


def synthesize_speech(
    request,
    text: str,
    *,
    voice: str | None = None,
    model: str | None = None,
) -> Path:
    """テキスト → 音声(mp3 のキャッシュパスを返す)。openai 互換エンジンのみ。

    voice / model は config(AUDIO_TTS_VOICE / AUDIO_TTS_MODEL)が既定で、
    引数で per-call 上書きできる(en/ja → voice の対応表は caller 側の責務)。
    files 登録が要るなら caller が行う。
    """
    config = request.app.state.config
    engine = config.TTS_ENGINE

    if engine != "openai":
        raise ValueError(
            f"synthesize_speech: openai 互換エンジンのみ対応(現在の TTS_ENGINE: {engine or '未設定'})"
        )

    model = model or config.TTS_MODEL
    voice = voice or config.TTS_VOICE

    # 旧 /audio/speech は voice がキーに入っておらず声違いが衝突し得た(#53 で是正)
    key = hashlib.sha256(f"{engine}:{model}:{voice}:{text}".encode("utf-8")).hexdigest()
    cache_path = SPEECH_CACHE_DIR / f"{key}.mp3"
    if cache_path.is_file():
        return cache_path

    r = requests.post(
        f"{config.TTS_OPENAI_API_BASE_URL}/audio/speech",
        json={"input": text, "model": model, "voice": voice},
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.TTS_OPENAI_API_KEY}",
        },
        timeout=AIOHTTP_CLIENT_TIMEOUT,
    )
    r.raise_for_status()

    cache_path.write_bytes(r.content)
    cache_path.with_suffix(".json").write_text(
        json.dumps({"input": text, "model": model, "voice": voice}, ensure_ascii=False)
    )
    return cache_path

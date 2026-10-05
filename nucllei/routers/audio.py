import asyncio
import hashlib
import json
import logging
import os
import tempfile
import uuid
from functools import lru_cache
from pydub import AudioSegment

import aiohttp
import aiofiles
import requests
import mimetypes

from fastapi import (
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
    APIRouter,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel


from nucllei.utils.auth import get_admin_user, get_verified_user
from nucllei.config import (
    WHISPER_MODEL_AUTO_UPDATE,
    WHISPER_MODEL_DIR,
)

from nucllei.constants import ERROR_MESSAGES
from nucllei.env import (
    AIOHTTP_CLIENT_TIMEOUT,
    AIOHTTP_CLIENT_TIMEOUT_CONNECT,
    AIOHTTP_CLIENT_TIMEOUT_SOCK_READ,
    SRC_LOG_LEVELS,
)
from nucllei.utils.audio import (
    AUDIO_LIST_TIMEOUT,
    AUDIO_REQUEST_TIMEOUT,
    SPEECH_CACHE_DIR,
    synthesize_speech,
)

router = APIRouter()

# Constants
MAX_FILE_SIZE_MB = 25
MAX_FILE_SIZE = MAX_FILE_SIZE_MB * 1024 * 1024  # Convert MB to bytes
AZURE_MAX_FILE_SIZE_MB = 200
AZURE_MAX_FILE_SIZE = AZURE_MAX_FILE_SIZE_MB * 1024 * 1024  # Convert MB to bytes

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["AUDIO"])

##########################################
#
# Utility functions
#
##########################################

from pydub.utils import mediainfo


def get_audio_format(file_path):
    """Check if the given file needs to be converted to a different format."""
    if not os.path.isfile(file_path):
        log.error(f"File not found: {file_path}")
        return False

    info = mediainfo(file_path)
    if (
        info.get("codec_name") == "aac"
        and info.get("codec_type") == "audio"
        and info.get("codec_tag_string") == "mp4a"
    ):
        return "mp4"
    elif info.get("format_name") == "ogg":
        return "ogg"
    elif info.get("format_name") == "matroska,webm":
        return "webm"
    return None


def convert_audio_to_wav(file_path, output_path, conversion_type):
    """Convert MP4/OGG audio file to WAV format."""
    audio = AudioSegment.from_file(file_path, format=conversion_type)
    audio.export(output_path, format="wav")
    log.info(f"Converted {file_path} to {output_path}")


def set_faster_whisper_model(
    model: str,
    auto_update: bool = False,
    compute_type: str = "int8",
    cpu_threads: int = 0,
    num_workers: int = 1,
):
    whisper_model = None
    if model:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            # llens: faster-whisper は local-ml extra。未導入環境(dev 等)で
            # audio config の保存(update_audio_config)を 500 にしない —
            # 内蔵 STT を実際に使う時点(transcribe)で明確に失敗させる
            log.warning(
                "faster-whisper is not installed (local-ml extra); built-in STT is unavailable"
            )
            return None

        faster_whisper_kwargs = {
            "model_size_or_path": model,
            # 2026-10-03: torch による CUDA/MPS 探索(USE_CUDA_DOCKER / DEVICE_TYPE)は撤去。
            # 内蔵 STT は CPU 固定(GPU 配備が現れたら CTranslate2 の device 設定を足す)。
            "device": "cpu",
            "compute_type": compute_type,
            # llens(#53 フォロー): 強い CPU 向けのスレッド系つまみ(config.py 参照)
            "cpu_threads": cpu_threads,
            "num_workers": num_workers,
            "download_root": WHISPER_MODEL_DIR,
            "local_files_only": not auto_update,
        }

        try:
            whisper_model = WhisperModel(**faster_whisper_kwargs)
        except Exception:
            log.warning(
                "WhisperModel initialization failed, attempting download with local_files_only=False"
            )
            faster_whisper_kwargs["local_files_only"] = False
            whisper_model = WhisperModel(**faster_whisper_kwargs)
    return whisper_model


##########################################
#
# Audio API
#
##########################################


class TTSConfigForm(BaseModel):
    ENABLE: bool
    OPENAI_API_BASE_URL: str
    OPENAI_API_KEY: str
    API_KEY: str
    ENGINE: str
    MODEL: str
    VOICE: str
    SPLIT_ON: str
    AZURE_SPEECH_REGION: str
    AZURE_SPEECH_OUTPUT_FORMAT: str


class STTConfigForm(BaseModel):
    ENABLE: bool
    OPENAI_API_BASE_URL: str
    OPENAI_API_KEY: str
    ENGINE: str
    MODEL: str
    WHISPER_MODEL: str
    DEEPGRAM_API_KEY: str
    AZURE_API_KEY: str
    AZURE_REGION: str
    AZURE_LOCALES: str


class AudioConfigUpdateForm(BaseModel):
    tts: TTSConfigForm
    stt: STTConfigForm


@router.get("/config")
async def get_audio_config(request: Request, user=Depends(get_admin_user)):
    return _audio_config_response(request)


def _audio_config_response(request: Request):
    return {
        "tts": {
            "ENABLE": request.app.state.config.ENABLE_AUDIO_TTS,
            "OPENAI_API_BASE_URL": request.app.state.config.TTS_OPENAI_API_BASE_URL,
            "OPENAI_API_KEY": request.app.state.config.TTS_OPENAI_API_KEY,
            "API_KEY": request.app.state.config.TTS_API_KEY,
            "ENGINE": request.app.state.config.TTS_ENGINE,
            "MODEL": request.app.state.config.TTS_MODEL,
            "VOICE": request.app.state.config.TTS_VOICE,
            "SPLIT_ON": request.app.state.config.TTS_SPLIT_ON,
            "AZURE_SPEECH_REGION": request.app.state.config.TTS_AZURE_SPEECH_REGION,
            "AZURE_SPEECH_OUTPUT_FORMAT": request.app.state.config.TTS_AZURE_SPEECH_OUTPUT_FORMAT,
        },
        "stt": {
            "ENABLE": request.app.state.config.ENABLE_AUDIO_STT,
            "OPENAI_API_BASE_URL": request.app.state.config.STT_OPENAI_API_BASE_URL,
            "OPENAI_API_KEY": request.app.state.config.STT_OPENAI_API_KEY,
            "ENGINE": request.app.state.config.STT_ENGINE,
            "MODEL": request.app.state.config.STT_MODEL,
            "WHISPER_MODEL": request.app.state.config.WHISPER_MODEL,
            "DEEPGRAM_API_KEY": request.app.state.config.DEEPGRAM_API_KEY,
            "AZURE_API_KEY": request.app.state.config.AUDIO_STT_AZURE_API_KEY,
            "AZURE_REGION": request.app.state.config.AUDIO_STT_AZURE_REGION,
            "AZURE_LOCALES": request.app.state.config.AUDIO_STT_AZURE_LOCALES,
        },
    }


@router.post("/config/update")
async def update_audio_config(
    request: Request, form_data: AudioConfigUpdateForm, user=Depends(get_admin_user)
):
    request.app.state.config.ENABLE_AUDIO_TTS = form_data.tts.ENABLE
    request.app.state.config.TTS_OPENAI_API_BASE_URL = form_data.tts.OPENAI_API_BASE_URL
    request.app.state.config.TTS_OPENAI_API_KEY = form_data.tts.OPENAI_API_KEY
    request.app.state.config.TTS_API_KEY = form_data.tts.API_KEY
    request.app.state.config.TTS_ENGINE = form_data.tts.ENGINE
    request.app.state.config.TTS_MODEL = form_data.tts.MODEL
    request.app.state.config.TTS_VOICE = form_data.tts.VOICE
    request.app.state.config.TTS_SPLIT_ON = form_data.tts.SPLIT_ON
    request.app.state.config.TTS_AZURE_SPEECH_REGION = form_data.tts.AZURE_SPEECH_REGION
    request.app.state.config.TTS_AZURE_SPEECH_OUTPUT_FORMAT = (
        form_data.tts.AZURE_SPEECH_OUTPUT_FORMAT
    )

    request.app.state.config.ENABLE_AUDIO_STT = form_data.stt.ENABLE
    request.app.state.config.STT_OPENAI_API_BASE_URL = form_data.stt.OPENAI_API_BASE_URL
    request.app.state.config.STT_OPENAI_API_KEY = form_data.stt.OPENAI_API_KEY
    request.app.state.config.STT_ENGINE = form_data.stt.ENGINE
    request.app.state.config.STT_MODEL = form_data.stt.MODEL
    request.app.state.config.WHISPER_MODEL = form_data.stt.WHISPER_MODEL
    request.app.state.config.DEEPGRAM_API_KEY = form_data.stt.DEEPGRAM_API_KEY
    request.app.state.config.AUDIO_STT_AZURE_API_KEY = form_data.stt.AZURE_API_KEY
    request.app.state.config.AUDIO_STT_AZURE_REGION = form_data.stt.AZURE_REGION
    request.app.state.config.AUDIO_STT_AZURE_LOCALES = form_data.stt.AZURE_LOCALES

    if request.app.state.config.STT_ENGINE == "":
        request.app.state.faster_whisper_model = set_faster_whisper_model(
            form_data.stt.WHISPER_MODEL,
            WHISPER_MODEL_AUTO_UPDATE,
            compute_type=request.app.state.config.WHISPER_COMPUTE_TYPE,
            cpu_threads=request.app.state.config.WHISPER_CPU_THREADS,
            num_workers=request.app.state.config.WHISPER_NUM_WORKERS,
        )

    return _audio_config_response(request)


def load_speech_pipeline(request):
    from transformers import pipeline
    from datasets import load_dataset

    if request.app.state.speech_synthesiser is None:
        request.app.state.speech_synthesiser = pipeline(
            "text-to-speech", "microsoft/speecht5_tts"
        )

    if request.app.state.speech_speaker_embeddings_dataset is None:
        request.app.state.speech_speaker_embeddings_dataset = load_dataset(
            "Matthijs/cmu-arctic-xvectors", split="validation"
        )


@router.post("/speech")
async def speech(request: Request, user=Depends(get_verified_user)):
    # llens(#53 フォロー): 配備レベルの機能フラグ(FE のアイコン制御と対のサーバー側 enforcement)
    if not request.app.state.config.ENABLE_AUDIO_TTS:
        raise HTTPException(status_code=403, detail="Text-to-Speech is disabled")

    body = await request.body()

    payload = None
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as e:
        log.exception(e)
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    if request.app.state.config.TTS_ENGINE == "openai":
        # llens(#53): 内部関数 synthesize_speech へ委譲(キャッシュキーに voice を
        # 含む形へ是正)。model は従来どおり config を常用し FE 申告は使わない。
        # sync + requests のため to_thread でループ外へ(#28 のパターン)。
        try:
            cache_path = await asyncio.to_thread(
                synthesize_speech,
                request,
                payload.get("input", ""),
                voice=payload.get("voice"),
            )
            return FileResponse(cache_path)
        except requests.HTTPError as e:
            r = e.response
            detail = None
            try:
                res = r.json()
                if "error" in res:
                    detail = f"External: {res['error'].get('message', '')}"
            except Exception:
                detail = f"External: {e}"

            raise HTTPException(
                status_code=r.status_code if r is not None else 500,
                detail=detail if detail else "Nucllei: Server Connection Error",
            )
        except Exception as e:
            log.exception(e)
            raise HTTPException(
                status_code=500,
                detail="Nucllei: Server Connection Error",
            )

    # 以降のエンジンは従来どおり body ハッシュのキャッシュ
    name = hashlib.sha256(
        body
        + str(request.app.state.config.TTS_ENGINE).encode("utf-8")
        + str(request.app.state.config.TTS_MODEL).encode("utf-8")
    ).hexdigest()

    file_path = SPEECH_CACHE_DIR.joinpath(f"{name}.mp3")
    file_body_path = SPEECH_CACHE_DIR.joinpath(f"{name}.json")

    # Check if the file already exists in the cache
    if file_path.is_file():
        return FileResponse(file_path)

    if request.app.state.config.TTS_ENGINE == "elevenlabs":
        voice_id = payload.get("voice", "")

        # llens(#28): 同期 requests を含む helper は to_thread でループ外へ
        if voice_id not in await asyncio.to_thread(get_available_voices, request):
            raise HTTPException(
                status_code=400,
                detail="Invalid voice id",
            )

        try:
            timeout = aiohttp.ClientTimeout(
                total=AIOHTTP_CLIENT_TIMEOUT,
                connect=AIOHTTP_CLIENT_TIMEOUT_CONNECT,
                sock_read=AIOHTTP_CLIENT_TIMEOUT_SOCK_READ,
            )
            async with aiohttp.ClientSession(
                timeout=timeout, trust_env=True
            ) as session:
                async with session.post(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                    json={
                        "text": payload["input"],
                        "model_id": request.app.state.config.TTS_MODEL,
                        "voice_settings": {"stability": 0.5, "similarity_boost": 0.5},
                    },
                    headers={
                        "Accept": "audio/mpeg",
                        "Content-Type": "application/json",
                        "xi-api-key": request.app.state.config.TTS_API_KEY,
                    },
                ) as r:
                    r.raise_for_status()

                    async with aiofiles.open(file_path, "wb") as f:
                        await f.write(await r.read())

                    async with aiofiles.open(file_body_path, "w") as f:
                        await f.write(json.dumps(payload))

            return FileResponse(file_path)

        except Exception as e:
            log.exception(e)
            detail = None

            try:
                if r.status != 200:
                    res = await r.json()
                    if "error" in res:
                        detail = f"External: {res['error'].get('message', '')}"
            except Exception:
                detail = f"External: {e}"

            raise HTTPException(
                status_code=getattr(r, "status", 500) if r else 500,
                detail=detail if detail else "Nucllei: Server Connection Error",
            )

    elif request.app.state.config.TTS_ENGINE == "azure":
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception as e:
            log.exception(e)
            raise HTTPException(status_code=400, detail="Invalid JSON payload")

        region = request.app.state.config.TTS_AZURE_SPEECH_REGION
        language = request.app.state.config.TTS_VOICE
        locale = "-".join(request.app.state.config.TTS_VOICE.split("-")[:1])
        output_format = request.app.state.config.TTS_AZURE_SPEECH_OUTPUT_FORMAT

        try:
            data = f"""<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="{locale}">
                <voice name="{language}">{payload["input"]}</voice>
            </speak>"""
            timeout = aiohttp.ClientTimeout(
                total=AIOHTTP_CLIENT_TIMEOUT,
                connect=AIOHTTP_CLIENT_TIMEOUT_CONNECT,
                sock_read=AIOHTTP_CLIENT_TIMEOUT_SOCK_READ,
            )
            async with aiohttp.ClientSession(
                timeout=timeout, trust_env=True
            ) as session:
                async with session.post(
                    f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
                    headers={
                        "Ocp-Apim-Subscription-Key": request.app.state.config.TTS_API_KEY,
                        "Content-Type": "application/ssml+xml",
                        "X-Microsoft-OutputFormat": output_format,
                    },
                    data=data,
                ) as r:
                    r.raise_for_status()

                    async with aiofiles.open(file_path, "wb") as f:
                        await f.write(await r.read())

                    async with aiofiles.open(file_body_path, "w") as f:
                        await f.write(json.dumps(payload))

                    return FileResponse(file_path)

        except Exception as e:
            log.exception(e)
            detail = None

            try:
                if r.status != 200:
                    res = await r.json()
                    if "error" in res:
                        detail = f"External: {res['error'].get('message', '')}"
            except Exception:
                detail = f"External: {e}"

            raise HTTPException(
                status_code=getattr(r, "status", 500) if r else 500,
                detail=detail if detail else "Nucllei: Server Connection Error",
            )

    elif request.app.state.config.TTS_ENGINE == "transformers":
        payload = None
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception as e:
            log.exception(e)
            raise HTTPException(status_code=400, detail="Invalid JSON payload")

        import torch
        import soundfile as sf

        load_speech_pipeline(request)

        embeddings_dataset = request.app.state.speech_speaker_embeddings_dataset

        speaker_index = 6799
        try:
            speaker_index = embeddings_dataset["filename"].index(
                request.app.state.config.TTS_MODEL
            )
        except Exception:
            pass

        speaker_embedding = torch.tensor(
            embeddings_dataset[speaker_index]["xvector"]
        ).unsqueeze(0)

        speech = request.app.state.speech_synthesiser(
            payload["input"],
            forward_params={"speaker_embeddings": speaker_embedding},
        )

        sf.write(file_path, speech["audio"], samplerate=speech["sampling_rate"])

        async with aiofiles.open(file_body_path, "w") as f:
            await f.write(json.dumps(payload))

        return FileResponse(file_path)


# 転写に渡す文脈(prompt)の上限文字数。Whisper の prompt はデコーダ文脈の半分 = 224 トークン
# までしか効かず、超えた分は先頭側が捨てられる(効くのは末尾)。large-v3-turbo の tokenizer で
# 数えると日本語は 1 文字 0.8〜1.1 トークン(200 文字 = 160〜220 トークンで枠にほぼ収まる)、
# 英語は 1 文字 0.23 トークン前後(同 50 トークン弱)。これ以上は受け取らない
# (外部 STT へ転送する量の上限でもある)
STT_PROMPT_MAX_CHARS = 200


def normalize_stt_prompt(prompt: str | None) -> str | None:
    """prompt を末尾 STT_PROMPT_MAX_CHARS 文字に丸める。空・空白だけなら None。"""
    prompt = (prompt or "").strip()
    if not prompt:
        return None
    return prompt[-STT_PROMPT_MAX_CHARS:].lstrip()


def transcribe(request: Request, file_path, prompt: str | None = None):
    # prompt = 転写の文脈(用語リスト等を渡したい呼び出し元のための任意引数。マイクの音声入力は
    # 送らない — _memos/audio.md)。呼び出し元の内容そのものなので、ログには長さだけを出す
    prompt = normalize_stt_prompt(prompt)
    log.info(f"transcribe: {file_path} (prompt: {len(prompt) if prompt else 0} chars)")
    filename = os.path.basename(file_path)
    file_dir = os.path.dirname(file_path)
    id = filename.split(".")[0]

    if request.app.state.config.STT_ENGINE == "":
        if request.app.state.faster_whisper_model is None:
            request.app.state.faster_whisper_model = set_faster_whisper_model(
                request.app.state.config.WHISPER_MODEL,
                compute_type=request.app.state.config.WHISPER_COMPUTE_TYPE,
                cpu_threads=request.app.state.config.WHISPER_CPU_THREADS,
                num_workers=request.app.state.config.WHISPER_NUM_WORKERS,
            )

        model = request.app.state.faster_whisper_model
        if model is None:
            # set_faster_whisper_model が import 失敗で None を返した場合の明確なエラー
            # (transcription() が catch して 400 + メッセージにする)
            raise Exception(
                "Built-in STT requires faster-whisper (install the local-ml extra), "
                "or configure an OpenAI-compatible STT engine"
            )
        segments, info = model.transcribe(
            file_path,
            beam_size=request.app.state.WHISPER_BEAM_SIZE,
            vad_filter=request.app.state.config.WHISPER_VAD_FILTER,
            # llens: 言語固定(空なら従来通り自動判定)。北大運用は ja 固定
            language=request.app.state.config.WHISPER_LANGUAGE or None,
            # None = 渡さないのと同じ(faster-whisper の既定値)
            initial_prompt=prompt,
        )
        log.info(
            "Detected language '%s' with probability %f"
            % (info.language, info.language_probability)
        )

        transcript = "".join([segment.text for segment in list(segments)])
        data = {"text": transcript.strip()}

        # save the transcript to a json file
        transcript_file = f"{file_dir}/{id}.json"
        with open(transcript_file, "w") as f:
            json.dump(data, f)

        log.debug(data)
        return data
    elif request.app.state.config.STT_ENGINE == "openai":
        audio_format = get_audio_format(file_path)
        if audio_format:
            os.rename(file_path, file_path.replace(".wav", f".{audio_format}"))
            # Convert unsupported audio file to WAV format
            convert_audio_to_wav(
                file_path.replace(".wav", f".{audio_format}"),
                file_path,
                audio_format,
            )

        r = None
        try:
            r = requests.post(
                url=f"{request.app.state.config.STT_OPENAI_API_BASE_URL}/audio/transcriptions",
                headers={
                    "Authorization": f"Bearer {request.app.state.config.STT_OPENAI_API_KEY}"
                },
                files={"file": (filename, open(file_path, "rb"))},
                # prompt は OpenAI transcriptions API の同名フィールド。無ければ載せない
                data={
                    "model": request.app.state.config.STT_MODEL,
                    **({"prompt": prompt} if prompt else {}),
                },
                timeout=AUDIO_REQUEST_TIMEOUT,
            )

            r.raise_for_status()
            data = r.json()

            # save the transcript to a json file
            transcript_file = f"{file_dir}/{id}.json"
            with open(transcript_file, "w") as f:
                json.dump(data, f)

            return data
        except Exception as e:
            log.exception(e)

            detail = None
            if r is not None:
                try:
                    res = r.json()
                    if "error" in res:
                        detail = f"External: {res['error'].get('message', '')}"
                except Exception:
                    # 例外文言(requests の HTTPError)は STT の URL を含む — tool 経由で
                    # モデルに届きうるので載せない(全文は上の log.exception に出ている)
                    detail = f"External: 外部の文字起こしエンジンが HTTP {r.status_code} を返した"

            raise Exception(detail if detail else "Nucllei: Server Connection Error")

    elif request.app.state.config.STT_ENGINE == "deepgram":
        # Deepgram の listen API には転写の文脈を渡す口が無いので、prompt は無視する
        try:
            # Determine the MIME type of the file
            mime, _ = mimetypes.guess_type(file_path)
            if not mime:
                mime = "audio/wav"  # fallback to wav if undetectable

            # Read the audio file
            with open(file_path, "rb") as f:
                file_data = f.read()

            # Build headers and parameters
            headers = {
                "Authorization": f"Token {request.app.state.config.DEEPGRAM_API_KEY}",
                "Content-Type": mime,
            }

            # Add model if specified
            params = {}
            if request.app.state.config.STT_MODEL:
                params["model"] = request.app.state.config.STT_MODEL

            # Make request to Deepgram API
            r = requests.post(
                "https://api.deepgram.com/v1/listen",
                headers=headers,
                params=params,
                data=file_data,
                timeout=AUDIO_REQUEST_TIMEOUT,
            )
            r.raise_for_status()
            response_data = r.json()

            # Extract transcript from Deepgram response
            try:
                transcript = response_data["results"]["channels"][0]["alternatives"][
                    0
                ].get("transcript", "")
            except (KeyError, IndexError) as e:
                log.error(f"Malformed response from Deepgram: {str(e)}")
                raise Exception(
                    "Failed to parse Deepgram response - unexpected response format"
                )
            data = {"text": transcript.strip()}

            # Save transcript
            transcript_file = f"{file_dir}/{id}.json"
            with open(transcript_file, "w") as f:
                json.dump(data, f)

            return data

        except Exception as e:
            log.exception(e)
            detail = None
            if r is not None:
                try:
                    res = r.json()
                    if "error" in res:
                        detail = f"External: {res['error'].get('message', '')}"
                except Exception:
                    # 例外文言(requests の HTTPError)は STT の URL を含む — tool 経由で
                    # モデルに届きうるので載せない(全文は上の log.exception に出ている)
                    detail = f"External: 外部の文字起こしエンジンが HTTP {r.status_code} を返した"
            raise Exception(detail if detail else "Nucllei: Server Connection Error")

    elif request.app.state.config.STT_ENGINE == "azure":
        # Azure の transcribe API にも転写の文脈を渡す口が無いので、prompt は無視する
        # Check file exists and size
        if not os.path.exists(file_path):
            raise HTTPException(status_code=400, detail="Audio file not found")

        # Check file size (Azure has a larger limit of 200MB)
        file_size = os.path.getsize(file_path)
        if file_size > AZURE_MAX_FILE_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"File size exceeds Azure's limit of {AZURE_MAX_FILE_SIZE_MB}MB",
            )

        api_key = request.app.state.config.AUDIO_STT_AZURE_API_KEY
        region = request.app.state.config.AUDIO_STT_AZURE_REGION
        locales = request.app.state.config.AUDIO_STT_AZURE_LOCALES

        # IF NO LOCALES, USE DEFAULTS
        if len(locales) < 2:
            locales = [
                "en-US",
                "es-ES",
                "es-MX",
                "fr-FR",
                "hi-IN",
                "it-IT",
                "de-DE",
                "en-GB",
                "en-IN",
                "ja-JP",
                "ko-KR",
                "pt-BR",
                "zh-CN",
            ]
            locales = ",".join(locales)

        if not api_key or not region:
            raise HTTPException(
                status_code=400,
                detail="Azure API key and region are required for Azure STT",
            )

        r = None
        try:
            # Prepare the request
            data = {
                "definition": json.dumps(
                    {
                        "locales": locales.split(","),
                        "diarization": {"maxSpeakers": 3, "enabled": True},
                    }
                    if locales
                    else {}
                )
            }
            url = f"https://{region}.api.cognitive.microsoft.com/speechtotext/transcriptions:transcribe?api-version=2024-11-15"

            # Use context manager to ensure file is properly closed
            with open(file_path, "rb") as audio_file:
                r = requests.post(
                    url=url,
                    files={"audio": audio_file},
                    data=data,
                    headers={
                        "Ocp-Apim-Subscription-Key": api_key,
                    },
                    timeout=AUDIO_REQUEST_TIMEOUT,
                )

            r.raise_for_status()
            response = r.json()

            # Extract transcript from response
            if not response.get("combinedPhrases"):
                raise ValueError("No transcription found in response")

            # Get the full transcript from combinedPhrases
            transcript = response["combinedPhrases"][0].get("text", "").strip()
            if not transcript:
                raise ValueError("Empty transcript in response")

            data = {"text": transcript}

            # Save transcript to json file (consistent with other providers)
            transcript_file = f"{file_dir}/{id}.json"
            with open(transcript_file, "w") as f:
                json.dump(data, f)

            log.debug(data)
            return data

        except (KeyError, IndexError, ValueError) as e:
            log.exception("Error parsing Azure response")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to parse Azure response: {str(e)}",
            )
        except requests.exceptions.RequestException as e:
            log.exception(e)
            detail = None

            try:
                if r is not None and r.status_code != 200:
                    res = r.json()
                    if "error" in res:
                        detail = f"External: {res['error'].get('message', '')}"
            except Exception:
                detail = f"External: {e}"

            raise HTTPException(
                status_code=getattr(r, "status_code", 500) if r else 500,
                detail=detail if detail else "Nucllei: Server Connection Error",
            )


def compress_audio(file_path):
    if os.path.getsize(file_path) > MAX_FILE_SIZE:
        file_dir = os.path.dirname(file_path)
        audio = AudioSegment.from_file(file_path)
        audio = audio.set_frame_rate(16000).set_channels(1)  # Compress audio
        compressed_path = f"{file_dir}/{id}_compressed.opus"
        audio.export(compressed_path, format="opus", bitrate="32k")
        log.debug(f"Compressed audio to {compressed_path}")

        if (
            os.path.getsize(compressed_path) > MAX_FILE_SIZE
        ):  # Still larger than MAX_FILE_SIZE after compression
            raise Exception(ERROR_MESSAGES.FILE_TOO_LARGE(size=f"{MAX_FILE_SIZE_MB}MB"))
        return compressed_path
    else:
        return file_path


@router.post("/transcriptions")
def transcription(
    request: Request,
    file: UploadFile = File(...),
    # OpenAI の transcriptions API と同名の任意フィールド。内蔵 faster-whisper では initial_prompt に
    # なる(省略時は従来と同じ)。長さの丸めは transcribe() 側。マイクの音声入力(FE)は送らない
    prompt: str | None = Form(None),
    user=Depends(get_verified_user),
):
    # llens(#53 フォロー): 配備レベルの機能フラグ(FE のアイコン制御と対のサーバー側 enforcement)
    if not request.app.state.config.ENABLE_AUDIO_STT:
        raise HTTPException(status_code=403, detail="Speech-to-Text is disabled")

    log.info(f"file.content_type: {file.content_type}")

    # llens(#56): MediaRecorder の既定コンテナ(Chrome/Edge = audio/webm;codecs=opus)を
    # そのままの MIME で受ける。FE が audio/wav を騙って送る回避をやめるための追加。
    supported_filetypes = (
        "audio/mpeg",
        "audio/wav",
        "audio/ogg",
        "audio/x-m4a",
        "audio/webm",
        "audio/mp4",
    )

    if not file.content_type.startswith(supported_filetypes):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.FILE_NOT_SUPPORTED,
        )

    try:
        # llens(GHSA-m8f9-9whg-f4xr / CVE-2026-45315): 保存拡張子をユーザー提供の
        # filename から取ると、audio content-type を詐称した polyglot(WAV+HTML)を
        # .html 等で保存できてしまう。検証済み content-type から安全な音声拡張子を
        # サーバ側で決める(結果は必ず非アクティブな音声拡張子に限定される)。
        # ※ #94 で保存先を一時ディレクトリに変更済み(/cache 配信には乗らない)が、
        #   拡張子詐称の防御はそのまま維持する。
        content_type_to_ext = {
            "audio/mpeg": "mp3",
            "audio/wav": "wav",
            "audio/ogg": "ogg",
            "audio/x-m4a": "m4a",
            "audio/webm": "webm",
            "audio/mp4": "mp4",
        }
        ext = next(
            (
                e
                for ct, e in content_type_to_ext.items()
                if file.content_type.startswith(ct)
            ),
            "bin",
        )
        id = uuid.uuid4()

        filename = f"{id}.{ext}"
        contents = file.file.read()

        # llens(#94): 受信音声・派生物({id}.json / compressed 等)は一時ディレクトリで
        # 完結させ、処理後にディレクトリごと自動削除する。旧実装の CACHE_DIR 配下は
        # /cache として無認証配信されるため、そこに実体を無期限に残さない
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = f"{tmp_dir}/{filename}"

            with open(file_path, "wb") as f:
                f.write(contents)

            try:
                try:
                    file_path = compress_audio(file_path)
                except Exception as e:
                    log.exception(e)

                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=ERROR_MESSAGES.DEFAULT(e),
                    )

                data = transcribe(request, file_path, prompt)
                file_path = file_path.split("/")[-1]
                return {**data, "filename": file_path}
            except Exception as e:
                log.exception(e)

                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT(e),
                )

    except Exception as e:
        log.exception(e)

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


def get_available_models(request: Request) -> list[dict]:
    available_models = []
    if request.app.state.config.TTS_ENGINE == "openai":
        # Use custom endpoint if not using the official OpenAI API URL
        if not request.app.state.config.TTS_OPENAI_API_BASE_URL.startswith(
            "https://api.openai.com"
        ):
            try:
                response = requests.get(
                    f"{request.app.state.config.TTS_OPENAI_API_BASE_URL}/audio/models",
                    timeout=AUDIO_LIST_TIMEOUT,
                )
                response.raise_for_status()
                data = response.json()
                available_models = data.get("models", [])
            except Exception as e:
                log.error(f"Error fetching models from custom endpoint: {str(e)}")
                available_models = [{"id": "tts-1"}, {"id": "tts-1-hd"}]
        else:
            available_models = [{"id": "tts-1"}, {"id": "tts-1-hd"}]
    elif request.app.state.config.TTS_ENGINE == "elevenlabs":
        try:
            response = requests.get(
                "https://api.elevenlabs.io/v1/models",
                headers={
                    "xi-api-key": request.app.state.config.TTS_API_KEY,
                    "Content-Type": "application/json",
                },
                timeout=AUDIO_LIST_TIMEOUT,
            )
            response.raise_for_status()
            models = response.json()

            available_models = [
                {"name": model["name"], "id": model["model_id"]} for model in models
            ]
        except requests.RequestException as e:
            log.error(f"Error fetching voices: {str(e)}")
    return available_models


@router.get("/models")
async def get_models(request: Request, user=Depends(get_verified_user)):
    # llens(#28): 同期 requests を含む helper は to_thread でループ外へ
    return {"models": await asyncio.to_thread(get_available_models, request)}


def get_available_voices(request) -> dict:
    """Returns {voice_id: voice_name} dict"""
    available_voices = {}
    if request.app.state.config.TTS_ENGINE == "openai":
        # Use custom endpoint if not using the official OpenAI API URL
        if not request.app.state.config.TTS_OPENAI_API_BASE_URL.startswith(
            "https://api.openai.com"
        ):
            try:
                response = requests.get(
                    f"{request.app.state.config.TTS_OPENAI_API_BASE_URL}/audio/voices",
                    timeout=AUDIO_LIST_TIMEOUT,
                )
                response.raise_for_status()
                data = response.json()
                voices_list = data.get("voices", [])
                available_voices = {voice["id"]: voice["name"] for voice in voices_list}
            except Exception as e:
                log.error(f"Error fetching voices from custom endpoint: {str(e)}")
                available_voices = {
                    "alloy": "alloy",
                    "echo": "echo",
                    "fable": "fable",
                    "onyx": "onyx",
                    "nova": "nova",
                    "shimmer": "shimmer",
                }
        else:
            available_voices = {
                "alloy": "alloy",
                "echo": "echo",
                "fable": "fable",
                "onyx": "onyx",
                "nova": "nova",
                "shimmer": "shimmer",
            }
    elif request.app.state.config.TTS_ENGINE == "elevenlabs":
        try:
            available_voices = get_elevenlabs_voices(
                api_key=request.app.state.config.TTS_API_KEY
            )
        except Exception:
            # Avoided @lru_cache with exception
            pass
    elif request.app.state.config.TTS_ENGINE == "azure":
        try:
            region = request.app.state.config.TTS_AZURE_SPEECH_REGION
            url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/voices/list"
            headers = {
                "Ocp-Apim-Subscription-Key": request.app.state.config.TTS_API_KEY
            }

            response = requests.get(url, headers=headers, timeout=AUDIO_LIST_TIMEOUT)
            response.raise_for_status()
            voices = response.json()

            for voice in voices:
                available_voices[voice["ShortName"]] = (
                    f"{voice['DisplayName']} ({voice['ShortName']})"
                )
        except requests.RequestException as e:
            log.error(f"Error fetching voices: {str(e)}")

    return available_voices


@lru_cache
def get_elevenlabs_voices(api_key: str) -> dict:
    """
    Note, set the following in your .env file to use Elevenlabs:
    AUDIO_TTS_ENGINE=elevenlabs
    AUDIO_TTS_API_KEY=sk_...  # Your Elevenlabs API key
    AUDIO_TTS_VOICE=EXAVITQu4vr4xnSDxMaL  # From https://api.elevenlabs.io/v1/voices
    AUDIO_TTS_MODEL=eleven_multilingual_v2
    """

    try:
        # TODO: Add retries
        response = requests.get(
            "https://api.elevenlabs.io/v1/voices",
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
            },
            timeout=AUDIO_LIST_TIMEOUT,
        )
        response.raise_for_status()
        voices_data = response.json()

        voices = {}
        for voice in voices_data.get("voices", []):
            voices[voice["voice_id"]] = voice["name"]
    except requests.RequestException as e:
        # Avoid @lru_cache with exception
        log.error(f"Error fetching voices: {str(e)}")
        raise RuntimeError(f"Error fetching voices: {str(e)}")

    return voices


@router.get("/voices")
async def get_voices(request: Request, user=Depends(get_verified_user)):
    # llens(#28): 同期 requests を含む helper は to_thread でループ外へ
    available_voices = await asyncio.to_thread(get_available_voices, request)
    return {"voices": [{"id": k, "name": v} for k, v in available_voices.items()]}

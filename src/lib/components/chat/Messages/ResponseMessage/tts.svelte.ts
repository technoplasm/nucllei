/**
 * ResponseMessage の読み上げ(TTS)状態機械。
 *
 * MessageActions の Read Aloud ボタンが唯一の利用者。UI から切り離した
 * runes クラス(.svelte.ts)で、`speaking` / `loadingSpeech` だけを公開する。
 * エンジン分岐は元実装のまま:
 * - config.audio.tts.engine === '' → ブラウザ speechSynthesis
 * - settings.audio.tts.engine === 'browser-kokoro' → KokoroWorker(WASM)
 * - それ以外 → backend の OpenAI 互換 TTS(synthesizeOpenAISpeech)
 */
import { get } from 'svelte/store';
import { toast } from 'svelte-sonner';

import { config, settings, TTSWorker } from '$lib/stores';
import { synthesizeOpenAISpeech } from '$lib/apis/audio';
import { getMessageContentParts } from '$lib/utils';
import { KokoroWorker } from '$lib/workers/KokoroWorker';

export class MessageSpeech {
	speaking = $state(false);
	loadingSpeech = $state(false);

	#audioParts: Record<number, HTMLAudioElement | null> = {};
	#speakingIdx: number | undefined = undefined;
	#t: (key: string) => string;

	constructor(t: (key: string) => string) {
		this.#t = t;
	}

	#playAudio(idx: number): Promise<void> {
		return new Promise<void>((res) => {
			this.#speakingIdx = idx;
			const audio = this.#audioParts[idx];

			if (!audio) {
				return res();
			}

			audio.play();
			audio.onended = async () => {
				await new Promise((r) => setTimeout(r, 300));

				if (Object.keys(this.#audioParts).length - 1 === idx) {
					this.speaking = false;
				}

				res();
			};
		});
	}

	#stop() {
		try {
			speechSynthesis.cancel();

			const current = this.#speakingIdx !== undefined ? this.#audioParts[this.#speakingIdx] : null;
			if (current) {
				current.pause();
				current.currentTime = 0;
			}
		} catch {
			// 再生していないときの cancel/pause 失敗は無視
		}

		this.speaking = false;
		this.#speakingIdx = undefined;
	}

	async toggle(content: string) {
		if (this.speaking) {
			this.#stop();
			return;
		}

		if (!(content ?? '').trim().length) {
			toast.info(this.#t('No content to speak'));
			return;
		}

		this.speaking = true;

		const currentConfig = get(config);
		const currentSettings = get(settings);
		const voice = currentSettings?.audio?.tts?.voice ?? currentConfig?.audio?.tts?.voice ?? '';

		if ((currentConfig?.audio?.tts?.engine ?? '') === '') {
			// ブラウザ内蔵 speechSynthesis。getVoices() が空で返る間はポーリングする
			const getVoicesLoop = setInterval(() => {
				const voices = speechSynthesis.getVoices();
				if (voices.length > 0) {
					clearInterval(getVoicesLoop);

					const speak = new SpeechSynthesisUtterance(content);
					speak.rate = currentSettings.audio?.tts?.playbackRate ?? 1;

					speak.onend = () => {
						this.speaking = false;
						if (currentSettings.conversationMode) {
							document.getElementById('voice-input-button')?.click();
						}
					};

					const matched = voices.find((v) => v.voiceURI === voice);
					if (matched) {
						speak.voice = matched;
					}

					speechSynthesis.speak(speak);
				}
			}, 100);
			return;
		}

		this.loadingSpeech = true;

		const messageContentParts: string[] = getMessageContentParts(
			content,
			currentConfig?.audio?.tts?.split_on ?? 'punctuation'
		);

		if (!messageContentParts.length) {
			toast.info(this.#t('No content to speak'));

			this.speaking = false;
			this.loadingSpeech = false;
			return;
		}

		this.#audioParts = messageContentParts.reduce(
			(acc, _sentence, idx) => {
				acc[idx] = null;
				return acc;
			},
			{} as Record<number, HTMLAudioElement | null>
		);

		const onError = (error: unknown) => {
			console.error(error);
			toast.error(`${error}`);

			this.speaking = false;
			this.loadingSpeech = false;
		};

		// 直前パートの再生完了に次パートをチェーンする(合成と再生のパイプライン化)
		let lastPlayedAudioPromise = Promise.resolve();

		if (currentSettings.audio?.tts?.engine === 'browser-kokoro') {
			if (!get(TTSWorker)) {
				TTSWorker.set(new KokoroWorker(currentSettings.audio?.tts?.engineConfig?.dtype ?? 'fp32'));
				await get(TTSWorker)?.init();
			}

			for (const [idx, sentence] of messageContentParts.entries()) {
				const blobUrl = await get(TTSWorker)?.generate({ text: sentence, voice }).catch(onError);

				if (blobUrl) {
					const audio = new Audio(blobUrl);
					audio.playbackRate = currentSettings.audio?.tts?.playbackRate ?? 1;

					this.#audioParts[idx] = audio;
					this.loadingSpeech = false;
					lastPlayedAudioPromise = lastPlayedAudioPromise.then(() => this.#playAudio(idx));
				}
			}
		} else {
			for (const [idx, sentence] of messageContentParts.entries()) {
				const res = await synthesizeOpenAISpeech(
					currentSettings?.audio?.tts?.defaultVoice === currentConfig?.audio?.tts?.voice
						? voice
						: (currentConfig?.audio?.tts?.voice ?? ''),
					sentence
				).catch(onError);

				if (res) {
					const blob = await res.blob();
					const blobUrl = URL.createObjectURL(blob);
					const audio = new Audio(blobUrl);
					audio.playbackRate = currentSettings.audio?.tts?.playbackRate ?? 1;

					this.#audioParts[idx] = audio;
					this.loadingSpeech = false;
					lastPlayedAudioPromise = lastPlayedAudioPromise.then(() => this.#playAudio(idx));
				}
			}
		}
	}
}

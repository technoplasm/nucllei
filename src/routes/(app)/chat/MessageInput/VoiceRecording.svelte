<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { tick, getContext, onMount, onDestroy, untrack } from 'svelte';
	import { config, settings } from '$lib/stores';
	import { blobToFile } from '$lib/utils';

	import { transcribeAudio } from '$lib/apis/audio';

	const i18n = getContext('i18n');

	let {
		recording = $bindable(false),
		className = ' p-2.5 w-full max-w-full',
		onConfirm = () => {},
		onTranscript = () => {},
		onCancel = () => {}
	}: {
		recording?: boolean;
		className?: string;
		onConfirm?: (data: { text: string; filename?: string }) => void;
		// llens(#56): 逐次文字起こしで確定したチャンクを録音中に親へ渡す(入力欄へ追記)
		onTranscript?: (data: { text: string }) => void;
		onCancel?: () => void;
	} = $props();

	// llens(#56): 逐次文字起こし(疑似ストリーミング)のパラメータ。
	// 既定値は Settings > Chats > Voice の同名行と揃える(根拠は _memos/audio.md)
	const DEFAULT_SILENCE_TIMEOUT = 4000;
	const DEFAULT_MIN_CHUNK_DURATION = 1000;

	const silenceTimeout = $derived($settings?.voiceSilenceTimeout ?? DEFAULT_SILENCE_TIMEOUT);
	const minChunkDuration = $derived($settings?.voiceMinChunkDuration ?? DEFAULT_MIN_CHUNK_DURATION);

	let loading = $state(false);

	let durationSeconds = $state(0);
	let durationCounter: ReturnType<typeof setInterval> | undefined;

	let transcription = '';

	// 録音中に確定したテキスト(入力欄は録音バーに隠れるので、ここにも出して見せる)
	let dictationText = $state('');
	let transcriptElement = $state<HTMLDivElement | null>(null);

	const startDurationCounter = () => {
		durationCounter = setInterval(() => {
			durationSeconds++;
		}, 1000);
	};

	const stopDurationCounter = () => {
		clearInterval(durationCounter);
		durationSeconds = 0;
	};

	// recording の on/off で録音を起動/停止する(マウント時は recording=true で開始)。
	// start/stop 内の状態読み書きで effect が再発火しないよう untrack で包む(runes 化の追跡罠)。
	$effect(() => {
		if (recording) {
			untrack(() => startRecording());
		} else {
			untrack(() => stopRecording());
		}
	});

	// 確定テキストが伸びたら末尾へスクロール
	$effect(() => {
		dictationText;
		if (transcriptElement) {
			transcriptElement.scrollTop = transcriptElement.scrollHeight;
		}
	});

	const formatSeconds = (seconds: number) => {
		const minutes = Math.floor(seconds / 60);
		const remainingSeconds = seconds % 60;
		const formattedSeconds = remainingSeconds < 10 ? `0${remainingSeconds}` : remainingSeconds;
		return `${minutes}:${formattedSeconds}`;
	};

	// Web Speech API は lib.dom に型が無いため、使用箇所に必要な最小限のローカル型を置く
	type SpeechRecognitionLike = {
		continuous: boolean;
		start: () => void;
		stop: () => void;
		onresult: ((event: { results: Record<number, { transcript: string }[]> }) => void) | null;
		onend: (() => void) | null;
		onerror: ((event: { error: string }) => void) | null;
	};

	let stream: MediaStream | null = null;
	let audioContext: AudioContext | null = null;
	let speechRecognition: SpeechRecognitionLike | undefined;

	let mediaRecorder: MediaRecorder | undefined;
	let audioChunks: Blob[] = [];
	// 現セグメントの録音開始時刻(0 = まだ録っていない)
	let segmentStartedAt = 0;
	// 確定ボタン後の最終セグメント処理中(この stop では次のセグメントを起こさない)
	let finishing = false;
	// stop() 済みで onstop 待ち(無音カットと確定ボタンが同フレームで競合するのを防ぐ)
	let stopping = false;
	// finish() は 1 度だけ(onConfirm の二重発火 = 入力欄への二重挿入を防ぐ)
	let finalized = false;
	// キャンセル/停止後に遅れて返ってきた転写を捨てるためのフラグ
	let aborted = false;
	// チャンクの POST は順序保証が要る(並列に投げると文が入れ替わる)ので直列キューに積む
	let transcribeQueue: Promise<void> = Promise.resolve();

	const MIN_DECIBELS = -45;
	let VISUALIZER_BUFFER_LENGTH = 300;

	let visualizerData = $state(Array(VISUALIZER_BUFFER_LENGTH).fill(0));

	const usesWebSpeech = () =>
		$config?.audio?.stt?.engine === 'web' || ($settings?.audio?.stt?.engine ?? '') === 'web';

	// llens(#56): backend の許可 MIME(audio/webm|ogg|wav|mpeg|x-m4a)に載る型を選ぶ。
	// 選べなければブラウザ既定に任せ、実際の MIME をそのまま送る(wav 詐称はしない)。
	const pickMimeType = () => {
		if (typeof MediaRecorder === 'undefined' || !MediaRecorder.isTypeSupported) {
			return '';
		}
		for (const type of ['audio/webm', 'audio/ogg']) {
			if (MediaRecorder.isTypeSupported(type)) {
				return type;
			}
		}
		return '';
	};

	const extensionFor = (mimeType: string) => {
		const base = (mimeType ?? '').split(';')[0];
		return (
			{
				'audio/webm': 'webm',
				'audio/ogg': 'ogg',
				'audio/mp4': 'mp4',
				'audio/mpeg': 'mp3',
				'audio/wav': 'wav'
			}[base] ?? 'webm'
		);
	};

	// Function to calculate the RMS level from time domain data
	const calculateRMS = (data: Uint8Array) => {
		let sumSquares = 0;
		for (let i = 0; i < data.length; i++) {
			const normalizedValue = (data[i] - 128) / 128; // Normalize the data
			sumSquares += normalizedValue * normalizedValue;
		}
		return Math.sqrt(sumSquares / data.length);
	};

	const normalizeRMS = (rms: number) => {
		rms = rms * 10;
		const exp = 1.5; // Adjust exponent value; values greater than 1 expand larger numbers more and compress smaller numbers more
		const scaledRMS = Math.pow(rms, exp);

		// Scale between 0.01 (1%) and 1.0 (100%)
		return Math.min(1.0, Math.max(0.01, scaledRMS));
	};

	// 波形の描画と、発話区間の切り出し(#56)を 1 本の rAF ループで回す。
	// timeslice で細切れにしないのは webm/opus の 2 個目以降が単体でデコードできないため —
	// 無音の切れ目で MediaRecorder を stop → 転写キューへ積む → すぐ次のセグメントを起こす
	// (CallOverlay の stopRecordingCallback と同じ形)。
	const analyseAudio = (stream: MediaStream) => {
		audioContext = new AudioContext();
		const audioStreamSource = audioContext.createMediaStreamSource(stream);

		const analyser = audioContext.createAnalyser();
		analyser.minDecibels = MIN_DECIBELS;
		audioStreamSource.connect(analyser);

		const bufferLength = analyser.frequencyBinCount;

		const domainData = new Uint8Array(bufferLength);
		const timeDomainData = new Uint8Array(analyser.fftSize);

		let lastSoundTime = Date.now();
		let speaking = false;

		const detectSound = () => {
			const processFrame = () => {
				if (!recording || loading) return;

				analyser.getByteTimeDomainData(timeDomainData);
				analyser.getByteFrequencyData(domainData);

				// Calculate RMS level from time domain data
				const rmsLevel = calculateRMS(timeDomainData);
				// Push the calculated decibel level to visualizerData
				visualizerData.push(normalizeRMS(rmsLevel));

				// Ensure visualizerData array stays within the buffer length
				if (visualizerData.length >= VISUALIZER_BUFFER_LENGTH) {
					visualizerData.shift();
				}

				visualizerData = visualizerData;

				const hasSound = domainData.some((value) => value > 0);
				if (hasSound) {
					// 発話が始まったところで録音開始(無音だけのセグメントを作らない)
					if (mediaRecorder && mediaRecorder.state === 'inactive') {
						mediaRecorder.start();
						segmentStartedAt = Date.now();
					}
					speaking = true;
					lastSoundTime = Date.now();
				}

				// 「読み取り猶予」ぶん無音が続いたら発話の区切りとみなす
				if (speaking && Date.now() - lastSoundTime > silenceTimeout) {
					speaking = false;
					if (mediaRecorder && mediaRecorder.state === 'recording') {
						stopping = true;
						mediaRecorder.stop();
					}
				}

				window.requestAnimationFrame(processFrame);
			};

			window.requestAnimationFrame(processFrame);
		};

		detectSound();
	};

	const transcribeSegment = async (chunks: Blob[], mimeType: string) => {
		await tick();
		const blob = new Blob(chunks, { type: mimeType });
		const file = blobToFile(blob, `recording.${extensionFor(mimeType)}`);

		const res = await transcribeAudio(file).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		return `${res?.text ?? ''}`.trim();
	};

	// 転写は直列キュー。並列に投げると順不同で追記され文が壊れる
	const enqueueSegment = (chunks: Blob[], duration: number, mimeType: string, isFinal: boolean) => {
		transcribeQueue = transcribeQueue.then(async () => {
			let text = '';

			// 短すぎるチャンクは精度が落ちるので送らない(拾い損ねた物音もここで落ちる)
			if (!aborted && chunks.length > 0 && duration >= minChunkDuration) {
				text = await transcribeSegment(chunks, mimeType);
			}

			if (aborted) return;

			if (isFinal) {
				await finish(text);
			} else if (text) {
				dictationText = `${dictationText}${text} `;
				onTranscript({ text });
			}
		});
	};

	const startSegment = () => {
		if (!stream || aborted) return;

		const mimeType = pickMimeType();
		const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);

		audioChunks = [];
		segmentStartedAt = 0;

		recorder.ondataavailable = (event) => {
			if (event.data.size > 0) {
				audioChunks.push(event.data);
			}
		};

		recorder.onstop = () => {
			stopping = false;

			const chunks = audioChunks.slice(0);
			const duration = segmentStartedAt === 0 ? 0 : Date.now() - segmentStartedAt;
			const isFinal = finishing;

			audioChunks = [];
			segmentStartedAt = 0;

			// 次のセグメントを先に用意してから転写を積む(取りこぼしを最小化)
			if (!isFinal) {
				startSegment();
			}

			enqueueSegment(chunks, duration, recorder.mimeType, isFinal);
		};

		mediaRecorder = recorder;
	};

	// 最終セグメントの転写が終わったところで録音 UI を閉じる
	const finish = async (text: string) => {
		if (finalized) return;
		finalized = true;

		finishing = false;
		loading = false;

		// 親が recording=false でこのコンポーネントを破棄するので、
		// マイクの解放はここで明示的にやる(effect の後片付けには頼らない)
		await stopRecording();
		recording = false;

		onConfirm({ text });
	};

	const startRecording = async () => {
		// llens(#56): http 配備では navigator.mediaDevices が生えない(secure context 必須)。
		// 例外で無反応になる前に理由を出して閉じる
		if (!navigator.mediaDevices?.getUserMedia) {
			toast.error($i18n.t('Microphone requires a secure (HTTPS) connection.'));
			onCancel();
			return;
		}

		loading = true;

		const _stream = await navigator.mediaDevices
			.getUserMedia({
				audio: {
					echoCancellation: true,
					noiseSuppression: true,
					autoGainControl: true
				}
			})
			.catch((error) => {
				toast.error($i18n.t(`Permission denied when accessing microphone: {{error}}`, { error }));
				return null;
			});

		if (!_stream) {
			loading = false;
			onCancel();
			return;
		}

		stream = _stream;
		loading = false;
		startDurationCounter();

		if (usesWebSpeech()) {
			// Web Speech は録音データを使わない(波形表示のためだけに analyser を回す)
			startWebSpeechRecognition();
			analyseAudio(_stream);
			return;
		}

		startSegment();
		analyseAudio(_stream);
	};

	const startWebSpeechRecognition = () => {
		// lib.dom に無い prefixed コンストラクタを型だけ拡張して存在チェック(挙動は従来の in 判定と同じ)
		const speechWindow = window as typeof window & {
			SpeechRecognition?: new () => SpeechRecognitionLike;
			webkitSpeechRecognition?: new () => SpeechRecognitionLike;
		};
		const SpeechRecognitionCtor =
			speechWindow.SpeechRecognition || speechWindow.webkitSpeechRecognition;
		if (!SpeechRecognitionCtor) return;

		// Create a SpeechRecognition object
		speechRecognition = new SpeechRecognitionCtor();

		// Set continuous to true for continuous recognition
		speechRecognition.continuous = true;

		let timeoutId: ReturnType<typeof setTimeout> | undefined;
		// Start recognition
		speechRecognition.start();

		// Event triggered when speech is recognized
		speechRecognition.onresult = async (event) => {
			// Clear the inactivity timeout
			clearTimeout(timeoutId);

			// Handle recognized speech
			const transcript = event.results[Object.keys(event.results).length - 1][0].transcript;

			transcription = `${transcription}${transcript}`;
			dictationText = transcription;

			await tick();
			document.getElementById('chat-input')?.focus();

			// 「読み取り猶予」ぶん無音が続いたら打ち切る(外部 STT の区切りと同じ設定を使う)
			timeoutId = setTimeout(() => {
				speechRecognition?.stop();
			}, silenceTimeout);
		};

		// Event triggered when recognition is ended
		speechRecognition.onend = function () {
			finish(transcription);
		};

		// Event triggered when an error occurs
		speechRecognition.onerror = function (event) {
			toast.error($i18n.t(`Speech recognition error: {{error}}`, { error: event.error }));
			onCancel();

			stopRecording();
		};
	};

	const stopRecording = async () => {
		aborted = true;

		if (mediaRecorder && mediaRecorder.state !== 'inactive') {
			mediaRecorder.stop();
		}
		mediaRecorder = undefined;

		if (speechRecognition) {
			speechRecognition.stop();
		}

		stopDurationCounter();
		audioChunks = [];

		if (stream) {
			const tracks = stream.getTracks();
			tracks.forEach((track) => track.stop());
		}

		stream = null;

		audioContext?.close();
		audioContext = null;
	};

	// 確定ボタン: 最後のセグメントをキューへ流し、drain したら finish() が閉じる
	const confirmRecording = async () => {
		if (finishing) return;

		loading = true;
		finishing = true;
		clearInterval(durationCounter);

		if (usesWebSpeech()) {
			speechRecognition?.stop();
			return;
		}

		if (mediaRecorder && mediaRecorder.state === 'recording') {
			stopping = true;
			mediaRecorder.stop();
		} else if (!stopping) {
			// 録音していない(無音待ち)= 送るものが無い。キューを drain して閉じる
			enqueueSegment([], 0, '', true);
		}
		// stopping なら onstop が finishing を見て最終セグメントとして処理する
	};

	let resizeObserver: ResizeObserver | undefined;

	onMount(() => {
		// listen to width changes
		resizeObserver = new ResizeObserver(() => {
			VISUALIZER_BUFFER_LENGTH = Math.floor(window.innerWidth / 4);
			if (visualizerData.length > VISUALIZER_BUFFER_LENGTH) {
				visualizerData = visualizerData.slice(visualizerData.length - VISUALIZER_BUFFER_LENGTH);
			} else {
				visualizerData = Array(VISUALIZER_BUFFER_LENGTH - visualizerData.length)
					.fill(0)
					.concat(visualizerData);
			}
		});

		resizeObserver.observe(document.body);
	});

	onDestroy(() => {
		// remove resize observer
		resizeObserver?.disconnect();
	});
</script>

<div
	class="{loading
		? ' bg-gray-100/50 dark:bg-gray-850/50'
		: 'bg-indigo-300/10 dark:bg-indigo-500/10 '} {dictationText
		? 'rounded-2xl'
		: 'rounded-full'} {className}"
>
	{#if dictationText}
		<!-- 録音中は入力欄がこのバーに置き換わるので、確定したテキストをここに出す -->
		<div
			bind:this={transcriptElement}
			class="text-xs text-content whitespace-pre-wrap max-h-16 overflow-y-auto px-1.5 pb-1.5"
		>
			{dictationText}
		</div>
	{/if}

	<div class="flex justify-between">
		<div class="flex items-center mr-1">
			<button
				type="button"
				class="p-1.5

            {loading
					? ' bg-gray-200 dark:bg-gray-700/50'
					: 'bg-indigo-400/20 text-indigo-600 dark:text-indigo-300 '}


             rounded-full"
				aria-label={$i18n.t('Cancel')}
				onclick={async () => {
					stopRecording();
					onCancel();
				}}
			>
				<span class="i-lucide-x size-4 block"></span>
			</button>
		</div>

		<div
			class="flex flex-1 self-center items-center justify-between ml-2 mx-1 overflow-hidden h-6"
			dir="rtl"
		>
			<div
				class="flex items-center gap-0.5 h-6 w-full max-w-full overflow-hidden overflow-x-hidden flex-wrap"
			>
				{#each visualizerData.slice().reverse() as rms}
					<div class="flex items-center h-full">
						<div
							class="w-[2px] shrink-0

                    {loading
								? ' bg-gray-500 dark:bg-gray-400   '
								: 'bg-indigo-500 dark:bg-indigo-400  '}

                    inline-block h-full"
							style="height: {Math.min(100, Math.max(14, rms * 100))}%;"
						></div>
					</div>
				{/each}
			</div>
		</div>

		<div class="flex">
			<div class="  mx-1.5 pr-1 flex justify-center items-center">
				<div
					class="text-sm


        {loading ? ' text-content-muted  ' : ' text-indigo-400 '}
       font-medium flex-1 mx-auto text-center"
				>
					{formatSeconds(durationSeconds)}
				</div>
			</div>

			<div class="flex items-center">
				{#if loading}
					<div class=" text-content-muted rounded-full cursor-not-allowed">
						<span class="i-lucide-loader-circle size-6 block animate-spin"></span>
					</div>
				{:else}
					<button
						type="button"
						class="p-1.5 bg-indigo-500 text-white dark:bg-indigo-500 dark:text-blue-950 rounded-full"
						aria-label={$i18n.t('Confirm')}
						onclick={async () => {
							await confirmRecording();
						}}
					>
						<span class="i-lucide-check size-4 block"></span>
					</button>
				{/if}
			</div>
		</div>
	</div>
</div>

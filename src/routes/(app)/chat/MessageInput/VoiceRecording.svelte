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
		onCancel = () => {}
	}: {
		recording?: boolean;
		className?: string;
		onConfirm?: (data: { text: string; filename?: string }) => void;
		onCancel?: () => void;
	} = $props();

	let loading = $state(false);
	let confirmed = false;

	let durationSeconds = $state(0);
	let durationCounter: ReturnType<typeof setInterval> | undefined;

	let transcription = '';

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
	let speechRecognition: SpeechRecognitionLike | undefined;

	let mediaRecorder: MediaRecorder | undefined;
	let audioChunks: Blob[] = [];

	const MIN_DECIBELS = -45;
	let VISUALIZER_BUFFER_LENGTH = 300;

	let visualizerData = $state(Array(VISUALIZER_BUFFER_LENGTH).fill(0));

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

	const analyseAudio = (stream: MediaStream) => {
		const audioContext = new AudioContext();
		const audioStreamSource = audioContext.createMediaStreamSource(stream);

		const analyser = audioContext.createAnalyser();
		analyser.minDecibels = MIN_DECIBELS;
		audioStreamSource.connect(analyser);

		const bufferLength = analyser.frequencyBinCount;

		const domainData = new Uint8Array(bufferLength);
		const timeDomainData = new Uint8Array(analyser.fftSize);

		const detectSound = () => {
			const processFrame = () => {
				if (!recording || loading) return;

				if (recording && !loading) {
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
				}

				window.requestAnimationFrame(processFrame);
			};

			window.requestAnimationFrame(processFrame);
		};

		detectSound();
	};

	const transcribeHandler = async (audioBlob: Blob) => {
		// Create a blob from the audio chunks

		await tick();
		const file = blobToFile(audioBlob, 'recording.wav');

		const res = await transcribeAudio(file).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			console.log(res);
			onConfirm(res);
		}
	};

	const startRecording = async () => {
		loading = true;

		const _stream = await navigator.mediaDevices.getUserMedia({
			audio: {
				echoCancellation: true,
				noiseSuppression: true,
				autoGainControl: true
			}
		});
		stream = _stream;
		mediaRecorder = new MediaRecorder(_stream);
		mediaRecorder.onstart = () => {
			console.log('Recording started');
			loading = false;
			startDurationCounter();

			audioChunks = [];
			analyseAudio(_stream);
		};
		mediaRecorder.ondataavailable = (event) => audioChunks.push(event.data);
		mediaRecorder.onstop = async () => {
			console.log('Recording stopped');
			if (
				$config?.audio?.stt?.engine === 'web' ||
				($settings?.audio?.stt?.engine ?? '') === 'web'
			) {
				audioChunks = [];
			} else {
				if (confirmed) {
					const audioBlob = new Blob(audioChunks, { type: 'audio/wav' });

					await transcribeHandler(audioBlob);

					confirmed = false;
					loading = false;
				}
				audioChunks = [];
				recording = false;
			}
		};
		mediaRecorder.start();
		if ($config?.audio?.stt?.engine === 'web' || ($settings?.audio?.stt?.engine ?? '') === 'web') {
			// lib.dom に無い prefixed コンストラクタを型だけ拡張して存在チェック(挙動は従来の in 判定と同じ)
			const speechWindow = window as typeof window & {
				SpeechRecognition?: new () => SpeechRecognitionLike;
				webkitSpeechRecognition?: new () => SpeechRecognitionLike;
			};
			const SpeechRecognitionCtor =
				speechWindow.SpeechRecognition || speechWindow.webkitSpeechRecognition;
			if (SpeechRecognitionCtor) {
				// Create a SpeechRecognition object
				speechRecognition = new SpeechRecognitionCtor();

				// Set continuous to true for continuous recognition
				speechRecognition.continuous = true;

				// Set the timeout for turning off the recognition after inactivity (in milliseconds)
				const inactivityTimeout = 2000; // 3 seconds

				let timeoutId: ReturnType<typeof setTimeout> | undefined;
				// Start recognition
				speechRecognition.start();

				// Event triggered when speech is recognized
				speechRecognition.onresult = async (event) => {
					// Clear the inactivity timeout
					clearTimeout(timeoutId);

					// Handle recognized speech
					console.log(event);
					const transcript = event.results[Object.keys(event.results).length - 1][0].transcript;

					transcription = `${transcription}${transcript}`;

					await tick();
					document.getElementById('chat-input')?.focus();

					// Restart the inactivity timeout
					timeoutId = setTimeout(() => {
						console.log('Speech recognition turned off due to inactivity.');
						speechRecognition?.stop();
					}, inactivityTimeout);
				};

				// Event triggered when recognition is ended
				speechRecognition.onend = function () {
					// Restart recognition after it ends
					console.log('recognition ended');

					confirmRecording();
					onConfirm({ text: transcription });
					confirmed = false;
					loading = false;
				};

				// Event triggered when an error occurs
				speechRecognition.onerror = function (event) {
					console.log(event);
					toast.error($i18n.t(`Speech recognition error: {{error}}`, { error: event.error }));
					onCancel();

					stopRecording();
				};
			}
		}
	};

	const stopRecording = async () => {
		if (recording && mediaRecorder) {
			await mediaRecorder.stop();
		}

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
	};

	const confirmRecording = async () => {
		loading = true;
		confirmed = true;

		if (recording && mediaRecorder) {
			await mediaRecorder.stop();
		}
		clearInterval(durationCounter);

		if (stream) {
			const tracks = stream.getTracks();
			tracks.forEach((track) => track.stop());
		}

		stream = null;
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
		: 'bg-indigo-300/10 dark:bg-indigo-500/10 '} rounded-full flex justify-between {className}"
>
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

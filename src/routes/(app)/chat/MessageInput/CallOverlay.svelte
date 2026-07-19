<script lang="ts">
	import { config, models, settings, showCallOverlay, TTSWorker } from '$lib/stores';
	import { DEFAULT_MODEL_IMAGE_URL } from '$lib/constants';
	import { onMount, tick, getContext, onDestroy } from 'svelte';

	import { blobToFile } from '$lib/utils';
	import { synthesizeOpenAISpeech, transcribeAudio } from '$lib/apis/audio';

	import { toast } from 'svelte-sonner';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import VideoInputMenu from './CallOverlay/VideoInputMenu.svelte';

	const i18n = getContext('i18n');

	let {
		eventTarget,
		submitPrompt,
		stopResponse,
		files = $bindable(),
		modelId,
		onClose = () => {}
	}: {
		eventTarget: EventTarget;
		submitPrompt: Function;
		stopResponse: Function;
		files?: any[];
		modelId: string | null;
		onClose?: () => void;
	} = $props();

	let wakeLock: WakeLockSentinel | null = null;

	let model = $state<any>(null);

	let loading = $state(false);
	let confirmed = false;
	let assistantSpeaking = $state(false);

	let camera = $state(false);
	let cameraStream = $state<MediaStream | null>(null);

	let chatStreaming = false;
	let rmsLevel = $state(0);
	let hasStartedSpeaking = false;
	// false は「録音停止済み」マーカー(stopRecordingCallback が代入する従来挙動を型に反映)
	let mediaRecorder: MediaRecorder | false | undefined;
	let audioStream: MediaStream | null = null;
	let audioChunks: Blob[] = [];

	let videoInputDevices = $state<any[]>([]);
	let selectedVideoInputDeviceId: string | null = null;

	const getVideoInputDevices = async () => {
		const devices = await navigator.mediaDevices.enumerateDevices();
		videoInputDevices = devices.filter((device) => device.kind === 'videoinput');

		if ('getDisplayMedia' in navigator.mediaDevices) {
			videoInputDevices = [
				...videoInputDevices,
				{
					deviceId: 'screen',
					label: 'Screen Share'
				}
			];
		}

		console.log(videoInputDevices);
		if (selectedVideoInputDeviceId === null && videoInputDevices.length > 0) {
			selectedVideoInputDeviceId = videoInputDevices[0].deviceId;
		}
	};

	const startCamera = async () => {
		await getVideoInputDevices();

		if (cameraStream === null) {
			camera = true;
			await tick();
			try {
				await startVideoStream();
			} catch (err) {
				console.error('Error accessing webcam: ', err);
			}
		}
	};

	const startVideoStream = async () => {
		const video = document.getElementById('camera-feed') as HTMLVideoElement | null;
		if (video) {
			if (selectedVideoInputDeviceId === 'screen') {
				cameraStream = await navigator.mediaDevices.getDisplayMedia({
					// 旧仕様の cursor 制約(lib.dom 未収載)— 挙動維持のため型だけ拡張
					video: {
						cursor: 'always'
					} as MediaTrackConstraints,
					audio: false
				});
			} else {
				cameraStream = await navigator.mediaDevices.getUserMedia({
					video: {
						deviceId: selectedVideoInputDeviceId ? { exact: selectedVideoInputDeviceId } : undefined
					}
				});
			}

			if (cameraStream) {
				await getVideoInputDevices();
				video.srcObject = cameraStream;
				await video.play();
			}
		}
	};

	const stopVideoStream = async () => {
		if (cameraStream) {
			const tracks = cameraStream.getTracks();
			tracks.forEach((track) => track.stop());
		}

		cameraStream = null;
	};

	const takeScreenshot = () => {
		const video = document.getElementById('camera-feed') as HTMLVideoElement | null;
		const canvas = document.getElementById('camera-canvas') as HTMLCanvasElement | null;

		if (!canvas || !video) {
			return;
		}

		const context = canvas.getContext('2d');
		if (!context) {
			return;
		}

		// Make the canvas match the video dimensions
		canvas.width = video.videoWidth;
		canvas.height = video.videoHeight;

		// Draw the image from the video onto the canvas
		context.drawImage(video, 0, 0, video.videoWidth, video.videoHeight);

		// Convert the canvas to a data base64 URL and console log it
		const dataURL = canvas.toDataURL('image/png');
		console.log(dataURL);

		return dataURL;
	};

	const stopCamera = async () => {
		await stopVideoStream();
		camera = false;
	};

	const MIN_DECIBELS = -55;

	const transcribeHandler = async (audioBlob: Blob) => {
		// Create a blob from the audio chunks

		await tick();
		const file = blobToFile(audioBlob, 'recording.wav');

		const res = await transcribeAudio(file).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			console.log(res.text);

			if (res.text !== '') {
				const _responses = await submitPrompt(res.text, { _raw: true });
				console.log(_responses);
			}
		}
	};

	const stopRecordingCallback = async (_continue = true) => {
		if ($showCallOverlay) {
			console.log('%c%s', 'color: red; font-size: 20px;', '🚨 stopRecordingCallback 🚨');

			// deep copy the audioChunks array
			const _audioChunks = audioChunks.slice(0);

			audioChunks = [];
			mediaRecorder = false;

			if (_continue) {
				startRecording();
			}

			if (confirmed) {
				loading = true;

				if (cameraStream) {
					const imageUrl = takeScreenshot();

					files = [
						{
							type: 'image',
							url: imageUrl
						}
					];
				}

				const audioBlob = new Blob(_audioChunks, { type: 'audio/wav' });
				await transcribeHandler(audioBlob);

				confirmed = false;
				loading = false;
			}
		} else {
			audioChunks = [];
			mediaRecorder = false;

			if (audioStream) {
				const tracks = audioStream.getTracks();
				tracks.forEach((track) => track.stop());
			}
			audioStream = null;
		}
	};

	const startRecording = async () => {
		if ($showCallOverlay) {
			if (!audioStream) {
				audioStream = await navigator.mediaDevices.getUserMedia({
					audio: {
						echoCancellation: true,
						noiseSuppression: true,
						autoGainControl: true
					}
				});
			}
			mediaRecorder = new MediaRecorder(audioStream);

			mediaRecorder.onstart = () => {
				console.log('Recording started');
				audioChunks = [];
			};

			mediaRecorder.ondataavailable = (event) => {
				if (hasStartedSpeaking) {
					audioChunks.push(event.data);
				}
			};

			mediaRecorder.onstop = (e) => {
				console.log('Recording stopped', audioStream, e);
				stopRecordingCallback();
			};

			analyseAudio(audioStream);
		}
	};

	const stopAudioStream = async () => {
		try {
			if (mediaRecorder) {
				mediaRecorder.stop();
			}
		} catch (error) {
			console.log('Error stopping audio stream:', error);
		}

		if (!audioStream) return;

		audioStream.getAudioTracks().forEach(function (track) {
			track.stop();
		});

		audioStream = null;
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

	const analyseAudio = (stream: MediaStream) => {
		const audioContext = new AudioContext();
		const audioStreamSource = audioContext.createMediaStreamSource(stream);

		const analyser = audioContext.createAnalyser();
		analyser.minDecibels = MIN_DECIBELS;
		audioStreamSource.connect(analyser);

		const bufferLength = analyser.frequencyBinCount;

		const domainData = new Uint8Array(bufferLength);
		const timeDomainData = new Uint8Array(analyser.fftSize);

		let lastSoundTime = Date.now();
		hasStartedSpeaking = false;

		console.log('🔊 Sound detection started', lastSoundTime, hasStartedSpeaking);

		const detectSound = () => {
			const processFrame = () => {
				if (!mediaRecorder || !$showCallOverlay) {
					return;
				}

				if (assistantSpeaking && !($settings?.voiceInterruption ?? false)) {
					// Mute the audio if the assistant is speaking
					analyser.maxDecibels = 0;
					analyser.minDecibels = -1;
				} else {
					analyser.minDecibels = MIN_DECIBELS;
					analyser.maxDecibels = -30;
				}

				analyser.getByteTimeDomainData(timeDomainData);
				analyser.getByteFrequencyData(domainData);

				// Calculate RMS level from time domain data
				rmsLevel = calculateRMS(timeDomainData);

				// Check if initial speech/noise has started
				const hasSound = domainData.some((value) => value > 0);
				if (hasSound) {
					// BIG RED TEXT
					console.log('%c%s', 'color: red; font-size: 20px;', '🔊 Sound detected');
					if (mediaRecorder && mediaRecorder.state !== 'recording') {
						mediaRecorder.start();
					}

					if (!hasStartedSpeaking) {
						hasStartedSpeaking = true;
						stopAllAudio();
					}

					lastSoundTime = Date.now();
				}

				// Start silence detection only after initial speech/noise has been detected
				if (hasStartedSpeaking) {
					if (Date.now() - lastSoundTime > 2000) {
						confirmed = true;

						if (mediaRecorder) {
							console.log('%c%s', 'color: red; font-size: 20px;', '🔇 Silence detected');
							mediaRecorder.stop();
							return;
						}
					}
				}

				window.requestAnimationFrame(processFrame);
			};

			window.requestAnimationFrame(processFrame);
		};

		detectSound();
	};

	let finishedMessages: Record<string, boolean> = {};
	let currentMessageId: string | null = null;
	let currentUtterance: SpeechSynthesisUtterance | null = null;

	const speakSpeechSynthesisHandler = (content: string) => {
		if ($showCallOverlay) {
			return new Promise((resolve) => {
				let voices: SpeechSynthesisVoice[] = [];
				const getVoicesLoop = setInterval(async () => {
					voices = await speechSynthesis.getVoices();
					if (voices.length > 0) {
						clearInterval(getVoicesLoop);

						const voice =
							voices
								?.filter(
									(v) => v.voiceURI === ($settings?.audio?.tts?.voice ?? $config?.audio?.tts?.voice)
								)
								?.at(0) ?? undefined;

						currentUtterance = new SpeechSynthesisUtterance(content);
						currentUtterance.rate = $settings.audio?.tts?.playbackRate ?? 1;

						if (voice) {
							currentUtterance.voice = voice;
						}

						speechSynthesis.speak(currentUtterance);
						currentUtterance.onend = async (e) => {
							await new Promise((r) => setTimeout(r, 200));
							resolve(e);
						};
					}
				}, 100);
			});
		} else {
			return Promise.resolve();
		}
	};

	const playAudio = (audio: HTMLAudioElement) => {
		if ($showCallOverlay) {
			return new Promise((resolve) => {
				const audioElement = document.getElementById('audioElement') as HTMLAudioElement;

				if (audioElement) {
					audioElement.src = audio.src;
					audioElement.muted = true;
					audioElement.playbackRate = $settings.audio?.tts?.playbackRate ?? 1;

					audioElement
						.play()
						.then(() => {
							audioElement.muted = false;
						})
						.catch((error) => {
							console.error(error);
						});

					audioElement.onended = async (e) => {
						await new Promise((r) => setTimeout(r, 100));
						resolve(e);
					};
				}
			});
		} else {
			return Promise.resolve();
		}
	};

	const stopAllAudio = async () => {
		assistantSpeaking = false;

		if (chatStreaming) {
			stopResponse();
		}

		if (currentUtterance) {
			speechSynthesis.cancel();
			currentUtterance = null;
		}

		const audioElement = document.getElementById('audioElement') as HTMLAudioElement | null;
		if (audioElement) {
			audioElement.muted = true;
			audioElement.pause();
			audioElement.currentTime = 0;
		}
	};

	let audioAbortController = new AbortController();

	// Audio cache map where key is the content and value is the Audio object.
	const audioCache = new Map();

	const fetchAudio = async (content: string) => {
		if (!audioCache.has(content)) {
			try {
				if ($settings.audio?.tts?.engine === 'browser-kokoro') {
					const blob = await $TTSWorker
						?.generate({
							text: content,
							voice: $settings?.audio?.tts?.voice ?? $config?.audio?.tts?.voice ?? ''
						})
						.catch((error) => {
							console.error(error);
							toast.error(`${error}`);
						});

					if (blob) {
						audioCache.set(content, new Audio(blob));
					}
				} else if (($config?.audio?.tts?.engine ?? '') !== '') {
					const res = await synthesizeOpenAISpeech(
						$settings?.audio?.tts?.defaultVoice === $config?.audio?.tts?.voice
							? ($settings?.audio?.tts?.voice ?? $config?.audio?.tts?.voice)
							: $config?.audio?.tts?.voice,
						content
					).catch((error) => {
						console.error(error);
						return null;
					});

					if (res) {
						const blob = await res.blob();
						const blobUrl = URL.createObjectURL(blob);
						audioCache.set(content, new Audio(blobUrl));
					}
				} else {
					audioCache.set(content, true);
				}
			} catch (error) {
				console.error('Error synthesizing speech:', error);
			}
		}

		return audioCache.get(content);
	};

	let messages: Record<string, string[]> = {};

	const monitorAndPlayAudio = async (id: string, signal: AbortSignal) => {
		while (!signal.aborted) {
			if (messages[id] && messages[id].length > 0) {
				// Retrieve the next content string from the queue
				// (直前で length > 0 を確認済みなので shift は必ず値を返す)
				const content = messages[id].shift()!; // Dequeues the content for playing

				if (audioCache.has(content)) {
					// If content is available in the cache, play it

					if (($config?.audio?.tts?.engine ?? '') !== '') {
						try {
							console.log(
								'%c%s',
								'color: red; font-size: 20px;',
								`Playing audio for content: ${content}`
							);

							const audio = audioCache.get(content);
							await playAudio(audio); // Here ensure that playAudio is indeed correct method to execute
							console.log(`Played audio for content: ${content}`);
							await new Promise((resolve) => setTimeout(resolve, 200)); // Wait before retrying to reduce tight loop
						} catch (error) {
							console.error('Error playing audio:', error);
						}
					} else {
						await speakSpeechSynthesisHandler(content);
					}
				} else {
					// If not available in the cache, push it back to the queue and delay
					messages[id].unshift(content); // Re-queue the content at the start
					console.log(`Audio for "${content}" not yet available in the cache, re-queued...`);
					await new Promise((resolve) => setTimeout(resolve, 200)); // Wait before retrying to reduce tight loop
				}
			} else if (finishedMessages[id] && messages[id] && messages[id].length === 0) {
				// If the message is finished and there are no more messages to process, break the loop
				assistantSpeaking = false;
				break;
			} else {
				// No messages to process, sleep for a bit
				await new Promise((resolve) => setTimeout(resolve, 200));
			}
		}
		console.log(`Audio monitoring and playing stopped for message ID ${id}`);
	};

	// eventTarget には Chat.svelte が CustomEvent('chat:start'|'chat'|'chat:finish') を
	// dispatch する(detail: { id, content? })。addEventListener の型に合わせ Event で受けて絞る。
	const chatStartHandler = async (e: Event) => {
		const { id } = (e as CustomEvent<{ id: string }>).detail;

		chatStreaming = true;

		if (currentMessageId !== id) {
			console.log(`Received chat start event for message ID ${id}`);

			currentMessageId = id;
			if (audioAbortController) {
				audioAbortController.abort();
			}
			audioAbortController = new AbortController();

			assistantSpeaking = true;
			// Start monitoring and playing audio for the message ID
			monitorAndPlayAudio(id, audioAbortController.signal);
		}
	};

	const chatEventHandler = async (e: Event) => {
		const { id, content } = (e as CustomEvent<{ id: string; content: string }>).detail;
		// "id" here is message id
		// if "id" is not the same as "currentMessageId" then do not process
		// "content" here is a sentence from the assistant,
		// there will be many sentences for the same "id"

		if (currentMessageId === id) {
			console.log(`Received chat event for message ID ${id}: ${content}`);

			try {
				if (messages[id] === undefined) {
					messages[id] = [content];
				} else {
					messages[id].push(content);
				}

				console.log(content);

				fetchAudio(content);
			} catch (error) {
				console.error('Failed to fetch or play audio:', error);
			}
		}
	};

	const chatFinishHandler = async (e: Event) => {
		const { id } = (e as CustomEvent<{ id: string; content: string }>).detail;
		// "content" here is the entire message from the assistant
		finishedMessages[id] = true;

		chatStreaming = false;
	};

	onMount(async () => {
		const setWakeLock = async () => {
			try {
				wakeLock = await navigator.wakeLock.request('screen');
			} catch (err) {
				// The Wake Lock request has failed - usually system related, such as battery.
				console.log(err);
			}

			if (wakeLock) {
				// Add a listener to release the wake lock when the page is unloaded
				wakeLock.addEventListener('release', () => {
					// the wake lock has been released
					console.log('Wake Lock released');
				});
			}
		};

		if ('wakeLock' in navigator) {
			await setWakeLock();

			document.addEventListener('visibilitychange', async () => {
				// Re-request the wake lock if the document becomes visible
				if (wakeLock !== null && document.visibilityState === 'visible') {
					await setWakeLock();
				}
			});
		}

		model = $models.find((m) => m.id === modelId);

		startRecording();

		eventTarget.addEventListener('chat:start', chatStartHandler);
		eventTarget.addEventListener('chat', chatEventHandler);
		eventTarget.addEventListener('chat:finish', chatFinishHandler);

		// 後始末は onDestroy(下)が担う。async onMount の返り値クリーンアップは
		// Svelte が無視する(同期コールバック限定)ため、ここでは返さない。
	});

	onDestroy(async () => {
		await stopAllAudio();
		await stopRecordingCallback(false);
		await stopCamera();

		await stopAudioStream();
		eventTarget.removeEventListener('chat:start', chatStartHandler);
		eventTarget.removeEventListener('chat', chatEventHandler);
		eventTarget.removeEventListener('chat:finish', chatFinishHandler);
		audioAbortController.abort();

		await tick();

		await stopAllAudio();
	});
</script>

{#if $showCallOverlay}
	<div class="max-w-lg w-full h-full max-h-[100dvh] flex flex-col justify-between p-3 md:p-6">
		{#if camera}
			<button
				type="button"
				class="flex justify-center items-center w-full h-20 min-h-20"
				onclick={() => {
					if (assistantSpeaking) {
						stopAllAudio();
					}
				}}
			>
				{#if loading || assistantSpeaking}
					<span class="i-lucide-ellipsis size-12 block text-content animate-pulse"></span>
				{:else}
					<div
						class=" {rmsLevel * 100 > 4
							? ' size-[4.5rem]'
							: rmsLevel * 100 > 2
								? ' size-16'
								: rmsLevel * 100 > 1
									? 'size-14'
									: 'size-12'}  transition-all rounded-full {(model?.info?.meta
							?.profile_image_url ?? DEFAULT_MODEL_IMAGE_URL) !== DEFAULT_MODEL_IMAGE_URL
							? ' bg-cover bg-center bg-no-repeat'
							: 'bg-black dark:bg-white'}  bg-black dark:bg-white"
						style={(model?.info?.meta?.profile_image_url ?? DEFAULT_MODEL_IMAGE_URL) !==
						DEFAULT_MODEL_IMAGE_URL
							? `background-image: url('${model?.info?.meta?.profile_image_url}');`
							: ''}
					></div>
				{/if}
				<!-- navbar -->
			</button>
		{/if}

		<div class="flex justify-center items-center flex-1 h-full w-full max-h-full">
			{#if !camera}
				<button
					type="button"
					onclick={() => {
						if (assistantSpeaking) {
							stopAllAudio();
						}
					}}
				>
					{#if loading || assistantSpeaking}
						<span class="i-lucide-ellipsis size-44 block text-content animate-pulse"></span>
					{:else}
						<div
							class=" {rmsLevel * 100 > 4
								? ' size-52'
								: rmsLevel * 100 > 2
									? 'size-48'
									: rmsLevel * 100 > 1
										? 'size-44'
										: 'size-40'}  transition-all rounded-full {(model?.info?.meta
								?.profile_image_url ?? DEFAULT_MODEL_IMAGE_URL) !== DEFAULT_MODEL_IMAGE_URL
								? ' bg-cover bg-center bg-no-repeat'
								: 'bg-black dark:bg-white'} "
							style={(model?.info?.meta?.profile_image_url ?? DEFAULT_MODEL_IMAGE_URL) !==
							DEFAULT_MODEL_IMAGE_URL
								? `background-image: url('${model?.info?.meta?.profile_image_url}');`
								: ''}
						></div>
					{/if}
				</button>
			{:else}
				<div class="relative flex video-container w-full max-h-full pt-2 pb-4 md:py-6 px-2 h-full">
					<video
						id="camera-feed"
						autoplay
						class="rounded-2xl h-full min-w-full object-cover object-center"
						playsinline
					></video>

					<canvas id="camera-canvas" style="display:none;"></canvas>

					<div class=" absolute top-4 md:top-8 left-4">
						<button
							type="button"
							class="p-1.5 text-white cursor-pointer backdrop-blur-xl bg-black/10 rounded-full"
							aria-label={$i18n.t('Stop camera')}
							onclick={() => {
								stopCamera();
							}}
						>
							<span class="i-lucide-x size-6 block"></span>
						</button>
					</div>
				</div>
			{/if}
		</div>

		<div class="flex justify-between items-center pb-2 w-full">
			<div>
				{#if camera}
					<VideoInputMenu
						devices={videoInputDevices}
						onChange={async (deviceId) => {
							console.log(deviceId);
							selectedVideoInputDeviceId = deviceId;
							await stopVideoStream();
							await startVideoStream();
						}}
					>
						<button
							class=" p-3 rounded-full bg-gray-50 dark:bg-gray-900"
							type="button"
							aria-label={$i18n.t('Switch Camera')}
						>
							<span class="i-lucide-switch-camera size-5 block"></span>
						</button>
					</VideoInputMenu>
				{:else}
					<Tooltip content={$i18n.t('Camera')}>
						<button
							class=" p-3 rounded-full bg-gray-50 dark:bg-gray-900"
							type="button"
							aria-label={$i18n.t('Camera')}
							onclick={async () => {
								await navigator.mediaDevices.getUserMedia({ video: true });
								startCamera();
							}}
						>
							<span class="i-lucide-camera size-5 block"></span>
						</button>
					</Tooltip>
				{/if}
			</div>

			<div>
				<button
					type="button"
					onclick={() => {
						if (assistantSpeaking) {
							stopAllAudio();
						}
					}}
				>
					<div class=" line-clamp-1 text-sm font-medium">
						{#if loading}
							{$i18n.t('Thinking...')}
						{:else if assistantSpeaking}
							{$i18n.t('Tap to interrupt')}
						{:else}
							{$i18n.t('Listening...')}
						{/if}
					</div>
				</button>
			</div>

			<div>
				<button
					class=" p-3 rounded-full bg-gray-50 dark:bg-gray-900"
					aria-label={$i18n.t('Close')}
					onclick={async () => {
						await stopAudioStream();
						await stopVideoStream();

						console.log(audioStream);
						console.log(cameraStream);

						showCallOverlay.set(false);
						onClose();
					}}
					type="button"
				>
					<span class="i-lucide-x size-5 block"></span>
				</button>
			</div>
		</div>
	</div>
{/if}

<script lang="ts">
	// Call(音声通話オーバーレイ)開始ボタン(#70 C トラック: MessageInput.svelte 分割)。
	// マイク権限の確認 → 必要なら Kokoro TTS worker 初期化 → オーバーレイ表示。
	// 表示条件(callEnabled・STT/TTS enable・入力が空)は親の {#if} が持つ
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { settings, config, showCallOverlay, showControls, TTSWorker } from '$lib/stores';
	import { KokoroWorker } from '$lib/workers/KokoroWorker';

	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n = getContext('i18n');

	let { selectedModels }: { selectedModels: string[] } = $props();

	// llens(#56): マイクは secure context(https / localhost)でしか使えない。
	// http 配備では navigator.mediaDevices ごと生えず、押しても無反応になるので押せなくする。
	// ※ 配備レベルの ENABLE_AUDIO_STT/TTS(ボタンごと非表示)とは別物 —
	//    あちらは「この配備に機能があるか」、こちらは「このブラウザ経路で使えるか」
	const micAvailable = !!navigator?.mediaDevices?.getUserMedia;
	const micUnavailableMessage = 'Microphone requires a secure (HTTPS) connection.';
</script>

<div class=" flex items-center">
	<Tooltip content={micAvailable ? $i18n.t('Call') : $i18n.t(micUnavailableMessage)}>
		<button
			class=" {micAvailable
				? 'bg-primary hover:bg-primary-hover text-primary-content'
				: 'bg-secondary text-content-subtle cursor-not-allowed'} rounded-full p-1.5 self-center"
			type="button"
			aria-disabled={!micAvailable}
			onclick={async () => {
				if (!micAvailable) {
					toast.error($i18n.t(micUnavailableMessage));

					return;
				}

				if (selectedModels.length > 1) {
					toast.error($i18n.t('Select only one model to call'));

					return;
				}

				if ($config?.audio?.stt?.engine === 'web') {
					toast.error($i18n.t('Call feature is not supported when using Web STT engine'));

					return;
				}
				// check if user has access to getUserMedia
				try {
					let stream: MediaStream | null = await navigator.mediaDevices.getUserMedia({
						audio: true
					});
					// If the user grants the permission, proceed to show the call overlay

					if (stream) {
						const tracks = stream.getTracks();
						tracks.forEach((track) => track.stop());
					}

					stream = null;

					if ($settings.audio?.tts?.engine === 'browser-kokoro') {
						// If the user has not initialized the TTS worker, initialize it
						if (!$TTSWorker) {
							const worker = new KokoroWorker($settings.audio?.tts?.engineConfig?.dtype ?? 'fp32');
							TTSWorker.set(worker);

							await worker.init();
						}
					}

					showCallOverlay.set(true);
					showControls.set(true);
				} catch (err) {
					// If the user denies the permission or an error occurs, show an error message
					toast.error($i18n.t('Permission denied when accessing media devices'));
				}
			}}
			aria-label={$i18n.t('Call')}
		>
			<span class="i-lucide-headphones size-5 block"></span>
		</button>
	</Tooltip>
</div>

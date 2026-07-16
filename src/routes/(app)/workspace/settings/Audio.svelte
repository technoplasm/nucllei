<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { onMount, getContext } from 'svelte';

	import { getBackendConfig } from '$lib/apis';
	import {
		getAudioConfig,
		updateAudioConfig,
		getModels as _getModels,
		getVoices as _getVoices
	} from '$lib/apis/audio';
	import { config } from '$lib/stores';

	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	import { TTS_RESPONSE_SPLIT } from '$lib/types';

	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	const i18n = getContext<Writable<i18nType>>('i18n');

	let { saveHandler }: { saveHandler: () => void } = $props();

	// Audio
	// llens(#53): 配備レベルの機能フラグ(false でチャットのアイコン非表示 + API 403)
	let TTS_ENABLE = $state(true);
	let STT_ENABLE = $state(true);

	let TTS_OPENAI_API_BASE_URL = $state('');
	let TTS_OPENAI_API_KEY = $state('');
	let TTS_API_KEY = $state('');
	let TTS_ENGINE = $state('');
	let TTS_MODEL = $state('');
	let TTS_VOICE = $state('');
	let TTS_SPLIT_ON = $state<TTS_RESPONSE_SPLIT>(TTS_RESPONSE_SPLIT.PUNCTUATION);
	let TTS_AZURE_SPEECH_REGION = $state('');
	let TTS_AZURE_SPEECH_OUTPUT_FORMAT = $state('');

	let STT_OPENAI_API_BASE_URL = $state('');
	let STT_OPENAI_API_KEY = $state('');
	let STT_ENGINE = $state('');
	let STT_MODEL = $state('');
	let STT_WHISPER_MODEL = $state('');
	let STT_AZURE_API_KEY = $state('');
	let STT_AZURE_REGION = $state('');
	let STT_AZURE_LOCALES = $state('');
	let STT_DEEPGRAM_API_KEY = $state('');

	let STT_WHISPER_MODEL_LOADING = $state(false);

	let voices = $state<any[]>([]);
	let models = $state<any[]>([]);

	const getModels = async () => {
		if (TTS_ENGINE === '') {
			models = [];
		} else {
			const res = await _getModels().catch((e) => {
				toast.error(`${e}`);
			});

			if (res) {
				models = res.models;
			}
		}
	};

	const getVoices = async () => {
		if (TTS_ENGINE === '') {
			const getVoicesLoop = setInterval(() => {
				voices = speechSynthesis.getVoices();

				// do your loop
				if (voices.length > 0) {
					clearInterval(getVoicesLoop);
					voices.sort((a, b) => a.name.localeCompare(b.name, $i18n.resolvedLanguage));
				}
			}, 100);
		} else {
			const res = await _getVoices().catch((e) => {
				toast.error(`${e}`);
			});

			if (res) {
				voices = res.voices;
				voices.sort((a, b) => a.name.localeCompare(b.name, $i18n.resolvedLanguage));
			}
		}
	};

	const updateConfigHandler = async () => {
		const res = await updateAudioConfig({
			tts: {
				ENABLE: TTS_ENABLE,
				OPENAI_API_BASE_URL: TTS_OPENAI_API_BASE_URL,
				OPENAI_API_KEY: TTS_OPENAI_API_KEY,
				API_KEY: TTS_API_KEY,
				ENGINE: TTS_ENGINE,
				MODEL: TTS_MODEL,
				VOICE: TTS_VOICE,
				SPLIT_ON: TTS_SPLIT_ON,
				AZURE_SPEECH_REGION: TTS_AZURE_SPEECH_REGION,
				AZURE_SPEECH_OUTPUT_FORMAT: TTS_AZURE_SPEECH_OUTPUT_FORMAT
			},
			stt: {
				ENABLE: STT_ENABLE,
				OPENAI_API_BASE_URL: STT_OPENAI_API_BASE_URL,
				OPENAI_API_KEY: STT_OPENAI_API_KEY,
				ENGINE: STT_ENGINE,
				MODEL: STT_MODEL,
				WHISPER_MODEL: STT_WHISPER_MODEL,
				DEEPGRAM_API_KEY: STT_DEEPGRAM_API_KEY,
				AZURE_API_KEY: STT_AZURE_API_KEY,
				AZURE_REGION: STT_AZURE_REGION,
				AZURE_LOCALES: STT_AZURE_LOCALES
			}
		});

		if (res) {
			saveHandler();
			config.set(await getBackendConfig());
		}
	};

	const sttModelUpdateHandler = async () => {
		STT_WHISPER_MODEL_LOADING = true;
		await updateConfigHandler();
		STT_WHISPER_MODEL_LOADING = false;
	};

	onMount(async () => {
		const res = await getAudioConfig();

		if (res) {
			TTS_ENABLE = res.tts.ENABLE;
			STT_ENABLE = res.stt.ENABLE;

			TTS_OPENAI_API_BASE_URL = res.tts.OPENAI_API_BASE_URL;
			TTS_OPENAI_API_KEY = res.tts.OPENAI_API_KEY;
			TTS_API_KEY = res.tts.API_KEY;

			TTS_ENGINE = res.tts.ENGINE;
			TTS_MODEL = res.tts.MODEL;
			TTS_VOICE = res.tts.VOICE;

			TTS_SPLIT_ON = res.tts.SPLIT_ON || TTS_RESPONSE_SPLIT.PUNCTUATION;

			TTS_AZURE_SPEECH_OUTPUT_FORMAT = res.tts.AZURE_SPEECH_OUTPUT_FORMAT;
			TTS_AZURE_SPEECH_REGION = res.tts.AZURE_SPEECH_REGION;

			STT_OPENAI_API_BASE_URL = res.stt.OPENAI_API_BASE_URL;
			STT_OPENAI_API_KEY = res.stt.OPENAI_API_KEY;

			STT_ENGINE = res.stt.ENGINE;
			STT_MODEL = res.stt.MODEL;
			STT_WHISPER_MODEL = res.stt.WHISPER_MODEL;
			STT_AZURE_API_KEY = res.stt.AZURE_API_KEY;
			STT_AZURE_REGION = res.stt.AZURE_REGION;
			STT_AZURE_LOCALES = res.stt.AZURE_LOCALES;
			STT_DEEPGRAM_API_KEY = res.stt.DEEPGRAM_API_KEY;
		}

		await getVoices();
		await getModels();
	});
</script>

<form
	class="flex flex-col h-full justify-between space-y-3 text-sm"
	onsubmit={async (e) => {
		e.preventDefault();
		await updateConfigHandler();
	}}
>
	<div class=" space-y-3 overflow-y-scroll scrollbar-hidden h-full">
		<div class="flex flex-col gap-3">
			<div>
				<div class=" mb-1 text-sm font-medium">{$i18n.t('STT Settings')}</div>

				<div class=" py-0.5 flex w-full justify-between">
					<div class=" self-center text-xs font-medium">{$i18n.t('Enable Speech-to-Text')}</div>
					<div class="flex items-center relative">
						<Switch bind:state={STT_ENABLE} />
					</div>
				</div>

				<div class=" py-0.5 flex w-full justify-between">
					<div class=" self-center text-xs font-medium">{$i18n.t('Speech-to-Text Engine')}</div>
					<div class="flex items-center relative">
						<select
							class="bg-surface-raised hover:bg-surface-hover cursor-pointer w-fit px-2 py-0.5 pr-6 rounded-xs text-xs outline-hidden text-right"
							bind:value={STT_ENGINE}
							placeholder="Select an engine"
						>
							<option value="">{$i18n.t('Whisper (Local)')}</option>
							<option value="openai">OpenAI</option>
							<option value="web">{$i18n.t('Web API')}</option>
							<option value="deepgram">Deepgram</option>
							<option value="azure">Azure AI Speech</option>
						</select>
					</div>
				</div>

				{#if STT_ENGINE === 'openai'}
					<div>
						<div class="mt-1 flex gap-2 mb-1">
							<input
								class="flex-1 w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
								placeholder={$i18n.t('API Base URL')}
								bind:value={STT_OPENAI_API_BASE_URL}
								required
							/>

							<SensitiveInput
								outerClassName="flex flex-1 px-2 bg-surface-raised rounded-xs"
								inputClassName="w-full text-sm py-1 placeholder:text-content-subtle bg-transparent outline-hidden"
								placeholder={$i18n.t('API Key')}
								bind:value={STT_OPENAI_API_KEY}
							/>
						</div>
					</div>

					<hr class="border-line my-2" />

					<div>
						<div class=" mb-1.5 text-sm font-medium">{$i18n.t('STT Model')}</div>
						<div class="flex w-full">
							<div class="flex-1">
								<input
									list="model-list"
									class="w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
									bind:value={STT_MODEL}
									placeholder="Select a model"
								/>

								<datalist id="model-list">
									<option value="whisper-1"></option>
								</datalist>
							</div>
						</div>
					</div>
				{:else if STT_ENGINE === 'deepgram'}
					<div>
						<div class="mt-1 flex gap-2 mb-1">
							<SensitiveInput
								outerClassName="flex flex-1 px-2 bg-surface-raised rounded-xs"
								inputClassName="w-full text-sm py-1 placeholder:text-content-subtle bg-transparent outline-hidden"
								placeholder={$i18n.t('API Key')}
								bind:value={STT_DEEPGRAM_API_KEY}
							/>
						</div>
					</div>

					<hr class="border-line my-2" />

					<div>
						<div class=" mb-1.5 text-sm font-medium">{$i18n.t('STT Model')}</div>
						<div class="flex w-full">
							<div class="flex-1">
								<input
									class="w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
									bind:value={STT_MODEL}
									placeholder="Select a model (optional)"
								/>
							</div>
						</div>
						<div class="mt-2 mb-1 text-xs text-content-subtle">
							{$i18n.t('Leave model field empty to use the default model.')}
							<a
								class=" hover:underline text-content"
								href="https://developers.deepgram.com/docs/models"
								target="_blank"
							>
								{$i18n.t('Click here to see available models.')}
							</a>
						</div>
					</div>
				{:else if STT_ENGINE === 'azure'}
					<div>
						<div class="mt-1 flex gap-2 mb-1">
							<SensitiveInput
								outerClassName="flex flex-1 px-2 bg-surface-raised rounded-xs"
								inputClassName="w-full text-sm py-1 placeholder:text-content-subtle bg-transparent outline-hidden"
								placeholder={$i18n.t('API Key')}
								bind:value={STT_AZURE_API_KEY}
								required
							/>
							<input
								class="flex-1 w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
								placeholder={$i18n.t('Azure Region')}
								bind:value={STT_AZURE_REGION}
								required
							/>
						</div>

						<hr class="border-line my-2" />

						<div>
							<div class=" mb-1.5 text-sm font-medium">{$i18n.t('Language Locales')}</div>
							<div class="flex w-full">
								<div class="flex-1">
									<input
										class="w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
										bind:value={STT_AZURE_LOCALES}
										placeholder={$i18n.t('e.g., en-US,ja-JP (leave blank for auto-detect)')}
									/>
								</div>
							</div>
						</div>
					</div>
				{:else if STT_ENGINE === ''}
					<div>
						<div class=" mb-1.5 text-sm font-medium">{$i18n.t('STT Model')}</div>

						<div class="flex w-full">
							<div class="flex-1 mr-2">
								<input
									class="w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
									placeholder={$i18n.t('Set whisper model')}
									bind:value={STT_WHISPER_MODEL}
								/>
							</div>

							<button
								class="px-2.5 bg-secondary hover:bg-secondary-hover text-secondary-content rounded-lg"
								type="button"
								aria-label={$i18n.t('Download')}
								onclick={() => {
									sttModelUpdateHandler();
								}}
								disabled={STT_WHISPER_MODEL_LOADING}
							>
								{#if STT_WHISPER_MODEL_LOADING}
									<Spinner className="size-4" />
								{:else}
									<span class="i-lucide-download size-4 block"></span>
								{/if}
							</button>
						</div>

						<div class="mt-2 mb-1 text-xs text-content-subtle">
							{$i18n.t(`Nucllei uses faster-whisper internally.`)}

							<a
								class=" hover:underline text-content"
								href="https://github.com/SYSTRAN/faster-whisper"
								target="_blank"
							>
								{$i18n.t(
									`Click here to learn more about faster-whisper and see the available models.`
								)}
							</a>
						</div>
					</div>
				{/if}
			</div>

			<hr class="border-line" />

			<div>
				<div class=" mb-1 text-sm font-medium">{$i18n.t('TTS Settings')}</div>

				<div class=" py-0.5 flex w-full justify-between">
					<div class=" self-center text-xs font-medium">{$i18n.t('Enable Text-to-Speech')}</div>
					<div class="flex items-center relative">
						<Switch bind:state={TTS_ENABLE} />
					</div>
				</div>

				<div class=" py-0.5 flex w-full justify-between">
					<div class=" self-center text-xs font-medium">{$i18n.t('Text-to-Speech Engine')}</div>
					<div class="flex items-center relative">
						<select
							class="bg-surface-raised hover:bg-surface-hover w-fit cursor-pointer px-2 py-0.5 pr-6 rounded-xs text-xs outline-hidden text-right"
							bind:value={TTS_ENGINE}
							placeholder="Select a mode"
							onchange={async (e) => {
								await updateConfigHandler();
								await getVoices();
								await getModels();

								if (e.currentTarget?.value === 'openai') {
									TTS_VOICE = 'alloy';
									TTS_MODEL = 'tts-1';
								} else {
									TTS_VOICE = '';
									TTS_MODEL = '';
								}
							}}
						>
							<option value="">{$i18n.t('Web API')}</option>
							<option value="transformers">{$i18n.t('Transformers')} ({$i18n.t('Local')})</option>
							<option value="openai">{$i18n.t('OpenAI')}</option>
							<option value="elevenlabs">{$i18n.t('ElevenLabs')}</option>
							<option value="azure">{$i18n.t('Azure AI Speech')}</option>
						</select>
					</div>
				</div>

				{#if TTS_ENGINE === 'openai'}
					<div>
						<div class="mt-1 flex gap-2 mb-1">
							<input
								class="flex-1 w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
								placeholder={$i18n.t('API Base URL')}
								bind:value={TTS_OPENAI_API_BASE_URL}
								required
							/>

							<SensitiveInput
								outerClassName="flex flex-1 px-2 bg-surface-raised rounded-xs"
								inputClassName="w-full text-sm py-1 placeholder:text-content-subtle bg-transparent outline-hidden"
								placeholder={$i18n.t('API Key')}
								bind:value={TTS_OPENAI_API_KEY}
							/>
						</div>
					</div>
				{:else if TTS_ENGINE === 'elevenlabs'}
					<div>
						<div class="mt-1 flex gap-2 mb-1">
							<input
								class="flex-1 w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
								placeholder={$i18n.t('API Key')}
								bind:value={TTS_API_KEY}
								required
							/>
						</div>
					</div>
				{:else if TTS_ENGINE === 'azure'}
					<div>
						<div class="mt-1 flex gap-2 mb-1">
							<input
								class="flex-1 w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
								placeholder={$i18n.t('API Key')}
								bind:value={TTS_API_KEY}
								required
							/>
							<input
								class="flex-1 w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
								placeholder={$i18n.t('Azure Region')}
								bind:value={TTS_AZURE_SPEECH_REGION}
								required
							/>
						</div>
					</div>
				{/if}

				<hr class="border-line my-2" />

				{#if TTS_ENGINE === ''}
					<div>
						<div class=" mb-1.5 text-sm font-medium">{$i18n.t('TTS Voice')}</div>
						<div class="flex w-full">
							<div class="flex-1">
								<select
									class="w-full text-sm px-2 py-1 bg-surface-raised hover:bg-surface-hover rounded-xs outline-hidden"
									bind:value={TTS_VOICE}
								>
									<option value="" selected={TTS_VOICE !== ''}>{$i18n.t('Default')}</option>
									{#each voices as voice}
										<option value={voice.voiceURI} selected={TTS_VOICE === voice.voiceURI}
											>{voice.name}</option
										>
									{/each}
								</select>
							</div>
						</div>
					</div>
				{:else if TTS_ENGINE === 'transformers'}
					<div>
						<div class=" mb-1.5 text-sm font-medium">{$i18n.t('TTS Model')}</div>
						<div class="flex w-full">
							<div class="flex-1">
								<input
									list="model-list"
									class="w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
									bind:value={TTS_MODEL}
									placeholder="CMU ARCTIC speaker embedding name"
								/>

								<datalist id="model-list">
									<option value="tts-1"></option>
								</datalist>
							</div>
						</div>
						<div class="mt-2 mb-1 text-xs text-content-subtle">
							{$i18n.t(`Nucllei uses SpeechT5 and CMU Arctic speaker embeddings.`)}

							To learn more about SpeechT5,

							<a
								class=" hover:underline text-content"
								href="https://github.com/microsoft/SpeechT5"
								target="_blank"
							>
								{$i18n.t(`click here`, {
									name: 'SpeechT5'
								})}.
							</a>
							To see the available CMU Arctic speaker embeddings,
							<a
								class=" hover:underline text-content"
								href="https://huggingface.co/datasets/Matthijs/cmu-arctic-xvectors"
								target="_blank"
							>
								{$i18n.t(`click here`)}.
							</a>
						</div>
					</div>
				{:else if TTS_ENGINE === 'openai' || TTS_ENGINE === 'elevenlabs'}
					<div class=" flex gap-2">
						<div class="w-full">
							<div class=" mb-1.5 text-sm font-medium">{$i18n.t('TTS Voice')}</div>
							<div class="flex w-full">
								<div class="flex-1">
									<input
										list="voice-list"
										class="w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
										bind:value={TTS_VOICE}
										placeholder="Select a voice"
									/>

									<datalist id="voice-list">
										{#each voices as voice}
											<option value={voice.id}>{voice.name}</option>
										{/each}
									</datalist>
								</div>
							</div>
						</div>
						<div class="w-full">
							<div class=" mb-1.5 text-sm font-medium">{$i18n.t('TTS Model')}</div>
							<div class="flex w-full">
								<div class="flex-1">
									<input
										list="tts-model-list"
										class="w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
										bind:value={TTS_MODEL}
										placeholder="Select a model"
									/>

									<datalist id="tts-model-list">
										{#each models as model}
											<option value={model.id}></option>
										{/each}
									</datalist>
								</div>
							</div>
						</div>
					</div>
				{:else if TTS_ENGINE === 'azure'}
					<div class=" flex gap-2">
						<div class="w-full">
							<div class=" mb-1.5 text-sm font-medium">{$i18n.t('TTS Voice')}</div>
							<div class="flex w-full">
								<div class="flex-1">
									<input
										list="voice-list"
										class="w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
										bind:value={TTS_VOICE}
										placeholder="Select a voice"
									/>

									<datalist id="voice-list">
										{#each voices as voice}
											<option value={voice.id}>{voice.name}</option>
										{/each}
									</datalist>
								</div>
							</div>
						</div>
						<div class="w-full">
							<div class=" mb-1.5 text-sm font-medium">
								{$i18n.t('Output format')}
								<a
									href="https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-text-to-speech?tabs=streaming#audio-outputs"
									target="_blank"
								>
									<small>{$i18n.t('Available list')}</small>
								</a>
							</div>
							<div class="flex w-full">
								<div class="flex-1">
									<input
										list="tts-model-list"
										class="w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
										bind:value={TTS_AZURE_SPEECH_OUTPUT_FORMAT}
										placeholder="Select a output format"
									/>
								</div>
							</div>
						</div>
					</div>
				{/if}

				<hr class="border-line my-2" />

				<div class="pt-0.5 flex w-full justify-between">
					<div class="self-center text-xs font-medium">{$i18n.t('Response splitting')}</div>
					<div class="flex items-center relative">
						<select
							class="bg-surface-raised hover:bg-surface-hover w-fit cursor-pointer px-2 py-0.5 pr-6 rounded-xs text-xs outline-hidden text-right"
							aria-label="Select how to split message text for TTS requests"
							bind:value={TTS_SPLIT_ON}
						>
							{#each Object.values(TTS_RESPONSE_SPLIT) as split}
								<option value={split}
									>{$i18n.t(split.charAt(0).toUpperCase() + split.slice(1))}</option
								>
							{/each}
						</select>
					</div>
				</div>
				<div class="mt-2 mb-1 text-xs text-content-subtle">
					{$i18n.t(
						"Control how message text is split for TTS requests. 'Punctuation' splits into sentences, 'paragraphs' splits into paragraphs, and 'none' keeps the message as a single string."
					)}
				</div>
			</div>
		</div>
	</div>
	<div class="flex justify-end text-sm font-medium">
		<button
			class="px-3.5 py-1.5 text-sm font-medium bg-primary hover:bg-primary-hover text-primary-content rounded-full"
			type="submit"
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>

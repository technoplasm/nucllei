<script lang="ts">
	import { toast } from 'svelte-sonner';

	import { onMount, tick, getContext, onDestroy } from 'svelte';

	import { type Model, mobile, settings, models, config } from '$lib/stores';

	import { blobToFile, extractCurlyBraceWords } from '$lib/utils';

	import { DEFAULT_MODEL_IMAGE_URL } from '$lib/constants';

	import InputMenu from './MessageInput/InputMenu.svelte';
	import VoiceRecording from './MessageInput/VoiceRecording.svelte';
	import FilesOverlay from './MessageInput/FilesOverlay.svelte';
	import Commands from './MessageInput/Commands.svelte';
	import FileLibraryModal from './MessageInput/FileLibraryModal.svelte';
	import InputFiles from './MessageInput/InputFiles.svelte';
	import CallButton from './MessageInput/CallButton.svelte';
	import UsageChip from './MessageInput/UsageChip.svelte';
	import BackgroundTasksChip from './MessageInput/BackgroundTasksChip.svelte';
	import { MessageInputUploads } from './MessageInput/uploads';

	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n = getContext('i18n');

	let {
		transparentBackground = false,
		onChange = () => {},
		onSubmit = () => {},
		createMessagePair,
		stopResponse,
		autoScroll = $bindable(false),
		atSelectedModel = $bindable(undefined),
		selectedModels,
		history,
		taskIds = null,
		prompt = $bindable(''),
		files = $bindable([]),
		placeholder = ''
	}: {
		transparentBackground?: boolean;
		onChange?: (input: { prompt: string; files: any[] }) => void;
		onSubmit?: (prompt: string) => void;
		createMessagePair: Function;
		stopResponse: Function;
		autoScroll?: boolean;
		atSelectedModel?: Model | undefined;
		selectedModels: string[];
		history: any;
		taskIds?: string[] | null;
		prompt?: string;
		files?: any[];
		placeholder?: string;
	} = $props();

	const selectedModelIds = $derived(
		atSelectedModel !== undefined ? [atSelectedModel.id] : selectedModels
	);

	// llens(#42): `/command` 補完は「選択モデルに紐づき、かつ機能スキルが有効」な skill だけ。
	// 複数モデル選択時は和集合(どれかで発火し得るものを出す)。
	const allowedSkillIds = $derived(
		selectedModelIds
			.map((id) => $models.find((m) => m.id === id))
			.filter((m) => m && (m?.info?.meta?.features?.skills ?? true) !== false)
			.flatMap((m) => m?.info?.meta?.skillIds ?? [])
	);

	$effect(() => {
		onChange({
			prompt,
			files
		});
	});

	let loaded = $state(false);
	let recording = $state(false);

	let isComposing = $state(false);

	let chatInputElement = $state<HTMLTextAreaElement>();

	let filesInputElement = $state<HTMLInputElement>();
	let commandsElement = $state<any>();

	let inputFiles = $state<FileList | null>(null);
	let dragged = $state(false);

	let showFileLibraryModal = $state(false);

	const visionCapableModels = $derived(
		[...(atSelectedModel ? [atSelectedModel] : selectedModels)].filter(
			(model) => $models.find((m) => m.id === model)?.info?.meta?.capabilities?.vision ?? true
		)
	);

	const t = (key: string, options?: Record<string, unknown>) => $i18n.t(key, options);

	// アップロード・ライブラリ再添付・添付解除。files は $bindable なのでアクセサで渡す
	const uploads = new MessageInputUploads({
		t,
		getFiles: () => files,
		setFiles: (value) => (files = value),
		getVisionCapableModels: () => visionCapableModels
	});

	const scrollToBottom = () => {
		const element = document.getElementById('messages-container');
		if (element) {
			element.scrollTo({
				top: element.scrollHeight,
				behavior: 'smooth'
			});
		}
	};

	// 送信キー(Enter ⇄ Ctrl+Enter)。切り替え UI は「+」メニュー(InputMenu)にある
	const ctrlEnterToSend = $derived($settings?.ctrlEnterToSend ?? true);

	// 設定値は 'LTR' | 'RTL' | 'auto'(大文字)— dir 属性用に小文字化(UserMessage.svelte と同じ)
	const chatDirection = $derived(
		$settings?.chatDirection
			? ($settings.chatDirection.toLowerCase() as 'ltr' | 'rtl' | 'auto')
			: 'auto'
	);

	const onPromptKeydown = async (e: KeyboardEvent) => {
		const isCtrlPressed = e.ctrlKey || e.metaKey; // metaKey is for Cmd key on Mac

		const commandsContainerElement = document.getElementById('commands-container');

		if (e.key === 'Escape') {
			stopResponse();
		}

		// Command/Ctrl + Shift + Enter to submit a message pair
		if (isCtrlPressed && e.key === 'Enter' && e.shiftKey) {
			e.preventDefault();
			createMessagePair(prompt);
		}

		// Check if Ctrl + R is pressed
		if (prompt === '' && isCtrlPressed && e.key.toLowerCase() === 'r') {
			e.preventDefault();

			const regenerateButton = (
				[...document.getElementsByClassName('regenerate-response-button')] as HTMLElement[]
			)?.at(-1);

			regenerateButton?.click();
		}

		if (prompt === '' && e.key == 'ArrowUp') {
			e.preventDefault();

			const userMessageElement = [...document.getElementsByClassName('user-message')]?.at(-1);

			const editButton = (
				[...document.getElementsByClassName('edit-user-message-button')] as HTMLElement[]
			)?.at(-1);

			userMessageElement?.scrollIntoView({ block: 'center' });
			editButton?.click();
		}

		if (commandsContainerElement) {
			if (commandsContainerElement && e.key === 'ArrowUp') {
				e.preventDefault();
				commandsElement.selectUp();

				const commandOptionButton = (
					[...document.getElementsByClassName('selected-command-option-button')] as HTMLElement[]
				)?.at(-1);
				commandOptionButton?.scrollIntoView({ block: 'center' });
			}

			if (commandsContainerElement && e.key === 'ArrowDown') {
				e.preventDefault();
				commandsElement.selectDown();

				const commandOptionButton = (
					[...document.getElementsByClassName('selected-command-option-button')] as HTMLElement[]
				)?.at(-1);
				commandOptionButton?.scrollIntoView({ block: 'center' });
			}

			if (commandsContainerElement && e.key === 'Enter') {
				e.preventDefault();

				const commandOptionButton = (
					[...document.getElementsByClassName('selected-command-option-button')] as HTMLElement[]
				)?.at(-1);

				if (e.shiftKey) {
					prompt = `${prompt}\n`;
				} else if (commandOptionButton) {
					commandOptionButton?.click();
				} else {
					document.getElementById('send-message-button')?.click();
				}
			}

			if (commandsContainerElement && e.key === 'Tab') {
				e.preventDefault();

				const commandOptionButton = (
					[...document.getElementsByClassName('selected-command-option-button')] as HTMLElement[]
				)?.at(-1);

				commandOptionButton?.click();
			}
		} else {
			if (
				!$mobile ||
				!(
					'ontouchstart' in window ||
					navigator.maxTouchPoints > 0 ||
					// IE/旧 Edge の prefixed プロパティ(lib.dom 未収載)— 型だけ拡張
					((navigator as Navigator & { msMaxTouchPoints?: number }).msMaxTouchPoints ?? 0) > 0
				)
			) {
				if (isComposing) {
					return;
				}

				// Prevent Enter key from creating a new line
				const isCtrlPressed = e.ctrlKey || e.metaKey;
				const enterPressed =
					($settings?.ctrlEnterToSend ?? true)
						? (e.key === 'Enter' || e.keyCode === 13) && isCtrlPressed
						: (e.key === 'Enter' || e.keyCode === 13) && !e.shiftKey;

				if (enterPressed) {
					e.preventDefault();
				}

				// Submit the prompt when Enter key is pressed
				if ((prompt !== '' || files.length > 0) && enterPressed) {
					onSubmit(prompt);
				}
			}
		}

		if (e.key === 'Tab') {
			const words = extractCurlyBraceWords(prompt);
			// await 後も参照できるよう e.target を textarea として捕まえる
			const target = e.target as HTMLTextAreaElement;

			if (words.length > 0) {
				const word = words[0];
				const fullPrompt = prompt;

				prompt = prompt.substring(0, word.endIndex + 1);
				await tick();

				target.scrollTop = target.scrollHeight;
				prompt = fullPrompt;
				await tick();

				e.preventDefault();
				target.setSelectionRange(word.startIndex, word.endIndex + 1);
			}

			target.style.height = '';
			target.style.height = Math.min(target.scrollHeight, 320) + 'px';
		}

		if (e.key === 'Escape') {
			atSelectedModel = undefined;
		}
	};

	// 入力量に応じた textarea の自動リサイズ(input / focus 共通)
	const autosizePrompt = (e: Event) => {
		const target = e.target as HTMLTextAreaElement;
		target.style.height = '';
		target.style.height = Math.min(target.scrollHeight, 320) + 'px';
	};

	const onPromptPaste = async (e: ClipboardEvent) => {
		// IE 由来の window.clipboardData(lib.dom 未収載)— 型だけ拡張
		const clipboardData =
			e.clipboardData || (window as Window & { clipboardData?: DataTransfer }).clipboardData;

		if (clipboardData && clipboardData.items) {
			for (const item of clipboardData.items) {
				if (item.type.indexOf('image') !== -1) {
					// llens(#69): paste 画像も他の画像と同じアップロード経路に統一
					const blob = item.getAsFile();
					if (blob) {
						uploads.inputFilesHandler([blobToFile(blob, `Pasted-Image-${Date.now()}.png`)]);
					}
				} else if (item.type === 'text/plain') {
					// llens: 長文ペーストの .txt 化は配備設定(PASTED_TEXT_CHARACTER_LIMIT)
					// のみで駆動する。未設定 = 無効(per-user トグルは廃止)
					const pastedTextLimit = $config?.file?.pasted_text_character_limit ?? null;
					if (pastedTextLimit) {
						const text = clipboardData.getData('text/plain');

						if (text.length > pastedTextLimit) {
							e.preventDefault();
							const blob = new Blob([text], { type: 'text/plain' });
							const file = new File([blob], `Pasted_Text_${Date.now()}.txt`, {
								type: 'text/plain'
							});

							await uploads.uploadFileHandler(file, true);
						}
					}
				}
			}
		}
	};

	// llens(#56): マイクは secure context(https / localhost)でしか使えない。
	// http 配備では navigator.mediaDevices ごと生えず、getUserMedia が例外になって
	// 「押しても無反応」になるので、押す前に理由を出せるようにここで判定する。
	// ※ 配備レベルの ENABLE_AUDIO_STT(アイコンごと非表示)とは別物 —
	//    あちらは「この配備に機能があるか」、こちらは「このブラウザ経路で使えるか」
	const micAvailable = !!navigator?.mediaDevices?.getUserMedia;
	const micUnavailableMessage = 'Microphone requires a secure (HTTPS) connection.';

	// マイク権限を確認してから録音 UI(VoiceRecording)へ切り替える
	const onRecordVoiceClick = async () => {
		if (!micAvailable) {
			toast.error($i18n.t(micUnavailableMessage));
			return;
		}

		try {
			let stream = await navigator.mediaDevices.getUserMedia({ audio: true }).catch(function (err) {
				toast.error(
					$i18n.t(`Permission denied when accessing microphone: {{error}}`, {
						error: err
					})
				);
				return null;
			});

			if (stream) {
				recording = true;
				const tracks = stream.getTracks();
				tracks.forEach((track) => track.stop());
			}
			stream = null;
		} catch {
			toast.error($i18n.t('Permission denied when accessing microphone'));
		}
	};

	const handleKeyDown = (event: KeyboardEvent) => {
		if (event.key === 'Escape') {
			dragged = false;
		}
	};

	const onDragOver = (e: DragEvent) => {
		e.preventDefault();

		// Check if a file is being dragged.
		if (e.dataTransfer?.types?.includes('Files')) {
			dragged = true;
		} else {
			dragged = false;
		}
	};

	const onDragLeave = () => {
		dragged = false;
	};

	const onDrop = async (e: DragEvent) => {
		e.preventDefault();

		if (e.dataTransfer?.files) {
			const inputFiles = Array.from(e.dataTransfer?.files);
			if (inputFiles && inputFiles.length > 0) {
				uploads.inputFilesHandler(inputFiles);
			}
		}

		dragged = false;
	};

	onMount(async () => {
		loaded = true;

		window.setTimeout(() => {
			const chatInput = document.getElementById('chat-input');
			chatInput?.focus();
		}, 0);

		window.addEventListener('keydown', handleKeyDown);

		await tick();

		const dropzoneElement = document.getElementById('chat-container');

		dropzoneElement?.addEventListener('dragover', onDragOver);
		dropzoneElement?.addEventListener('drop', onDrop);
		dropzoneElement?.addEventListener('dragleave', onDragLeave);
	});

	onDestroy(() => {
		window.removeEventListener('keydown', handleKeyDown);

		const dropzoneElement = document.getElementById('chat-container');

		if (dropzoneElement) {
			dropzoneElement?.removeEventListener('dragover', onDragOver);
			dropzoneElement?.removeEventListener('drop', onDrop);
			dropzoneElement?.removeEventListener('dragleave', onDragLeave);
		}
	});
</script>

<FilesOverlay show={dragged} />

<FileLibraryModal bind:show={showFileLibraryModal} onSelect={uploads.libraryFilesHandler} />

{#if loaded}
	<div class="w-full font-primary">
		<div class=" mx-auto inset-x-0 bg-transparent flex justify-center">
			<div
				class="flex flex-col px-3 {($settings?.widescreenMode ?? null)
					? 'max-w-full'
					: 'max-w-6xl'} w-full"
			>
				<div class="relative">
					{#if autoScroll === false && history?.currentId}
						<div
							class=" absolute -top-12 left-0 right-0 flex justify-center z-30 pointer-events-none"
						>
							<button
								class=" bg-surface-raised border border-line p-1.5 rounded-full pointer-events-auto"
								aria-label={$i18n.t('Scroll to bottom')}
								onclick={() => {
									autoScroll = true;
									scrollToBottom();
								}}
							>
								<span class="i-lucide-arrow-down size-5 block"></span>
							</button>
						</div>
					{/if}

					<!-- llens(#102): floating トークンゲージ(入力カードの右上外側)。中央の
					     スクロールボタンとは位置が重ならない。データが無ければ何も描かない -->
					<div class="absolute -top-10 right-0 z-30 flex justify-end pointer-events-none">
						<UsageChip />
					</div>

					<!-- llens(#110): バックグラウンドタスク(exec 行)のチップ(トークンゲージと
					     同じ行の左側)。running も未配達 terminal も無ければ何も描かない -->
					<div class="absolute -top-10 left-0 z-30 flex justify-start pointer-events-none">
						<BackgroundTasksChip />
					</div>
				</div>

				<div class="w-full relative">
					{#if atSelectedModel !== undefined}
						<div
							class="px-3 pb-0.5 pt-1.5 text-left w-full flex flex-col absolute bottom-0 left-0 right-0 bg-linear-to-t from-surface z-10"
						>
							<div class="flex items-center justify-between w-full">
								<div class="pl-[1px] flex items-center gap-2 text-sm text-content-muted">
									<img
										crossorigin="anonymous"
										alt="model profile"
										class="size-3.5 max-w-[28px] object-cover rounded-full"
										src={$models.find((model) => model.id === atSelectedModel?.id)?.info?.meta
											?.profile_image_url ?? DEFAULT_MODEL_IMAGE_URL}
									/>
									<div class="translate-y-[0.5px] font-medium">
										{$i18n.t('Talking to {{modelName}}', { modelName: atSelectedModel.name })}
									</div>
								</div>
								<div>
									<button
										class="flex items-center text-content-muted"
										aria-label={$i18n.t('Close')}
										onclick={() => {
											atSelectedModel = undefined;
										}}
									>
										<span class="i-lucide-x size-3.5 block"></span>
									</button>
								</div>
							</div>
						</div>
					{/if}

					<Commands
						bind:this={commandsElement}
						bind:prompt
						{allowedSkillIds}
						onSelect={(data) => {
							if (data?.type === 'model') {
								atSelectedModel = data.data;
							}

							const chatInputElement = document.getElementById('chat-input');
							chatInputElement?.focus();
						}}
					/>
				</div>
			</div>
		</div>

		<div class="{transparentBackground ? 'bg-transparent' : 'bg-surface'} ">
			<div
				class="{($settings?.widescreenMode ?? null)
					? 'max-w-full'
					: 'max-w-6xl'} px-2.5 mx-auto inset-x-0"
			>
				<div class="">
					<input
						bind:this={filesInputElement}
						bind:files={inputFiles}
						type="file"
						hidden
						multiple
						onchange={async () => {
							if (inputFiles && inputFiles.length > 0) {
								const _inputFiles = Array.from(inputFiles);
								uploads.inputFilesHandler(_inputFiles);
							} else {
								toast.error($i18n.t(`File not found.`));
							}

							if (filesInputElement) {
								filesInputElement.value = '';
							}
						}}
					/>

					{#if recording}
						<VoiceRecording
							bind:recording
							onCancel={async () => {
								recording = false;

								await tick();
								document.getElementById('chat-input')?.focus();
							}}
							onTranscript={(data) => {
								// llens(#56): 逐次文字起こし。発話の区切りごとに確定テキストが届く
								// (最後の 1 区間は onConfirm 側で来るのでここでは追記しない)
								const { text } = data;
								if (text) {
									prompt = `${prompt}${text} `;
								}
							}}
							onConfirm={async (data) => {
								const { text } = data;
								if (text) {
									prompt = `${prompt}${text} `;
								}
								recording = false;

								await tick();
								document.getElementById('chat-input')?.focus();

								if ($settings?.speechAutoSend ?? false) {
									onSubmit(prompt);
								}
							}}
						/>
					{:else}
						<form
							class="w-full flex gap-1.5"
							onsubmit={(e) => {
								e.preventDefault();
								onSubmit(prompt);
							}}
						>
							<div
								class="flex-1 flex flex-col relative w-full shadow-[0_2px_12px_-4px_rgba(0,0,0,0.12)] rounded-3xl border border-line px-1 bg-surface-raised text-content"
								dir={chatDirection}
							>
								{#if files.length > 0}
									<InputFiles
										{files}
										{atSelectedModel}
										{selectedModels}
										{visionCapableModels}
										onDismiss={uploads.dismissFileHandler}
									/>
								{/if}

								<div class="px-2.5">
									<textarea
										id="chat-input"
										dir="auto"
										bind:this={chatInputElement}
										class="bg-transparent text-content outline-hidden w-full pt-3 px-1 resize-none"
										placeholder={placeholder ? placeholder : $i18n.t('Send a Message')}
										bind:value={prompt}
										oncompositionstart={() => (isComposing = true)}
										oncompositionend={() => (isComposing = false)}
										onkeydown={onPromptKeydown}
										rows="1"
										oninput={autosizePrompt}
										onfocus={autosizePrompt}
										onpaste={onPromptPaste}
									></textarea>
								</div>

								<div class=" flex justify-between mt-1 mb-2.5 mx-0.5 max-w-full" dir="ltr">
									<div class="ml-1 self-end flex items-center flex-1 max-w-[80%] gap-0.5">
										<InputMenu
											uploadFilesHandler={() => {
												filesInputElement?.click();
											}}
											openLibraryHandler={() => {
												showFileLibraryModal = true;
											}}
											onClose={async () => {
												await tick();

												const chatInput = document.getElementById('chat-input');
												chatInput?.focus();
											}}
										>
											<button
												class="bg-transparent hover:bg-surface-hover text-content rounded-full p-1.5 outline-hidden focus:outline-hidden"
												type="button"
												aria-label={$i18n.t('More')}
											>
												<span class="i-lucide-plus size-5 block"></span>
											</button>
										</InputMenu>
									</div>

									<div class="self-end flex space-x-1 mr-1 shrink-0">
										<!-- llens(#66/#53): per-user フラグは廃止(全員使用可)。
										     配備レベルの enable(audio.stt.enable)でアイコンごと制御 -->
										{#if ($config?.audio?.stt?.enable ?? true) && (!history?.currentId || history.messages[history.currentId]?.done == true)}
											<Tooltip
												content={micAvailable
													? $i18n.t('Record voice')
													: $i18n.t(micUnavailableMessage)}
											>
												<button
													id="voice-input-button"
													class=" {micAvailable
														? 'text-content-muted hover:text-content'
														: 'text-content-subtle cursor-not-allowed'} rounded-full p-1.5 mr-0.5 self-center"
													type="button"
													aria-disabled={!micAvailable}
													onclick={onRecordVoiceClick}
													aria-label={$i18n.t('Record voice')}
												>
													<span class="i-lucide-mic size-5 block translate-y-[0.5px]"></span>
												</button>
											</Tooltip>
										{/if}

										{#if (taskIds && taskIds.length > 0) || (history.currentId && history.messages[history.currentId]?.done != true)}
											<div class=" flex items-center">
												<Tooltip content={$i18n.t('Stop')}>
													<button
														class="bg-secondary hover:bg-secondary-hover text-secondary-content rounded-full p-1.5"
														aria-label={$i18n.t('Stop')}
														onclick={() => {
															stopResponse();
														}}
													>
														<span class="i-lucide-circle-stop size-5 block"></span>
													</button>
												</Tooltip>
											</div>
										{:else}
											{#if ($settings?.callEnabled ?? false) && prompt === '' && files.length === 0 && ($config?.audio?.stt?.enable ?? true) && ($config?.audio?.tts?.enable ?? true)}
												<!-- llens(#53): Call は STT+TTS 両方が有効なときだけ(片方無効なら出さない)。
											     ユーザー設定 callEnabled(既定 OFF)で表示自体を切り替える -->
												<CallButton {selectedModels} />
											{/if}

											<div class=" flex items-center">
												<Tooltip
													content={ctrlEnterToSend
														? `${$i18n.t('Ctrl+Enter to Send')}<br>${$i18n.t('Enter for a new line')}`
														: `${$i18n.t('Enter to Send')}<br>${$i18n.t('Shift+Enter for a new line')}`}
												>
													<button
														id="send-message-button"
														class="{!(prompt === '' && files.length === 0)
															? 'bg-primary hover:bg-primary-hover text-primary-content'
															: 'bg-secondary text-content-subtle'} rounded-full p-1.5 self-center"
														type="submit"
														aria-label={$i18n.t('Send message')}
														disabled={prompt === '' && files.length === 0}
													>
														<span class="i-lucide-arrow-up size-5 block"></span>
													</button>
												</Tooltip>
											</div>
										{/if}
									</div>
								</div>
							</div>
						</form>
					{/if}
				</div>
			</div>
		</div>
	</div>
{/if}

<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { v4 as uuidv4 } from 'uuid';

	import { onMount, tick, getContext, onDestroy } from 'svelte';

	import {
		type Model,
		mobile,
		settings,
		models,
		config,
		showCallOverlay,
		user as _user,
		showControls,
		TTSWorker
	} from '$lib/stores';

	import { blobToFile, compressImage, extractCurlyBraceWords } from '$lib/utils';
	import { uploadFile, deleteFileById, processFileById } from '$lib/apis/files';

	import {
		WEBUI_API_BASE_URL,
		PASTED_TEXT_CHARACTER_LIMIT,
		DEFAULT_MODEL_IMAGE_URL
	} from '$lib/constants';

	import InputMenu from './MessageInput/InputMenu.svelte';
	import VoiceRecording from './MessageInput/VoiceRecording.svelte';
	import FilesOverlay from './MessageInput/FilesOverlay.svelte';
	import Commands from './MessageInput/Commands.svelte';
	import FileLibraryModal from './MessageInput/FileLibraryModal.svelte';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import FileItem from '$lib/components/common/FileItem.svelte';
	import Image from '$lib/components/common/Image.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	import { KokoroWorker } from '$lib/workers/KokoroWorker';

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

	const visionCapableModels = $derived(
		[...(atSelectedModel ? [atSelectedModel] : selectedModels)].filter(
			(model) => $models.find((m) => m.id === model)?.info?.meta?.capabilities?.vision ?? true
		)
	);

	const scrollToBottom = () => {
		const element = document.getElementById('messages-container');
		element.scrollTo({
			top: element.scrollHeight,
			behavior: 'smooth'
		});
	};

	const uploadFileHandler = async (file, fullContext: boolean = false) => {
		if ($_user?.role !== 'admin' && !($_user?.permissions?.chat?.file_upload ?? true)) {
			toast.error($i18n.t('You do not have permission to upload files.'));
			return null;
		}

		const tempItemId = uuidv4();
		const fileItem = {
			type: 'file',
			file: '',
			id: null,
			url: '',
			name: file.name,
			collection_name: '',
			status: 'uploading',
			size: file.size,
			error: '',
			itemId: tempItemId,
			...(fullContext ? { context: 'full' } : {})
		};

		if (fileItem.size == 0) {
			toast.error($i18n.t('You cannot upload an empty file.'));
			return null;
		}

		files = [...files, fileItem];

		try {
			// During the file upload, file content is automatically extracted.
			// llens(#69): グローバル Attachment(ATTACHMENT_FUNCTION_ID)はサーバー側で発火する。
			const uploadedFile = await uploadFile(file);

			if (uploadedFile) {
				// アップロード中に X で外された場合はサーバーに orphan を残さない
				if (!files.some((item) => item?.itemId === tempItemId)) {
					deleteFileById(uploadedFile.id).catch(() => {});
					return null;
				}

				if (uploadedFile.error) {
					console.warn('File upload warning:', uploadedFile.error);
					toast.warning(uploadedFile.error);
				}

				fileItem.status = 'uploaded';
				fileItem.file = uploadedFile;
				fileItem.id = uploadedFile.id;
				fileItem.collection_name =
					uploadedFile?.meta?.collection_name || uploadedFile?.collection_name;
				fileItem.url = `${WEBUI_API_BASE_URL}/files/${uploadedFile.id}`;

				files = files;
			} else {
				files = files.filter((item) => item?.itemId !== tempItemId);
			}
		} catch (e) {
			toast.error(`${e}`);
			files = files.filter((item) => item?.itemId !== tempItemId);
		}
	};

	// llens(#69): 画像も file として backend に保存する(base64 直入れを廃止)。
	// chat JSON には正準形 "/api/v1/files/{id}/content" の相対 URL を保存する
	// (送信 payload 中のこの形式だけ backend が base64 化する)。
	// アップロード中はローカル objectURL でプレビュー(status:'uploading')。
	const uploadImageHandler = async (file) => {
		if ($_user?.role !== 'admin' && !($_user?.permissions?.chat?.file_upload ?? true)) {
			toast.error($i18n.t('You do not have permission to upload files.'));
			return null;
		}

		if (file.size == 0) {
			toast.error($i18n.t('You cannot upload an empty file.'));
			return null;
		}

		const tempItemId = uuidv4();
		const previewUrl = URL.createObjectURL(file);
		const fileItem = {
			type: 'image',
			file: '',
			id: null,
			url: previewUrl,
			name: file.name,
			status: 'uploading',
			size: file.size,
			error: '',
			itemId: tempItemId
		};

		files = [...files, fileItem];

		try {
			// 圧縮設定(既存挙動を維持)は dataURL 経由で適用し、file に戻してからアップロード
			let uploadTarget = file;

			if ($settings?.imageCompression ?? false) {
				const width = $settings?.imageCompressionSize?.width ?? null;
				const height = $settings?.imageCompressionSize?.height ?? null;

				if (width || height) {
					const dataUrl = await new Promise((resolve, reject) => {
						const reader = new FileReader();
						reader.onload = () => resolve(reader.result);
						reader.onerror = reject;
						reader.readAsDataURL(file);
					});

					const compressedUrl = await compressImage(dataUrl, width, height);
					if (compressedUrl !== dataUrl) {
						const compressedBlob = await (await fetch(compressedUrl)).blob();
						uploadTarget = blobToFile(compressedBlob, file.name);
					}
				}
			}

			const uploadedFile = await uploadFile(uploadTarget);

			if (uploadedFile) {
				// アップロード中に X で外された場合はサーバーに orphan を残さない
				if (!files.some((item) => item?.itemId === tempItemId)) {
					deleteFileById(uploadedFile.id).catch(() => {});
					return null;
				}

				if (uploadedFile.error) {
					console.warn('File upload warning:', uploadedFile.error);
					toast.warning(uploadedFile.error);
				}

				fileItem.status = 'uploaded';
				fileItem.file = uploadedFile;
				fileItem.id = uploadedFile.id;
				fileItem.name = uploadedFile?.meta?.name ?? file.name;
				fileItem.size = uploadedFile?.meta?.size ?? file.size;
				fileItem.url = `/api/v1/files/${uploadedFile.id}/content`;

				files = files;
			} else {
				files = files.filter((item) => item?.itemId !== tempItemId);
			}
		} catch (e) {
			toast.error(`${e}`);
			files = files.filter((item) => item?.itemId !== tempItemId);
		} finally {
			URL.revokeObjectURL(previewUrl);
		}
	};

	// llens(#69): 保存済みファイルの再添付。processFileById で Attachment フックを再発火し
	// (発火は owner/admin のみサーバー側で制御)、戻りの FileModel から files エントリを組む。
	// origin:'library' — dismiss 時に実体削除しない(添付解除のみ)。
	const libraryFilesHandler = async (selected) => {
		for (const item of selected ?? []) {
			if (files.some((f) => f?.id === item.id)) {
				continue;
			}

			const isImage = (item?.meta?.content_type ?? '').startsWith('image/');
			if (isImage && visionCapableModels.length === 0) {
				toast.error($i18n.t('Selected model(s) do not support image inputs'));
				continue;
			}

			try {
				const file = (await processFileById(item.id)) ?? item;

				if (isImage) {
					files = [
						...files,
						{
							type: 'image',
							id: file.id,
							url: `/api/v1/files/${file.id}/content`,
							name: file?.meta?.name ?? file?.filename,
							size: file?.meta?.size,
							status: 'uploaded',
							itemId: uuidv4(),
							origin: 'library'
						}
					];
				} else {
					files = [
						...files,
						{
							type: 'file',
							file: file,
							id: file.id,
							url: `${WEBUI_API_BASE_URL}/files/${file.id}`,
							name: file?.meta?.name ?? file?.filename,
							collection_name: file?.meta?.collection_name ?? file?.collection_name ?? '',
							status: 'uploaded',
							size: file?.meta?.size,
							error: '',
							itemId: uuidv4(),
							origin: 'library'
						}
					];
				}
			} catch (e) {
				toast.error(`${e}`);
			}
		}
	};

	// llens(#69): dismiss の意味論 — 新規アップロード分は実体削除、
	// library 再添付分(origin:'library')は添付解除のみ。
	const dismissFileHandler = async (fileIdx) => {
		const file = files[fileIdx];

		if (file?.type !== 'collection' && !file?.collection && file?.origin !== 'library') {
			if (file?.id) {
				// This will handle both file deletion and Chroma cleanup
				await deleteFileById(file.id);
			}
		}

		// Remove from UI state
		files.splice(fileIdx, 1);
		files = files;
	};

	let showFileLibraryModal = $state(false);

	// 送信キー(Enter ⇄ Ctrl+Enter)。切り替え UI は「+」メニュー(InputMenu)にある
	const ctrlEnterToSend = $derived($settings?.ctrlEnterToSend ?? false);

	const inputFilesHandler = async (inputFiles) => {
		inputFiles.forEach((file) => {
			if (
				($config?.file?.max_size ?? null) !== null &&
				file.size > ($config?.file?.max_size ?? 0) * 1024 * 1024
			) {
				toast.error(
					$i18n.t(`File size should not exceed {{maxSize}} MB.`, {
						maxSize: $config?.file?.max_size
					})
				);
				return;
			}

			if (
				['image/gif', 'image/webp', 'image/jpeg', 'image/png', 'image/avif'].includes(file['type'])
			) {
				if (visionCapableModels.length === 0) {
					toast.error($i18n.t('Selected model(s) do not support image inputs'));
					return;
				}
				uploadImageHandler(file);
			} else {
				uploadFileHandler(file);
			}
		});
	};

	const handleKeyDown = (event: KeyboardEvent) => {
		if (event.key === 'Escape') {
			dragged = false;
		}
	};

	const onDragOver = (e) => {
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

	const onDrop = async (e) => {
		e.preventDefault();

		if (e.dataTransfer?.files) {
			const inputFiles = Array.from(e.dataTransfer?.files);
			if (inputFiles && inputFiles.length > 0) {
				inputFilesHandler(inputFiles);
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

<FileLibraryModal bind:show={showFileLibraryModal} onSelect={libraryFilesHandler} />

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
										src={$models.find((model) => model.id === atSelectedModel.id)?.info?.meta
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
								inputFilesHandler(_inputFiles);
							} else {
								toast.error($i18n.t(`File not found.`));
							}

							filesInputElement.value = '';
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
							onConfirm={async (data) => {
								const { text } = data;
								prompt = `${prompt}${text} `;

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
								dir={$settings?.chatDirection ?? 'auto'}
							>
								{#if files.length > 0}
									<div class="mx-2 mt-2.5 -mb-1 flex items-center flex-wrap gap-2">
										{#each files as file, fileIdx}
											{#if file.type === 'image'}
												<div class=" relative group">
													<div class="relative flex items-center">
														<Image
															src={file.url}
															alt="input"
															imageClassName=" size-14 rounded-xl object-cover"
														/>
														{#if file.status === 'uploading'}
															<div
																class="absolute inset-0 flex items-center justify-center bg-black/20 rounded-xl pointer-events-none"
															>
																<Spinner className="size-4" />
															</div>
														{/if}
														{#if atSelectedModel ? visionCapableModels.length === 0 : selectedModels.length !== visionCapableModels.length}
															<Tooltip
																className=" absolute top-1 left-1"
																content={[...(atSelectedModel ? [atSelectedModel] : selectedModels)]
																	.filter((id) => !visionCapableModels.includes(id))
																	.join(', ')}
															>
																<span class="i-lucide-triangle-alert size-4 block text-yellow-300"
																></span>
															</Tooltip>
														{/if}
													</div>
													<div class=" absolute -top-1 -right-1">
														<button
															class=" bg-white text-black border border-white rounded-full group-hover:visible invisible"
															type="button"
															aria-label={$i18n.t('Remove file')}
															onclick={async () => {
																await dismissFileHandler(fileIdx);
															}}
														>
															<span class="i-lucide-x size-4 block"></span>
														</button>
													</div>
												</div>
											{:else}
												<FileItem
													item={file}
													name={file.name}
													type={file.type}
													size={file?.size}
													loading={file.status === 'uploading'}
													dismissible={true}
													onDismiss={async () => {
														await dismissFileHandler(fileIdx);
													}}
												/>
											{/if}
										{/each}
									</div>
								{/if}

								<div class="px-2.5">
									<textarea
										id="chat-input"
										dir="auto"
										bind:this={chatInputElement}
										class="scrollbar-hidden bg-transparent text-content outline-hidden w-full pt-3 px-1 resize-none"
										placeholder={placeholder ? placeholder : $i18n.t('Send a Message')}
										bind:value={prompt}
										oncompositionstart={() => (isComposing = true)}
										oncompositionend={() => (isComposing = false)}
										onkeydown={async (e) => {
											const isCtrlPressed = e.ctrlKey || e.metaKey; // metaKey is for Cmd key on Mac

											const commandsContainerElement =
												document.getElementById('commands-container');

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

												const regenerateButton = [
													...document.getElementsByClassName('regenerate-response-button')
												]?.at(-1);

												regenerateButton?.click();
											}

											if (prompt === '' && e.key == 'ArrowUp') {
												e.preventDefault();

												const userMessageElement = [
													...document.getElementsByClassName('user-message')
												]?.at(-1);

												const editButton = [
													...document.getElementsByClassName('edit-user-message-button')
												]?.at(-1);

												userMessageElement.scrollIntoView({ block: 'center' });
												editButton?.click();
											}

											if (commandsContainerElement) {
												if (commandsContainerElement && e.key === 'ArrowUp') {
													e.preventDefault();
													commandsElement.selectUp();

													const commandOptionButton = [
														...document.getElementsByClassName('selected-command-option-button')
													]?.at(-1);
													commandOptionButton.scrollIntoView({ block: 'center' });
												}

												if (commandsContainerElement && e.key === 'ArrowDown') {
													e.preventDefault();
													commandsElement.selectDown();

													const commandOptionButton = [
														...document.getElementsByClassName('selected-command-option-button')
													]?.at(-1);
													commandOptionButton.scrollIntoView({ block: 'center' });
												}

												if (commandsContainerElement && e.key === 'Enter') {
													e.preventDefault();

													const commandOptionButton = [
														...document.getElementsByClassName('selected-command-option-button')
													]?.at(-1);

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

													const commandOptionButton = [
														...document.getElementsByClassName('selected-command-option-button')
													]?.at(-1);

													commandOptionButton?.click();
												}
											} else {
												if (
													!$mobile ||
													!(
														'ontouchstart' in window ||
														navigator.maxTouchPoints > 0 ||
														navigator.msMaxTouchPoints > 0
													)
												) {
													if (isComposing) {
														return;
													}

													// Prevent Enter key from creating a new line
													const isCtrlPressed = e.ctrlKey || e.metaKey;
													const enterPressed =
														($settings?.ctrlEnterToSend ?? false)
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

												if (words.length > 0) {
													const word = words.at(0);
													const fullPrompt = prompt;

													prompt = prompt.substring(0, word?.endIndex + 1);
													await tick();

													e.target.scrollTop = e.target.scrollHeight;
													prompt = fullPrompt;
													await tick();

													e.preventDefault();
													e.target.setSelectionRange(word?.startIndex, word.endIndex + 1);
												}

												e.target.style.height = '';
												e.target.style.height = Math.min(e.target.scrollHeight, 320) + 'px';
											}

											if (e.key === 'Escape') {
												atSelectedModel = undefined;
											}
										}}
										rows="1"
										oninput={async (e) => {
											e.target.style.height = '';
											e.target.style.height = Math.min(e.target.scrollHeight, 320) + 'px';
										}}
										onfocus={async (e) => {
											e.target.style.height = '';
											e.target.style.height = Math.min(e.target.scrollHeight, 320) + 'px';
										}}
										onpaste={async (e) => {
											const clipboardData = e.clipboardData || window.clipboardData;

											if (clipboardData && clipboardData.items) {
												for (const item of clipboardData.items) {
													if (item.type.indexOf('image') !== -1) {
														// llens(#69): paste 画像も他の画像と同じアップロード経路に統一
														const blob = item.getAsFile();
														if (blob) {
															inputFilesHandler([
																blobToFile(blob, `Pasted-Image-${Date.now()}.png`)
															]);
														}
													} else if (item.type === 'text/plain') {
														if ($settings?.largeTextAsFile ?? false) {
															const text = clipboardData.getData('text/plain');

															if (text.length > PASTED_TEXT_CHARACTER_LIMIT) {
																e.preventDefault();
																const blob = new Blob([text], { type: 'text/plain' });
																const file = new File([blob], `Pasted_Text_${Date.now()}.txt`, {
																	type: 'text/plain'
																});

																await uploadFileHandler(file, true);
															}
														}
													}
												}
											}
										}}
									></textarea>
								</div>

								<div class=" flex justify-between mt-1 mb-2.5 mx-0.5 max-w-full" dir="ltr">
									<div class="ml-1 self-end flex items-center flex-1 max-w-[80%] gap-0.5">
										<InputMenu
											uploadFilesHandler={() => {
												filesInputElement.click();
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
											<Tooltip content={$i18n.t('Record voice')}>
												<button
													id="voice-input-button"
													class=" text-content-muted hover:text-content rounded-full p-1.5 mr-0.5 self-center"
													type="button"
													onclick={async () => {
														try {
															let stream = await navigator.mediaDevices
																.getUserMedia({ audio: true })
																.catch(function (err) {
																	toast.error(
																		$i18n.t(
																			`Permission denied when accessing microphone: {{error}}`,
																			{
																				error: err
																			}
																		)
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
													}}
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
												<div class=" flex items-center">
													<Tooltip content={$i18n.t('Call')}>
														<button
															class=" bg-primary hover:bg-primary-hover text-primary-content rounded-full p-1.5 self-center"
															type="button"
															onclick={async () => {
																if (selectedModels.length > 1) {
																	toast.error($i18n.t('Select only one model to call'));

																	return;
																}

																if ($config.audio.stt.engine === 'web') {
																	toast.error(
																		$i18n.t(
																			'Call feature is not supported when using Web STT engine'
																		)
																	);

																	return;
																}
																// check if user has access to getUserMedia
																try {
																	let stream = await navigator.mediaDevices.getUserMedia({
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
																			await TTSWorker.set(
																				new KokoroWorker({
																					dtype: $settings.audio?.tts?.engineConfig?.dtype ?? 'fp32'
																				})
																			);

																			await $TTSWorker.init();
																		}
																	}

																	showCallOverlay.set(true);
																	showControls.set(true);
																} catch (err) {
																	// If the user denies the permission or an error occurs, show an error message
																	toast.error(
																		$i18n.t('Permission denied when accessing media devices')
																	);
																}
															}}
															aria-label={$i18n.t('Call')}
														>
															<span class="i-lucide-headphones size-5 block"></span>
														</button>
													</Tooltip>
												</div>
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

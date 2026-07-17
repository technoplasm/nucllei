<script lang="ts">
	import { DropdownMenu } from 'bits-ui';
	import { DEFAULT_MODEL_IMAGE_URL } from '$lib/constants';
	import { render } from '$lib/utils/markdown-it';
	import Fuse from 'fuse.js';

	import { flyAndScale } from '$lib/utils/transitions';
	import { onMount, getContext, tick } from 'svelte';

	import { deleteModel, getOllamaVersion, pullModel } from '$lib/apis/ollama';
	import { updateUserSettings } from '$lib/apis/users';

	import { user, MODEL_DOWNLOAD_POOL, models, mobile, settings, type Model } from '$lib/stores';
	import { toast } from 'svelte-sonner';
	import { sanitizeResponseContent, splitStream } from '$lib/utils';
	import { getModels } from '$lib/apis';

	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n = getContext('i18n');

	let {
		id = '',
		value = $bindable(''),
		placeholder = 'Select a model',
		searchEnabled = true,
		searchPlaceholder = $i18n.t('Search a model'),
		items = [],
		className = 'w-[32rem]',
		triggerClassName = 'text-lg'
	}: {
		id?: string;
		value?: string;
		placeholder?: string;
		searchEnabled?: boolean;
		searchPlaceholder?: string;
		items?: {
			label: string;
			value: string;
			model: Model;
			// eslint-disable-next-line @typescript-eslint/no-explicit-any
			[key: string]: any;
		}[];
		className?: string;
		triggerClassName?: string;
	} = $props();

	// llens(#70): ユーザー既定モデル(新規チャットの初期選択)。$settings.models は
	// Chat.svelte の initNewChat が admin 既定(config.default_models)より先に読む
	// 既存経路 — 書く UI をここ(picker の bookmark)に置く。単一なので [id] 固定。
	// radio と checkbox の中間: 他を選ぶと付け替え、既定をもう一度押すと解除。
	const userDefaultModelId = $derived(($settings?.models ?? [])[0] ?? null);

	const toggleDefaultModel = async (modelId: string) => {
		const s = { ...($settings ?? {}) };
		if ((s.models ?? [])[0] === modelId) {
			delete s.models;
		} else {
			s.models = [modelId];
		}
		settings.set(s);
		await updateUserSettings({ ui: $settings });
	};

	let show = $state(false);

	const selectedModel = $derived(items.find((item) => item.value === value) ?? null);

	let searchValue = $state('');

	let selectedConnectionType = $state('');

	let ollamaVersion = $state<string | boolean | null>(null);
	let selectedModelIdx = $state(0);

	const fuse = $derived(
		new Fuse(
			items.map((item) => ({
				...item,
				modelName: item.model?.name,
				desc: item.model?.info?.meta?.description
			})),
			{
				keys: ['value', 'modelName'],
				threshold: 0.4
			}
		)
	);

	const filteredItems = $derived(
		(searchValue ? fuse.search(searchValue).map((e) => e.item) : items)
			.filter((item) => {
				if (selectedConnectionType === 'ollama') {
					return item.model?.owned_by === 'ollama';
				} else if (selectedConnectionType === 'openai') {
					return item.model?.owned_by === 'openai';
				}
				return true;
			})
			.filter((item) => !(item.model?.info?.meta?.hidden ?? false))
	);

	// フィルタ(Local/External)変更時に選択位置を追従させる
	// (resetView は先頭の await tick() 以降しか状態を読まないため、この effect の
	//  依存は selectedConnectionType のみ — 検索入力では発火しない)
	$effect(() => {
		void selectedConnectionType;
		resetView();
	});

	const resetView = async () => {
		await tick();

		const selectedInFiltered = filteredItems.findIndex((item) => item.value === value);

		if (selectedInFiltered >= 0) {
			// The selected model is visible in the current filter
			selectedModelIdx = selectedInFiltered;
		} else {
			// The selected model is not visible, default to first item in filtered list
			selectedModelIdx = 0;
		}

		await tick();
		const item = document.querySelector(`[data-arrow-selected="true"]`);
		item?.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
	};

	const pullModelHandler = async () => {
		const sanitizedModelTag = searchValue.trim().replace(/^ollama\s+(run|pull)\s+/, '');

		if ($MODEL_DOWNLOAD_POOL[sanitizedModelTag]) {
			toast.error(
				$i18n.t(`Model '{{modelTag}}' is already in queue for downloading.`, {
					modelTag: sanitizedModelTag
				})
			);
			return;
		}
		if (Object.keys($MODEL_DOWNLOAD_POOL).length === 3) {
			toast.error(
				$i18n.t('Maximum of 3 models can be downloaded simultaneously. Please try again later.')
			);
			return;
		}

		const [res, controller] = await pullModel(sanitizedModelTag, '0').catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			const reader = res.body
				.pipeThrough(new TextDecoderStream())
				.pipeThrough(splitStream('\n'))
				.getReader();

			MODEL_DOWNLOAD_POOL.set({
				...$MODEL_DOWNLOAD_POOL,
				[sanitizedModelTag]: {
					...$MODEL_DOWNLOAD_POOL[sanitizedModelTag],
					abortController: controller,
					reader,
					done: false
				}
			});

			while (true) {
				try {
					const { value, done } = await reader.read();
					if (done) break;

					let lines = value.split('\n');

					for (const line of lines) {
						if (line !== '') {
							let data = JSON.parse(line);
							if (data.error) {
								throw data.error;
							}
							if (data.detail) {
								throw data.detail;
							}

							if (data.status) {
								if (data.digest) {
									let downloadProgress = 0;
									if (data.completed) {
										downloadProgress = Math.round((data.completed / data.total) * 1000) / 10;
									} else {
										downloadProgress = 100;
									}

									MODEL_DOWNLOAD_POOL.set({
										...$MODEL_DOWNLOAD_POOL,
										[sanitizedModelTag]: {
											...$MODEL_DOWNLOAD_POOL[sanitizedModelTag],
											pullProgress: downloadProgress,
											digest: data.digest
										}
									});
								} else {
									toast.success(data.status);

									MODEL_DOWNLOAD_POOL.set({
										...$MODEL_DOWNLOAD_POOL,
										[sanitizedModelTag]: {
											...$MODEL_DOWNLOAD_POOL[sanitizedModelTag],
											done: data.status === 'success'
										}
									});
								}
							}
						}
					}
				} catch (error) {
					console.error(error);
					if (typeof error !== 'string') {
						error = error.message;
					}

					toast.error(`${error}`);
					break;
				}
			}

			if ($MODEL_DOWNLOAD_POOL[sanitizedModelTag].done) {
				toast.success(
					$i18n.t(`Model '{{modelName}}' has been successfully downloaded.`, {
						modelName: sanitizedModelTag
					})
				);

				models.set(await getModels());
			} else {
				toast.error($i18n.t('Download canceled'));
			}

			delete $MODEL_DOWNLOAD_POOL[sanitizedModelTag];

			MODEL_DOWNLOAD_POOL.set({
				...$MODEL_DOWNLOAD_POOL
			});
		}
	};

	onMount(async () => {
		ollamaVersion = await getOllamaVersion().catch(() => false);
	});

	const cancelModelPullHandler = async (model: string) => {
		const { reader, abortController } = $MODEL_DOWNLOAD_POOL[model];
		if (abortController) {
			abortController.abort();
		}
		if (reader) {
			await reader.cancel();
			delete $MODEL_DOWNLOAD_POOL[model];
			MODEL_DOWNLOAD_POOL.set({
				...$MODEL_DOWNLOAD_POOL
			});
			await deleteModel(model);
			toast.success($i18n.t('Download canceled'));
		}
	};
</script>

<DropdownMenu.Root
	bind:open={show}
	onOpenChange={async () => {
		searchValue = '';
		window.setTimeout(() => document.getElementById('model-search-input')?.focus(), 0);

		resetView();
	}}
	closeFocus={false}
>
	<DropdownMenu.Trigger
		class="relative w-full font-primary"
		aria-label={placeholder}
		id="model-selector-{id}-button"
	>
		<div
			class="flex w-full text-left px-0.5 outline-hidden bg-transparent truncate {triggerClassName} justify-between font-medium focus:outline-hidden"
		>
			{#if selectedModel}
				{selectedModel.label}
			{:else}
				{placeholder}
			{/if}
			<span class="i-lucide-chevron-down size-3 block self-center ml-2 shrink-0"></span>
		</div>
	</DropdownMenu.Trigger>

	<DropdownMenu.Content
		class=" z-40 {$mobile
			? `w-full`
			: `${className}`} max-w-[calc(100vw-1rem)] justify-start rounded-xl border border-line bg-surface-raised text-content shadow-sm outline-hidden"
		transition={flyAndScale}
		side={$mobile ? 'bottom' : 'bottom-start'}
		sideOffset={3}
	>
		{#if searchEnabled}
			<div class="flex items-center gap-2.5 px-5 mt-3.5 mb-1.5">
				<span class="i-lucide-search size-4 block shrink-0"></span>

				<input
					id="model-search-input"
					bind:value={searchValue}
					class="w-full text-sm bg-transparent outline-hidden"
					placeholder={searchPlaceholder}
					autocomplete="off"
					onkeydown={(e) => {
						if (e.code === 'Enter' && filteredItems.length > 0) {
							value = filteredItems[selectedModelIdx].value;
							show = false;
							return; // dont need to scroll on selection
						} else if (e.code === 'ArrowDown') {
							selectedModelIdx = Math.min(selectedModelIdx + 1, filteredItems.length - 1);
						} else if (e.code === 'ArrowUp') {
							selectedModelIdx = Math.max(selectedModelIdx - 1, 0);
						} else {
							// if the user types something, reset to the top selection.
							selectedModelIdx = 0;
						}

						const item = document.querySelector(`[data-arrow-selected="true"]`);
						item?.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
					}}
				/>
			</div>
		{/if}

		<div class="px-3 max-h-64 overflow-y-auto scrollbar-hidden group relative">
			{#if items.find((item) => item.model?.owned_by === 'ollama') && items.find((item) => item.model?.owned_by === 'openai') && items.filter((item) => !(item.model?.info?.meta?.hidden ?? false)).length > 0}
				<div
					class=" flex w-full sticky top-0 z-10 bg-surface-raised overflow-x-auto scrollbar-none"
					onwheel={(e) => {
						if (e.deltaY !== 0) {
							e.preventDefault();
							e.currentTarget.scrollLeft += e.deltaY;
						}
					}}
				>
					<div
						class="flex gap-1 w-fit text-center text-sm font-medium rounded-full bg-transparent px-1.5 pb-0.5"
					>
						{#each [{ type: '', label: 'All' }, { type: 'ollama', label: 'Local' }, { type: 'openai', label: 'External' }] as tab (tab.type)}
							<button
								class="min-w-fit outline-none p-1.5 {selectedConnectionType === tab.type
									? ''
									: 'text-content-subtle hover:text-content'} capitalize"
								onclick={() => {
									selectedConnectionType = tab.type;
								}}
							>
								{$i18n.t(tab.label)}
							</button>
						{/each}
					</div>
				</div>
			{/if}

			{#each filteredItems as item, index (item.value)}
				<button
					aria-label="model-item"
					class="flex w-full text-left font-medium line-clamp-1 select-none items-center rounded-button py-2 pl-3 pr-1.5 text-sm text-content outline-hidden hover:bg-surface-hover rounded-lg cursor-pointer data-highlighted:bg-muted {index ===
					selectedModelIdx
						? 'bg-surface-hover group-hover:bg-transparent'
						: ''}"
					data-arrow-selected={index === selectedModelIdx}
					data-value={item.value}
					onclick={() => {
						value = item.value;
						selectedModelIdx = index;

						show = false;
					}}
				>
					<div class="flex flex-col">
						<div class="flex items-center gap-2">
							<div class="flex items-center min-w-fit">
								<div class="line-clamp-1">
									<div class="flex items-center min-w-fit">
										<Tooltip
											content={$user?.role === 'admin' ? (item?.value ?? '') : ''}
											placement="top-start"
										>
											<img
												src={item.model?.info?.meta?.profile_image_url ?? DEFAULT_MODEL_IMAGE_URL}
												alt="Model"
												class="rounded-full size-5 flex items-center mr-2"
											/>

											<div class="flex items-center line-clamp-1">
												<div class="line-clamp-1">
													{item.label}
												</div>

												{#if item.model.owned_by === 'ollama' && (item.model.ollama?.details?.parameter_size ?? '') !== ''}
													<div class="flex ml-1 items-center translate-y-[0.5px]">
														<Tooltip
															content={`${
																item.model.ollama?.details?.quantization_level
																	? item.model.ollama?.details?.quantization_level + ' '
																	: ''
															}${
																item.model.ollama?.size
																	? `(${(item.model.ollama?.size / 1024 ** 3).toFixed(1)}GB)`
																	: ''
															}`}
															className="self-end"
														>
															<span class=" text-xs font-medium text-content-muted line-clamp-1"
																>{item.model.ollama?.details?.parameter_size ?? ''}</span
															>
														</Tooltip>
													</div>
												{/if}
											</div>
										</Tooltip>
									</div>
								</div>
							</div>

							{#if item.model.owned_by === 'openai'}
								<Tooltip content={`${'External'}`}>
									<span class="i-lucide-link size-3 block translate-y-[1px]"></span>
								</Tooltip>
							{/if}

							{#if item.model?.info?.meta?.description}
								<Tooltip
									content={`${render(
										sanitizeResponseContent(item.model?.info?.meta?.description).replaceAll(
											'\n',
											'<br>'
										)
									)}`}
								>
									<span class="i-lucide-info size-4 block translate-y-[1px]"></span>
								</Tooltip>
							{/if}
						</div>
					</div>

					<!-- llens(#70): ユーザー既定モデル(新規チャットの初期選択)の bookmark。
					     行クリック(モデル選択)とは独立に効かせる(stopPropagation)。
					     既定 = bookmark-check を実色、それ以外は薄い bookmark。
					     選択中の ✓ は撤去(トリガーに選択中モデル名が出ており冗長)。 -->
					<Tooltip
						className="ml-auto mr-2 md:mr-0 flex"
						content={userDefaultModelId === item.value
							? $i18n.t('Unset default model')
							: $i18n.t('Set as default')}
					>
						<span
							class="block p-0.5 rounded-sm {userDefaultModelId === item.value
								? 'text-content'
								: 'text-content-subtle/50 hover:text-content-muted'}"
							role="button"
							tabindex="-1"
							onclick={(e) => {
								e.stopPropagation();
								toggleDefaultModel(item.value);
							}}
							onkeydown={(e) => {
								if (e.key === 'Enter' || e.key === ' ') {
									e.preventDefault();
									e.stopPropagation();
									toggleDefaultModel(item.value);
								}
							}}
						>
							{#if userDefaultModelId === item.value}
								<span class="i-lucide-bookmark-check size-4 block"></span>
							{:else}
								<span class="i-lucide-bookmark size-4 block"></span>
							{/if}
						</span>
					</Tooltip>
				</button>
			{:else}
				<div class="">
					<div class="block px-3 py-2 text-sm text-content">
						{$i18n.t('No results found')}
					</div>
				</div>
			{/each}

			{#if !(searchValue.trim() in $MODEL_DOWNLOAD_POOL) && searchValue && ollamaVersion && $user?.role === 'admin'}
				<Tooltip
					content={$i18n.t(`Pull "{{searchValue}}" from Ollama.com`, {
						searchValue: searchValue
					})}
					placement="top-start"
				>
					<button
						class="flex w-full font-medium line-clamp-1 select-none items-center rounded-button py-2 pl-3 pr-1.5 text-sm text-content outline-hidden hover:bg-surface-hover rounded-lg cursor-pointer data-highlighted:bg-muted"
						onclick={() => {
							pullModelHandler();
						}}
					>
						<div class=" truncate">
							{$i18n.t(`Pull "{{searchValue}}" from Ollama.com`, { searchValue: searchValue })}
						</div>
					</button>
				</Tooltip>
			{/if}

			{#each Object.keys($MODEL_DOWNLOAD_POOL) as model (model)}
				<div
					class="flex w-full justify-between font-medium select-none rounded-button py-2 pl-3 pr-1.5 text-sm text-content outline-hidden rounded-lg cursor-pointer data-highlighted:bg-muted"
				>
					<div class="flex">
						<div class="-ml-2 mr-2.5 translate-y-0.5">
							<span class="i-lucide-loader-circle size-4 block animate-spin"></span>
						</div>

						<div class="flex flex-col self-start">
							<div class="flex gap-1">
								<div class="line-clamp-1">
									{$i18n.t('Downloading "{{modelName}}"', { modelName: model })}
								</div>

								<div class="shrink-0">
									{'pullProgress' in $MODEL_DOWNLOAD_POOL[model]
										? `(${$MODEL_DOWNLOAD_POOL[model].pullProgress}%)`
										: ''}
								</div>
							</div>

							{#if 'digest' in $MODEL_DOWNLOAD_POOL[model] && $MODEL_DOWNLOAD_POOL[model].digest}
								<div class="-mt-1 h-fit text-[0.7rem] text-content-subtle line-clamp-1">
									{$MODEL_DOWNLOAD_POOL[model].digest}
								</div>
							{/if}
						</div>
					</div>

					<div class="mr-2 ml-1 translate-y-0.5">
						<Tooltip content={$i18n.t('Cancel')}>
							<button
								class="text-content"
								aria-label={$i18n.t('Cancel')}
								onclick={() => {
									cancelModelPullHandler(model);
								}}
							>
								<span class="i-lucide-x size-4 block"></span>
							</button>
						</Tooltip>
					</div>
				</div>
			{/each}
		</div>

		{#if filteredItems.length === 0}
			<div class="mb-3"></div>
		{/if}

		<div class="hidden w-[42rem]"></div>
		<div class="hidden w-[32rem]"></div>
	</DropdownMenu.Content>
</DropdownMenu.Root>

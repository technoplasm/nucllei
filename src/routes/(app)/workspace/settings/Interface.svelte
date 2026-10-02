<script lang="ts">
	import { v4 as uuidv4 } from 'uuid';

	import { getBackendConfig, getTaskConfig, updateTaskConfig } from '$lib/apis';
	import { config, models } from '$lib/stores';
	import { onMount, getContext } from 'svelte';

	import { banners as _banners } from '$lib/stores';
	import type { Banner } from '$lib/types';

	import { getBanners, setBanners } from '$lib/apis/configs';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import Textarea from '$lib/components/common/Textarea.svelte';

	const i18n = getContext('i18n');

	let { onSave = () => {} }: { onSave?: () => void } = $props();

	let taskConfig = $state<any>({
		TASK_MODEL: '',
		TASK_MODEL_EXTERNAL: '',
		ENABLE_TITLE_GENERATION: true,
		TITLE_GENERATION_PROMPT_TEMPLATE: ''
	});

	let banners = $state<Banner[]>([]);

	const updateInterfaceHandler = async () => {
		taskConfig = await updateTaskConfig(taskConfig);

		await updateBanners();

		await config.set(await getBackendConfig());
	};

	onMount(async () => {
		taskConfig = await getTaskConfig();

		banners = await getBanners();
	});

	const updateBanners = async () => {
		_banners.set(await setBanners(banners));
	};
</script>

{#if taskConfig}
	<form
		class="flex flex-col h-full justify-between space-y-3 text-sm"
		onsubmit={(e) => {
			e.preventDefault();
			updateInterfaceHandler();
			onSave();
		}}
	>
		<div class="  overflow-y-scroll h-full pr-1.5">
			<div class="mb-3.5">
				<div class=" mb-2.5 text-base font-medium">{$i18n.t('Tasks')}</div>

				<hr class=" border-line my-2" />

				<div class=" mb-1 font-medium flex items-center">
					<div class=" text-xs mr-1">{$i18n.t('Set Task Model')}</div>
					<Tooltip
						content={$i18n.t(
							'A task model is used when performing tasks such as generating titles for chats and web search queries'
						)}
					>
						<span class="i-lucide-info size-3.5 block"></span>
					</Tooltip>
				</div>

				<div class=" mb-2.5 flex w-full gap-2">
					<div class="flex-1">
						<div class=" text-xs mb-1">{$i18n.t('Local Models')}</div>
						<select
							class="w-full rounded-xs py-2 px-4 text-sm text-content bg-surface-form hover:bg-surface-hover outline-hidden"
							bind:value={taskConfig.TASK_MODEL}
							placeholder={$i18n.t('Select a model')}
						>
							<option value="" selected>{$i18n.t('Current Model')}</option>
							{#each $models.filter((m) => m.owned_by === 'ollama') as model}
								<option value={model.id}>
									{model.name}
								</option>
							{/each}
						</select>
					</div>

					<div class="flex-1">
						<div class=" text-xs mb-1">{$i18n.t('External Models')}</div>
						<select
							class="w-full rounded-xs py-2 px-4 text-sm text-content bg-surface-form hover:bg-surface-hover outline-hidden"
							bind:value={taskConfig.TASK_MODEL_EXTERNAL}
							placeholder={$i18n.t('Select a model')}
						>
							<option value="" selected>{$i18n.t('Current Model')}</option>
							{#each $models as model}
								<option value={model.id}>
									{model.name}
								</option>
							{/each}
						</select>
					</div>
				</div>

				<div class="mb-2.5 flex w-full items-center justify-between">
					<div class=" self-center text-xs font-medium">
						{$i18n.t('Title Generation')}
					</div>

					<Switch bind:state={taskConfig.ENABLE_TITLE_GENERATION} />
				</div>

				{#if taskConfig.ENABLE_TITLE_GENERATION}
					<div class="mb-2.5">
						<div class=" mb-1 text-xs font-medium">{$i18n.t('Title Generation Prompt')}</div>

						<Tooltip
							content={$i18n.t('Leave empty to use the default prompt, or enter a custom prompt')}
							placement="top-start"
						>
							<Textarea
								bind:value={taskConfig.TITLE_GENERATION_PROMPT_TEMPLATE}
								placeholder={$i18n.t(
									'Leave empty to use the default prompt, or enter a custom prompt'
								)}
							/>
						</Tooltip>
					</div>
				{/if}
			</div>

			<div class="mb-3.5">
				<div class=" mb-2.5 text-base font-medium">{$i18n.t('UI')}</div>

				<hr class=" border-line my-2" />

				<div class="  {banners.length > 0 ? ' mb-3' : ''}">
					<div class="mb-2.5 flex w-full justify-between">
						<div class=" self-center text-sm font-semibold">
							{$i18n.t('Banners')}
						</div>

						<button
							class="p-1 px-3 text-xs flex rounded-sm"
							type="button"
							aria-label={$i18n.t('Add')}
							onclick={() => {
								if (banners.length === 0 || banners.at(-1)?.content !== '') {
									banners = [
										...banners,
										{
											id: uuidv4(),
											type: '',
											title: '',
											content: '',
											dismissible: true,
											timestamp: Math.floor(Date.now() / 1000)
										}
									];
								}
							}}
						>
							<span class="i-lucide-plus size-4 block"></span>
						</button>
					</div>

					<div class=" flex flex-col space-y-1">
						{#each banners as banner, bannerIdx (banner.id)}
							<div class=" flex justify-between">
								<div class="flex flex-row flex-1 border rounded-xl border-line">
									<select
										class="w-fit capitalize rounded-xl py-2 px-4 text-xs bg-transparent outline-hidden"
										bind:value={banner.type}
										required
									>
										{#if banner.type == ''}
											<option value="" selected disabled class="text-content"
												>{$i18n.t('Type')}</option
											>
										{/if}
										<option value="info" class="text-content">{$i18n.t('Info')}</option>
										<option value="warning" class="text-content">{$i18n.t('Warning')}</option>
										<option value="error" class="text-content">{$i18n.t('Error')}</option>
										<option value="success" class="text-content">{$i18n.t('Success')}</option>
									</select>

									<input
										class="pr-5 py-1.5 text-xs w-full bg-transparent outline-hidden"
										placeholder={$i18n.t('Content')}
										bind:value={banner.content}
									/>

									<div class="relative top-1.5 -left-2">
										<Tooltip content={$i18n.t('Dismissible')} className="flex h-fit items-center">
											<Switch bind:state={banner.dismissible} />
										</Tooltip>
									</div>
								</div>

								<button
									class="px-2"
									type="button"
									aria-label={$i18n.t('Delete')}
									onclick={() => {
										banners.splice(bannerIdx, 1);
									}}
								>
									<span class="i-lucide-x size-4 block"></span>
								</button>
							</div>
						{/each}
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
{/if}

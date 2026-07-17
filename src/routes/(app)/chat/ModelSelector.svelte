<script lang="ts">
	import { models, user } from '$lib/stores';
	import { getContext } from 'svelte';
	import Selector from './ModelSelector/Selector.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n = getContext('i18n');

	let {
		selectedModels = $bindable(['']),
		disabled = false
	}: { selectedModels?: string[]; disabled?: boolean } = $props();

	// 存在しないモデル id は '' に落とす(モデル一覧の変化・URL 指定への追従)
	$effect(() => {
		if (selectedModels.length > 0 && $models.length > 0) {
			const next = selectedModels.map((model) =>
				$models.some((m) => m.id === model) ? model : ''
			);
			if (next.some((model, idx) => model !== selectedModels[idx])) {
				selectedModels = next;
			}
		}
	});
</script>

<div class="flex flex-col w-full items-start">
	{#each selectedModels as _, selectedModelIdx (selectedModelIdx)}
		<div class="flex w-full max-w-fit">
			<div class="overflow-hidden w-full">
				<div class="mr-1 max-w-full">
					<Selector
						id={`${selectedModelIdx}`}
						placeholder={$i18n.t('Select a model')}
						items={$models.map((model) => ({
							value: model.id,
							label: model.name,
							model: model
						}))}
						bind:value={selectedModels[selectedModelIdx]}
					/>
				</div>
			</div>

			{#if $user?.role === 'admin' || ($user?.permissions?.chat?.multiple_models ?? true)}
				<div class="self-center mx-1 -translate-y-[0.5px]">
					{#if selectedModelIdx === 0}
						<Tooltip content={$i18n.t('Add Model')}>
							<button
								class="disabled:text-content-subtle"
								{disabled}
								onclick={() => {
									selectedModels = [...selectedModels, ''];
								}}
								aria-label={$i18n.t('Add Model')}
							>
								<span class="i-lucide-plus size-3.5 block"></span>
							</button>
						</Tooltip>
					{:else}
						<Tooltip content={$i18n.t('Remove Model')}>
							<button
								class="disabled:text-content-subtle"
								{disabled}
								onclick={() => {
									selectedModels = selectedModels.filter((_, idx) => idx !== selectedModelIdx);
								}}
								aria-label={$i18n.t('Remove Model')}
							>
								<span class="i-lucide-minus size-3 block"></span>
							</button>
						</Tooltip>
					{/if}
				</div>
			{/if}
		</div>
	{/each}
</div>

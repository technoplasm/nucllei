<script lang="ts">
	import Sortable from 'sortablejs';
	import { getContext } from 'svelte';

	import { models } from '$lib/stores';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n = getContext('i18n');

	let { modelIds = $bindable([]) }: { modelIds?: string[] } = $props();

	let modelListElement = $state<HTMLElement>();

	// drag(sortablejs)で DOM の並びが変わったら id 配列へ書き戻す
	const positionChangeHandler = () => {
		if (!modelListElement) return;
		modelIds = Array.from(modelListElement.children).map((child) =>
			child.id.replace('model-item-', '')
		);
	};

	$effect(() => {
		void modelIds; // 並び替え(ソートボタン等)後の再描画にも Sortable を張り直す
		const el = modelListElement;
		if (!el) return;

		const sortable = Sortable.create(el, {
			animation: 150,
			handle: '.item-handle',
			onUpdate: () => {
				positionChangeHandler();
			}
		});
		return () => sortable.destroy();
	});
</script>

{#if modelIds.length > 0}
	<div class="flex flex-col -translate-x-1" bind:this={modelListElement}>
		{#each modelIds as modelId (modelId)}
			<div class=" flex gap-2 w-full justify-between items-center" id="model-item-{modelId}">
				<Tooltip content={modelId} placement="top-start">
					<div class="flex items-center gap-1">
						<span class="i-lucide-grip-vertical size-4 block cursor-move item-handle"></span>

						<div class=" text-sm flex-1 py-1 rounded-lg">
							{($models ?? []).find((model) => model.id === modelId)?.name ?? modelId}
						</div>
					</div>
				</Tooltip>
			</div>
		{/each}
	</div>
{:else}
	<div class="text-content-subtle text-xs text-center py-2">
		{$i18n.t('No models found')}
	</div>
{/if}

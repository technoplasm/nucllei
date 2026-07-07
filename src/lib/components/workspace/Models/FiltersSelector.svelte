<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n = getContext('i18n');

	export let filters = [];
	export let selectedFilterIds = [];

	let _filters = {};

	onMount(() => {
		_filters = filters.reduce((acc, filter) => {
			acc[filter.id] = {
				...filter,
				selected: selectedFilterIds.includes(filter.id)
			};

			return acc;
		}, {});
	});
</script>

<div>
	<div class="flex w-full justify-between mb-1">
		<div class=" self-center text-sm font-semibold">{$i18n.t('Filters')}</div>
	</div>

	<!-- TODO: Filer order matters -->
	<div class="flex flex-col">
		{#if filters.length > 0}
			<div class=" flex items-center mt-2 flex-wrap">
				{#each Object.keys(_filters) as filter, filterIdx}
					<div class=" flex items-center gap-2 mr-3">
						<div class="self-center flex items-center">
							<!-- llens(#46): is_global 撤去 → 強制 checked+disabled をやめ、モデル別に自由トグル -->
							<Checkbox
								state={_filters[filter].selected ? 'checked' : 'unchecked'}
								on:change={(e) => {
									_filters[filter].selected = e.detail === 'checked';
									selectedFilterIds = Object.keys(_filters).filter((t) => _filters[t].selected);
								}}
							/>
						</div>

						<div class=" py-0.5 text-sm w-full capitalize font-medium">
							<Tooltip content={_filters[filter].description}>
								{_filters[filter].name}
							</Tooltip>
						</div>
					</div>
				{/each}
			</div>
		{:else}
			<div class="text-xs text-gray-400 dark:text-gray-500 mt-1">{$i18n.t('None')}</div>
		{/if}
	</div>
</div>

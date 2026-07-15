<script lang="ts">
	// llens(#70): FiltersSelector / ActionsSelector を統合。ToolsSelector と同じ様式で
	// 種別(Filter / Action)の小見出しで分ける。保存先は従来どおり filterIds / actionIds の 2 本。
	import { getContext, onMount } from 'svelte';
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n = getContext('i18n');

	let {
		functions = [],
		selectedFilterIds = $bindable([]),
		selectedActionIds = $bindable([])
	}: {
		functions?: any[];
		selectedFilterIds?: string[];
		selectedActionIds?: string[];
	} = $props();

	let _functions = $state<Record<string, any>>({});

	const filterIds = $derived(
		Object.keys(_functions).filter((id) => _functions[id].type === 'filter')
	);
	const actionIds = $derived(
		Object.keys(_functions).filter((id) => _functions[id].type === 'action')
	);

	onMount(() => {
		_functions = functions.reduce((acc: Record<string, any>, func) => {
			acc[func.id] = {
				...func,
				selected:
					func.type === 'filter'
						? selectedFilterIds.includes(func.id)
						: selectedActionIds.includes(func.id)
			};
			return acc;
		}, {});
	});

	const commit = () => {
		selectedFilterIds = Object.keys(_functions).filter(
			(id) => _functions[id].type === 'filter' && _functions[id].selected
		);
		selectedActionIds = Object.keys(_functions).filter(
			(id) => _functions[id].type === 'action' && _functions[id].selected
		);
	};
</script>

<div>
	<div class="flex w-full justify-between mb-1">
		<div class=" self-center text-md font-semibold">{$i18n.t('Functions')}</div>
	</div>

	<div class="flex flex-col">
		{#if filterIds.length === 0 && actionIds.length === 0}
			<div class="text-xs text-content-subtle mt-1">{$i18n.t('None')}</div>
		{/if}

		<!-- 空のセクション(type)は見出しごと出さない。項目があれば小見出しは常時表示 -->
		{#each [{ label: 'Filter', ids: filterIds }, { label: 'Action', ids: actionIds }] as section (section.label)}
			{#if section.ids.length > 0}
				<div class="text-xs text-content-muted mt-2">{$i18n.t(section.label)}</div>
				<div class="flex items-center mt-1 flex-wrap">
					{#each section.ids as funcId (funcId)}
						<!-- llens(#49): label で行クリックトグル -->
						<label class=" flex items-center gap-2 mr-3 cursor-pointer">
							<span class="self-center flex items-center">
								<Checkbox
									state={_functions[funcId].selected ? 'checked' : 'unchecked'}
									on:change={(e) => {
										_functions[funcId].selected = e.detail === 'checked';
										commit();
									}}
								/>
							</span>

							<div class=" py-0.5 text-sm w-full capitalize font-medium">
								<Tooltip content={_functions[funcId].description}>
									{_functions[funcId].name}
								</Tooltip>
							</div>
						</label>
					{/each}
				</div>
			{/if}
		{/each}
	</div>
</div>

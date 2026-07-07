<script lang="ts">
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { getContext, onMount } from 'svelte';

	export let tools = [];

	let _tools = {};

	export let selectedToolIds = [];

	const i18n = getContext('i18n');

	// llens(#42): 組み込み(repo 定義・起動時 sync)とカスタム(DB 定義)を分けて見せる。
	// 仕組みは同列(どちらも toolIds に入るだけ)で、表示だけの区分
	$: builtinIds = Object.keys(_tools).filter((id) => _tools[id].meta?.builtin);
	$: customIds = Object.keys(_tools).filter((id) => !_tools[id].meta?.builtin);

	onMount(() => {
		_tools = tools.reduce((acc, tool) => {
			acc[tool.id] = {
				...tool,
				selected: selectedToolIds.includes(tool.id)
			};

			return acc;
		}, {});
	});
</script>

<div>
	<div class="flex w-full justify-between mb-1">
		<div class=" self-center text-sm font-semibold">{$i18n.t('Tools')}</div>
	</div>

	<div class="flex flex-col">
		{#if builtinIds.length === 0 && customIds.length === 0}
			<div class="text-xs text-gray-400 dark:text-gray-500 mt-1">{$i18n.t('None')}</div>
		{/if}

		{#if builtinIds.length > 0}
			<div class="text-xs text-gray-500 mt-2">{$i18n.t('組み込み')}</div>
			<div class=" flex items-center mt-1 flex-wrap">
				{#each builtinIds as tool}
					<div class=" flex items-center gap-2 mr-3">
						<div class="self-center flex items-center">
							<Checkbox
								state={_tools[tool].selected ? 'checked' : 'unchecked'}
								on:change={(e) => {
									_tools[tool].selected = e.detail === 'checked';
									selectedToolIds = Object.keys(_tools).filter((t) => _tools[t].selected);
								}}
							/>
						</div>

						<Tooltip content={_tools[tool].description ?? ''} placement="top-start">
							<div class=" py-0.5 text-sm w-full font-medium">
								{_tools[tool].name}
							</div>
						</Tooltip>
					</div>
				{/each}
			</div>
		{/if}

		{#if customIds.length > 0}
			{#if builtinIds.length > 0}
				<div class="text-xs text-gray-500 mt-2">{$i18n.t('カスタムツール')}</div>
			{/if}
			<div class=" flex items-center mt-1 flex-wrap">
				{#each customIds as tool}
					<div class=" flex items-center gap-2 mr-3">
						<div class="self-center flex items-center">
							<Checkbox
								state={_tools[tool].selected ? 'checked' : 'unchecked'}
								on:change={(e) => {
									_tools[tool].selected = e.detail === 'checked';
									selectedToolIds = Object.keys(_tools).filter((t) => _tools[t].selected);
								}}
							/>
						</div>

						<div class=" py-0.5 text-sm w-full capitalize font-medium">
							{_tools[tool].name}
						</div>
					</div>
				{/each}
			</div>
		{/if}
	</div>
</div>

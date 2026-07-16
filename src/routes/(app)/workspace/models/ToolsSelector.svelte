<script lang="ts">
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { getContext, onMount } from 'svelte';

	const i18n = getContext('i18n');

	let {
		tools = [],
		selectedToolIds = $bindable([])
	}: {
		tools?: any[];
		selectedToolIds?: string[];
	} = $props();

	let _tools = $state<Record<string, any>>({});

	// llens(#42): 組み込み(repo 定義・起動時 sync)とカスタム(DB 定義)を分けて見せる。
	// 仕組みは同列(どちらも toolIds に入るだけ)で、表示だけの区分
	const builtinIds = $derived(Object.keys(_tools).filter((id) => _tools[id].meta?.builtin));
	const customIds = $derived(Object.keys(_tools).filter((id) => !_tools[id].meta?.builtin));

	onMount(() => {
		_tools = tools.reduce((acc: Record<string, any>, tool) => {
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
		<div class=" self-center text-md font-semibold">{$i18n.t('Tools')}</div>
	</div>

	<div class="flex flex-col">
		{#if builtinIds.length === 0 && customIds.length === 0}
			<div class="text-xs text-content-subtle mt-1">{$i18n.t('None')}</div>
		{/if}

		{#each [{ label: $i18n.t('Builtin'), ids: builtinIds }, { label: $i18n.t('Custom'), ids: customIds }] as section}
			{#if section.ids.length > 0}
				{#if builtinIds.length > 0 && customIds.length > 0}
					<div class="text-xs text-content-muted mt-2">{section.label}</div>
				{/if}
				<div class="flex flex-col mt-1">
					{#each section.ids as tool}
						<!-- llens(#49): skill 表示に倣い、行全体をラベルにして中の関数(specs)まで見せる。
						 label で包むと中の Checkbox(button)クリックが行全体に効く -->
						<label class="flex items-start gap-2 mr-3 py-0.5 cursor-pointer">
							<span class="mt-0.5 shrink-0">
								<Checkbox
									state={_tools[tool].selected ? 'checked' : 'unchecked'}
									onChange={(state) => {
										_tools[tool].selected = state === 'checked';
										selectedToolIds = Object.keys(_tools).filter((t) => _tools[t].selected);
									}}
								/>
							</span>

							<div class="flex flex-col min-w-0">
								<Tooltip content={_tools[tool].description ?? ''} placement="top-start">
									<span class="text-sm font-medium">{_tools[tool].name}</span>
								</Tooltip>

								{#if (_tools[tool].specs ?? []).length > 0}
									<div class="flex flex-wrap gap-x-2 gap-y-0.5 text-xs text-content-muted mt-0.5">
										{#each _tools[tool].specs as spec}
											<Tooltip content={spec.description ?? ''} placement="top-start">
												<span class="font-mono">{spec.name}</span>
											</Tooltip>
										{/each}
									</div>
								{/if}
							</div>
						</label>
					{/each}
				</div>
			{/if}
		{/each}
	</div>
</div>

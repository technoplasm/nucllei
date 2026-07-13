<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n = getContext('i18n');

	export let actions = [];
	export let selectedActionIds = [];

	let _actions = {};

	onMount(() => {
		_actions = actions.reduce((acc, action) => {
			acc[action.id] = {
				...action,
				selected: selectedActionIds.includes(action.id)
			};

			return acc;
		}, {});
	});
</script>

<div>
	<div class="flex w-full justify-between mb-1">
		<div class=" self-center text-sm font-semibold">{$i18n.t('Actions')}</div>
	</div>

	<div class="flex flex-col">
		{#if actions.length > 0}
			<div class=" flex items-center mt-2 flex-wrap">
				{#each Object.keys(_actions) as action}
					<!-- llens(#49): label で行クリックトグル -->
					<label class=" flex items-center gap-2 mr-3 cursor-pointer">
						<span class="self-center flex items-center">
							<Checkbox
								state={_actions[action].selected ? 'checked' : 'unchecked'}
								on:change={(e) => {
									_actions[action].selected = e.detail === 'checked';
									selectedActionIds = Object.keys(_actions).filter((t) => _actions[t].selected);
								}}
							/>
						</span>

						<div class=" py-0.5 text-sm w-full capitalize font-medium">
							<Tooltip content={_actions[action].description}>
								{_actions[action].name}
							</Tooltip>
						</div>
					</label>
				{/each}
			</div>
		{:else}
			<div class="text-xs text-gray-400 dark:text-gray-500 mt-1">{$i18n.t('None')}</div>
		{/if}
	</div>
</div>

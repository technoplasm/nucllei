<script lang="ts">
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { getContext, onMount } from 'svelte';

	export let skills = [];

	let _skills = {};

	export let selectedSkillIds = [];

	const i18n = getContext('i18n');

	onMount(() => {
		_skills = skills.reduce((acc, skill) => {
			acc[skill.command] = {
				...skill,
				selected: selectedSkillIds.includes(skill.command)
			};

			return acc;
		}, {});
	});
</script>

<div>
	<div class="flex w-full justify-between mb-1">
		<div class=" self-center text-sm font-semibold">{$i18n.t('Skills')}</div>
	</div>

	<div class=" text-xs dark:text-gray-500">
		{$i18n.t('To select skills here, add them to the "Skills" workspace first.')}
	</div>

	<div class="flex flex-col">
		{#if skills.length > 0}
			<div class=" flex flex-col mt-2">
				{#each Object.keys(_skills) as skill}
					<div class=" flex items-center gap-2 mr-3">
						<div class="self-center flex items-center">
							<Checkbox
								state={_skills[skill].selected ? 'checked' : 'unchecked'}
								on:change={(e) => {
									_skills[skill].selected = e.detail === 'checked';
									selectedSkillIds = Object.keys(_skills).filter((s) => _skills[s].selected);
								}}
							/>
						</div>

						<div class=" py-0.5 text-sm w-full font-medium flex items-center gap-1.5">
							<span class="text-gray-500">/{_skills[skill].command}</span>
							<span>— {_skills[skill].title}</span>

							{#if !(_skills[skill].description ?? '').trim()}
								<Tooltip
									content={$i18n.t('description が無いためモデルには公開されません')}
									placement="top-start"
								>
									<span class="text-xs text-yellow-600 dark:text-yellow-500"
										>{$i18n.t('(no description)')}</span
									>
								</Tooltip>
							{/if}
						</div>
					</div>
				{/each}
			</div>
		{/if}
	</div>
</div>

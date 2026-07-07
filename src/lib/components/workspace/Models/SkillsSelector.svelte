<script lang="ts">
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { getContext, onMount } from 'svelte';

	export let skills = [];

	let _skills = {};

	export let selectedSkillIds = [];
	// llens(#42): 機能「スキル」OFF のとき。紐づけ設定は隠さず、発火しない旨だけ示す
	export let disabled = false;

	const i18n = getContext('i18n');

	onMount(() => {
		// キーは skill.id(meta.skillIds はサーバが Skills.get_skill_by_id で引く)。
		// 旧実装は command をキーにしており、保存された id と突き合わず
		// 「チェックが復元されない・保存すると command が入る」バグだった(#42 で修正)
		_skills = skills.reduce((acc, skill) => {
			acc[skill.id] = {
				...skill,
				selected: selectedSkillIds.includes(skill.id)
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

	{#if disabled}
		<div class="text-xs text-yellow-600 dark:text-yellow-500 mt-1">
			{$i18n.t('スキル機能が無効のため、このモデルでは発火しません(紐づけ設定は保持されます)')}
		</div>
	{/if}

	<div class="flex flex-col" class:opacity-50={disabled}>
		{#if skills.length > 0}
			<div class=" flex flex-col mt-2">
				{#each Object.keys(_skills) as skillId}
					<div class=" flex items-center gap-2 mr-3">
						<div class="self-center flex items-center">
							<Checkbox
								state={_skills[skillId].selected ? 'checked' : 'unchecked'}
								on:change={(e) => {
									_skills[skillId].selected = e.detail === 'checked';
									selectedSkillIds = Object.keys(_skills).filter((id) => _skills[id].selected);
								}}
							/>
						</div>

						<div class=" py-0.5 text-sm w-full font-medium flex items-center gap-1.5">
							<span class="text-gray-500">/{_skills[skillId].id}</span>
							<span>— {_skills[skillId].name}</span>

							{#if !(_skills[skillId].description ?? '').trim()}
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

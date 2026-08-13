<script lang="ts">
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import { getContext, onMount } from 'svelte';

	const i18n = getContext('i18n');

	// llens(#107): run_subagent の委任先 preset の紐づけ(SkillsSelector 同型)。
	// 一覧の供給元は workspace の getModels()(/api/v1/models/ = preset のみ)。
	// 編集中の preset 自身も一覧に出す(選択可 — 実行時は depth 上限が守る)。
	let {
		presets = [],
		selectedPresetIds = $bindable([]),
		// llens(#107): 機能「サブエージェント」OFF のとき。紐づけ設定は隠さず、発火しない旨だけ示す
		disabled = false
	}: {
		presets?: any[];
		selectedPresetIds?: string[];
		disabled?: boolean;
	} = $props();

	let _presets = $state<Record<string, any>>({});

	// 表示名昇順で並べる(同名は id 昇順)
	const sortedPresetIds = $derived(
		Object.keys(_presets).sort(
			(a, b) => _presets[a].name.localeCompare(_presets[b].name) || a.localeCompare(b)
		)
	);

	onMount(() => {
		// キーは model.id(meta.subagentPresetIds はサーバが Models.get_model_by_id で引く)。
		// SkillsSelector の教訓(#42: id 以外をキーにすると保存値と突き合わず
		// チェックが復元されない)を踏襲し、常に id をキーにする
		_presets = presets.reduce((acc: Record<string, any>, preset) => {
			acc[preset.id] = {
				...preset,
				selected: selectedPresetIds.includes(preset.id)
			};

			return acc;
		}, {});
	});
</script>

<div>
	<div class="flex w-full justify-between">
		<div class=" self-center text-md font-semibold">{$i18n.t('Subagent presets')}</div>
	</div>

	{#if disabled}
		<div class="text-xs text-yellow-600 dark:text-yellow-500 mt-1">
			{$i18n.t(
				'Subagent is disabled for this model, so run_subagent will not be offered (assignments are preserved)'
			)}
		</div>
	{/if}

	<div class="flex flex-col mt-1" class:opacity-50={disabled}>
		{#if presets.length > 0}
			<div class=" flex flex-col">
				{#each sortedPresetIds as presetId}
					<!-- llens(#107 仕様変更 2026-08-14): is_active=False はモデルピッカーに
					     出ないだけで、subagent の spawnable としては選択・実行できる
					     (裏方 preset の想定運用)。バッジで「一覧非表示」であることだけ示す。 -->
					{@const inactive = _presets[presetId].is_active === false}
					<!-- llens(#49): label で包み、行全体クリックでトグル(disabled 時は Checkbox 側が握り潰す) -->
					<label
						class=" flex items-center gap-2 mr-3 {disabled
							? 'cursor-not-allowed'
							: 'cursor-pointer'}"
					>
						<span class="self-center flex items-center">
							<Checkbox
								{disabled}
								state={_presets[presetId].selected ? 'checked' : 'unchecked'}
								onChange={(state) => {
									_presets[presetId].selected = state === 'checked';
									selectedPresetIds = Object.keys(_presets).filter((id) => _presets[id].selected);
								}}
							/>
						</span>

						<div class=" py-0.5 text-sm w-full font-medium flex items-center gap-1.5">
							<span>{_presets[presetId].name}</span>
							<span class="text-content-muted">{_presets[presetId].id}</span>
							{#if inactive}
								<span class="text-xs px-1.5 py-0.5 rounded-full bg-surface-hover text-content-muted"
									>{$i18n.t('Hidden from model list')}</span
								>
							{/if}
						</div>
					</label>
				{/each}
			</div>
		{:else}
			<div class="text-xs text-content-subtle mt-1">{$i18n.t('None')}</div>
		{/if}
	</div>
</div>

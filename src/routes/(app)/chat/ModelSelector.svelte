<script lang="ts">
	import { models } from '$lib/stores';
	import { getContext } from 'svelte';
	import Selector from './ModelSelector/Selector.svelte';
	import { normalizeSelectedModels } from './ModelSelector/selection';

	const i18n = getContext('i18n');

	let {
		selectedModels = $bindable(['']),
		recordedModelId = null
	}: {
		selectedModels?: string[];
		// 開いているチャットに記録されたモデル(新規チャットでは null)。一覧に無くても
		// 選択欄に残す(ModelSelector/selection.ts)
		recordedModelId?: string | null;
	} = $props();

	// llens(#108): マルチモデル送信は撤去 — 選択は常に 1 モデル。
	// 旧 sessionStorage / 旧チャット由来の複数要素は先頭だけ残して切り詰める。
	// 一覧に無いモデル id は '' に落とす(モデル一覧の変化・URL 指定への追従)。
	// ただし開いているチャットに記録されたモデルは残す。
	$effect(() => {
		if (selectedModels.length > 0 && $models.length > 0) {
			const next = normalizeSelectedModels(
				selectedModels,
				$models.map((m) => m.id),
				recordedModelId
			);
			if (
				next.length !== selectedModels.length ||
				next.some((model, idx) => model !== selectedModels[idx])
			) {
				selectedModels = next;
			}
		}
	});
</script>

<div class="flex flex-col w-full items-start">
	<div class="flex w-full max-w-fit">
		<div class="overflow-hidden w-full">
			<div class="mr-1 max-w-full">
				<Selector
					id="0"
					placeholder={$i18n.t('Select a model')}
					items={$models.map((model) => ({
						value: model.id,
						label: model.name,
						model: model
					}))}
					bind:value={selectedModels[0]}
				/>
			</div>
		</div>
	</div>
</div>

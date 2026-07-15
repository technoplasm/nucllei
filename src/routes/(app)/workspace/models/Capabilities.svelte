<script lang="ts">
	import { getContext } from 'svelte';
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { render } from '$lib/utils/markdown-it';

	const i18n = getContext('i18n');

	const helpText: Record<string, string> = {
		vision: $i18n.t('Model accepts image inputs'),
		usage: $i18n.t(
			'Request measured usage (token consumption) via stream_options.include_usage. Turn this off only for OpenAI-compatible APIs that reject it as an unknown parameter'
		),
		citations: $i18n.t('Displays citations in the response')
	};

	// キーは vision / usage / citations(usage はベースモデル依存でキーごと消える)。
	// llens(#70): 機能「スキル」(meta.features.skills、既定 ON)のトグルも先頭に置く。
	// OFF でこのモデルの manifest(load_skill)と /command 発火が両方止まる。
	let {
		capabilities = $bindable({}),
		featureSkills = $bindable(true)
	}: {
		capabilities?: Record<string, boolean | undefined>;
		featureSkills?: boolean;
	} = $props();
</script>

<div>
	<div class="flex w-full justify-between mb-1">
		<div class=" self-center text-sm font-semibold">{$i18n.t('Capabilities')}</div>
	</div>
	<div class="flex">
		<!-- llens(#49): label で行クリックトグル -->
		<label class=" flex items-center gap-2 mr-3 cursor-pointer">
			<span class="flex items-center">
				<Checkbox
					state={featureSkills ? 'checked' : 'unchecked'}
					on:change={(e) => {
						featureSkills = e.detail === 'checked';
					}}
				/>
			</span>

			<div class=" py-0.5 text-sm capitalize">
				<Tooltip content={$i18n.t('Expose the skill list to the model (load_skill) + /command activation')}>
					{$i18n.t('Skills')}
				</Tooltip>
			</div>
		</label>

		{#each Object.keys(capabilities) as capability}
			<!-- llens(#49): label で行クリックトグル -->
			<label class=" flex items-center gap-2 mr-3 cursor-pointer">
				<span class="flex items-center">
					<Checkbox
						state={capabilities[capability] ? 'checked' : 'unchecked'}
						on:change={(e) => {
							capabilities[capability] = e.detail === 'checked';
						}}
					/>
				</span>

				<div class=" py-0.5 text-sm capitalize">
					<Tooltip content={render(helpText[capability])}>
						{$i18n.t(capability)}
					</Tooltip>
				</div>
			</label>
		{/each}
	</div>
</div>

<script lang="ts">
	import { getContext } from 'svelte';
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { render } from '$lib/utils/markdown-it';

	const i18n = getContext('i18n');

	const helpText: Record<string, string> = {
		vision: $i18n.t('Model accepts image inputs'),
		usage: $i18n.t(
			'Sends `stream_options: { include_usage: true }` in the request.\nSupported providers will return token usage information in the response when set.'
		),
		citations: $i18n.t('Displays citations in the response')
	};

	// キーは vision / usage / citations(usage はベースモデル依存でキーごと消える)
	let {
		capabilities = $bindable({})
	}: {
		capabilities?: Record<string, boolean | undefined>;
	} = $props();
</script>

<div>
	<div class="flex w-full justify-between mb-1">
		<div class=" self-center text-sm font-semibold">{$i18n.t('Capabilities')}</div>
	</div>
	<div class="flex">
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

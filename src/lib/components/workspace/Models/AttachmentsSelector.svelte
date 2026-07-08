<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n = getContext('i18n');

	// llens(#52): Attachment 種別の Function を per-model で割り当てる(完全 opt-in)。
	// Attachment は file.data.content を「生成」するルータなので 1 モデルにつき **単一選択**
	// (None or 1つ)。fork は builtin を持たないので、候補は案件層(prism 等)の authored のみ。
	// attachmentIds は list のまま(backend と外形一致)だが要素は 0 or 1。
	export let attachments = [];
	export let selectedAttachmentIds = [];

	let selected = ''; // '' = None、それ以外は attachment id

	onMount(() => {
		selected = selectedAttachmentIds?.[0] ?? '';
	});

	const apply = () => {
		selectedAttachmentIds = selected ? [selected] : [];
	};
</script>

<div>
	<div class="flex w-full justify-between mb-1">
		<div class=" self-center text-sm font-semibold">{$i18n.t('Attachments')}</div>
	</div>

	<div class="flex items-center gap-4 flex-wrap mt-1">
		{#each attachments as attachment}
			<label class="flex items-center gap-1.5 text-sm cursor-pointer">
				<input type="radio" bind:group={selected} value={attachment.id} on:change={apply} />
				<Tooltip content={attachment.description}>
					<span class="capitalize font-medium">{attachment.name}</span>
				</Tooltip>
			</label>
		{/each}

		<label class="flex items-center gap-1.5 text-sm cursor-pointer">
			<input type="radio" bind:group={selected} value="" on:change={apply} />
			<span>None</span>
		</label>
	</div>
</div>

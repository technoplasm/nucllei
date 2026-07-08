<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n = getContext('i18n');

	// llens(#52): Attachment 種別の Function を per-model で割り当てる(完全 opt-in)。
	// Attachment は file.data.content を「生成」するルータなので、1 モデルにつき **単一選択**
	// (0 or 1)。複数走らせると content を上書きし合うため。選択済みを再度押すと「なし」に戻る。
	// attachmentIds は list のまま(backend と外形を揃える)だが要素は 0/1 に制約する。
	export let attachments = [];
	export let selectedAttachmentIds = [];

	let _attachments = {};

	onMount(() => {
		_attachments = attachments.reduce((acc, attachment) => {
			acc[attachment.id] = {
				...attachment,
				selected: selectedAttachmentIds.includes(attachment.id)
			};

			return acc;
		}, {});
	});
</script>

<div>
	<div class="flex w-full justify-between mb-1">
		<div class=" self-center text-sm font-semibold">{$i18n.t('Attachments')}</div>
	</div>

	<div class="flex flex-col">
		{#if attachments.length > 0}
			<div class=" flex items-center mt-2 flex-wrap">
				{#each Object.keys(_attachments) as attachment, attachmentIdx}
					<div class=" flex items-center gap-2 mr-3">
						<div class="self-center flex items-center">
							<Checkbox
								state={_attachments[attachment].selected ? 'checked' : 'unchecked'}
								on:change={(e) => {
									// 単一選択: これを選んだら他は外す。選択済みを押したら解除(なし)。
									const selecting = e.detail === 'checked';
									Object.keys(_attachments).forEach((k) => {
										_attachments[k].selected = selecting && k === attachment;
									});
									_attachments = _attachments;
									selectedAttachmentIds = Object.keys(_attachments).filter(
										(t) => _attachments[t].selected
									);
								}}
							/>
						</div>

						<div class=" py-0.5 text-sm w-full capitalize font-medium">
							<Tooltip content={_attachments[attachment].description}>
								{_attachments[attachment].name}
							</Tooltip>
						</div>
					</div>
				{/each}
			</div>
		{:else}
			<div class="text-xs text-gray-400 dark:text-gray-500 mt-1">{$i18n.t('None')}</div>
		{/if}
	</div>
</div>

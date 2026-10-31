<script lang="ts">
	// preset の編集画面「プロンプト」タブ「会話の前置き」のプレビュー。編集中の前置きで新しいチャットを
	// 始めたとき、記憶の目次とレシピの一覧がどう展開され (自分として)、どこに入るか (system prompt の後、
	// 最初の user 発言の先頭) を見せる。POST /models/preview の context (framed = 実際に入るバイト列) — 実行はしない。
	import { getContext, onMount } from 'svelte';

	import { previewModel, type ModelPreview } from '$lib/apis/models';
	import { getPromptVariables } from '$lib/utils';
	import { estimateTokens } from '$lib/utils/prompt-preview';
	import ExpandedText from '$lib/components/workspace/common/ExpandedText.svelte';
	import PreviewLegend from '$lib/components/workspace/common/PreviewLegend.svelte';

	const i18n = getContext('i18n');

	let {
		model
	}: {
		// 編集中の preset (ModelForm の形。前置きは params.context)
		model: () => object;
	} = $props();

	let preview = $state<ModelPreview | null>(null);
	let loading = $state(false);
	let error = $state('');

	const load = async () => {
		loading = true;
		error = '';
		try {
			// チャットの送信 (send.ts) と同じブラウザ側の変数を渡す
			preview = await previewModel(model(), getPromptVariables());
		} catch (e) {
			preview = null;
			error = `${e}`;
		}
		loading = false;
	};

	onMount(load);

	const size = (text: string) =>
		$i18n.t('{{chars}} chars · ~{{tokens}} tokens', {
			chars: text.length.toLocaleString(),
			tokens: estimateTokens(text).toLocaleString()
		});

	const contextWarnings = $derived((preview?.warnings ?? []).filter((w) => w.where === 'context'));
</script>

{#snippet band(text: string)}
	<div
		class="rounded-lg border border-dashed border-line px-2 py-1 text-xs text-content-subtle select-none"
	>
		{text}
	</div>
{/snippet}

<div class="flex flex-col gap-3 text-sm">
	<div class="flex items-start gap-2">
		<div class="flex-1 min-w-0 text-xs text-content-muted">
			{$i18n.t(
				'Expanded as you, for the first turn of a new chat: how the memory index and the visible recipes expand, and where they go. The preamble is placed after the system prompt, at the head of the first message of the conversation, and is kept unchanged for the rest of the conversation. Nothing is run.'
			)}
		</div>
		<button
			class="shrink-0 flex items-center gap-1 px-2 py-1 rounded-lg text-xs text-content-muted hover:text-content hover:bg-surface-hover"
			type="button"
			disabled={loading}
			onclick={load}
		>
			<span class="i-lucide-refresh-cw size-3.5 block {loading ? 'animate-spin' : ''}"></span>
			{$i18n.t('Refresh')}
		</button>
	</div>

	<PreviewLegend />

	{#if error}
		<div class="text-xs text-red-600 dark:text-red-400">
			{$i18n.t('Could not load the preview: {{error}}', { error })}
		</div>
	{:else if preview}
		<div class="flex flex-col gap-1.5">
			{@render band(
				$i18n.t('1. Tools ({{total}}) → 2. System Prompt', { total: preview.tools.length })
			)}

			<section class="flex flex-col gap-1">
				<div class="flex flex-wrap items-baseline gap-x-2">
					<div class="font-semibold">3. {$i18n.t('Conversation preamble')}</div>
					<div class="text-xs text-content-muted">{size(preview.context.framed)}</div>
				</div>
				<div class="text-xs text-content-muted">
					{$i18n.t(
						'After the system prompt, before the first message (it goes at the head of the first user message).'
					)}
				</div>
				{#each contextWarnings as warning (warning.variable)}
					<div class="text-xs text-red-600 dark:text-red-400">
						{$i18n.t(
							'{{variable}} is not expanded in the conversation preamble (it becomes empty). Write it in the system prompt.',
							{ variable: warning.variable }
						)}
					</div>
				{/each}
				<div class="rounded-lg border border-line px-2 py-1.5">
					{#if preview.context.framed}
						<pre
							class="font-mono text-xs leading-relaxed text-content-muted">{'<conversation-context>'}</pre>
						<ExpandedText
							value={preview.context}
							misplacedTitle={$i18n.t(
								'Not expanded in the conversation preamble — write it in the system prompt.'
							)}
						/>
						<pre
							class="font-mono text-xs leading-relaxed text-content-muted">{'</conversation-context>'}</pre>
					{:else}
						<ExpandedText
							value={preview.context}
							misplacedTitle={$i18n.t(
								'Not expanded in the conversation preamble — write it in the system prompt.'
							)}
						/>
						<div class="mt-1 text-xs text-content-subtle">
							{$i18n.t('Nothing is added to the first message.')}
						</div>
					{/if}
				</div>
			</section>

			<div class="px-2 text-xs text-content-subtle">
				{$i18n.t(
					'(If the message has attachments, their <source> blocks go here, after the preamble.)'
				)}
			</div>
			{@render band($i18n.t('(The first message of the user goes here.)'))}

			<div class="text-xs text-content-muted">
				{$i18n.t(
					'If the memory or the recipes change during the conversation, an update block is added at the head of the next message.'
				)}
			</div>
		</div>
	{:else if loading}
		<div class="flex items-center gap-2 text-xs text-content-muted">
			<span class="i-lucide-loader-circle size-4 block animate-spin"></span>
			{$i18n.t('Loading...')}
		</div>
	{/if}
</div>

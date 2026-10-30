<script lang="ts">
	// preset の編集画面「プロンプト」タブのプレビュー (2026-10-30 ken「実際どのように展開されるか
	// プレビューをタブで付けて」)。編集中の system prompt で新しいチャットを始めたとき、モデルに渡る
	// tools → system prompt を自分として展開して見せる (POST /models/preview — 実際のリクエストと
	// 同じ関数を通る。実行はしない)。利用者ごと・日時ごとに変わる所は色で分ける (prefix cache)。
	import { getContext, onMount } from 'svelte';

	import { user } from '$lib/stores';
	import { previewModel, type ModelPreview } from '$lib/apis/models';
	import { getPromptVariables } from '$lib/utils';
	import { estimateTokens, splitToolDescription, toolVaries } from '$lib/utils/prompt-preview';
	import ExpandedText from '$lib/components/workspace/common/ExpandedText.svelte';
	import PreviewLegend from '$lib/components/workspace/common/PreviewLegend.svelte';

	const i18n = getContext('i18n');

	let {
		model,
		hasFilters = false
	}: {
		// 編集中の preset (ModelForm の形。system は params.system)
		model: () => object;
		hasFilters?: boolean;
	} = $props();

	let preview = $state<ModelPreview | null>(null);
	let loading = $state(false);
	let error = $state('');

	const load = async () => {
		loading = true;
		error = '';
		try {
			// チャットの送信 (send.ts) と同じブラウザ側の変数を渡す
			preview = await previewModel(model(), getPromptVariables($user?.name, undefined));
		} catch (e) {
			preview = null;
			error = `${e}`;
		}
		loading = false;
	};

	onMount(load);

	const size = (text: string, chars: number) =>
		$i18n.t('{{chars}} chars · ~{{tokens}} tokens', {
			chars: chars.toLocaleString(),
			tokens: estimateTokens(text).toLocaleString()
		});

	// tool の宣言 1 つ分の文字列 (BE の chars と同じく name・description・parameters の JSON)
	const specText = (tool: ModelPreview['tools'][number]) =>
		JSON.stringify({ name: tool.name, description: tool.description, parameters: tool.parameters });
	const toolsText = $derived((preview?.tools ?? []).map(specText).join(''));
</script>

<div class="flex flex-col gap-3 text-sm">
	<div class="flex items-start gap-2">
		<div class="flex-1 min-w-0 text-xs text-content-muted">
			{$i18n.t(
				'Expanded as you, for the first turn of a new chat: the tools the model receives, then the system prompt. Tools, skills and capabilities use the saved settings; the system prompt uses what you are editing. Nothing is run.'
			)}
			{#if hasFilters}
				{$i18n.t('Changes made by the inlet filters of this model are not shown.')}
			{/if}
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
		<section class="flex flex-col gap-1.5">
			<div class="flex flex-wrap items-baseline gap-x-2">
				<div class="font-semibold">
					1. {$i18n.t('Tools ({{total}})', { total: preview.tools.length })}
				</div>
				<div class="text-xs text-content-muted">{size(toolsText, preview.tools_chars)}</div>
			</div>
			<div class="text-xs text-content-muted">
				{$i18n.t(
					'Which tools appear can also depend on the user (access to each tool; load_skill only when the user can see a skill).'
				)}
			</div>
			{#if preview.tools.length === 0}
				<div class="text-xs text-content-subtle">{$i18n.t('No tools')}</div>
			{/if}
			{#each preview.tools as tool (tool.name)}
				{@const parts = splitToolDescription(tool)}
				<details class="rounded-lg border border-line">
					<summary class="flex items-center gap-2 px-2 py-1 cursor-pointer select-none min-w-0">
						<span class="font-mono text-xs font-medium truncate">{tool.name}</span>
						{#if toolVaries(tool)}
							<span
								class="shrink-0 px-1.5 rounded-full text-[10px] bg-amber-200/70 dark:bg-amber-400/25"
								>{$i18n.t('Differs per user')}</span
							>
						{/if}
						<span class="ml-auto shrink-0 text-xs text-content-subtle"
							>{size(specText(tool), tool.chars)}</span
						>
					</summary>
					<div class="px-2 pb-2 flex flex-col gap-1.5">
						<pre
							class="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed">{parts.fixed}{#if parts.varies}<span
									class="bg-amber-200/70 dark:bg-amber-400/25 rounded-xs">{parts.varies}</span
								>{/if}</pre>
						{#if (tool.varies?.params ?? []).length > 0}
							<div class="text-xs text-content-muted">
								{$i18n.t('The allowed values of {{params}} differ per user.', {
									params: (tool.varies?.params ?? []).join(', ')
								})}
							</div>
						{/if}
						<details>
							<summary class="text-xs text-content-muted cursor-pointer select-none"
								>{$i18n.t('Parameters')}</summary
							>
							<pre
								class="mt-1 whitespace-pre-wrap break-all font-mono text-xs text-content-muted">{JSON.stringify(
									tool.parameters,
									null,
									2
								)}</pre>
						</details>
					</div>
				</details>
			{/each}
		</section>

		<section class="flex flex-col gap-1.5">
			<div class="flex flex-wrap items-baseline gap-x-2">
				<div class="font-semibold">2. {$i18n.t('System Prompt')}</div>
				<div class="text-xs text-content-muted">
					{size(preview.system.text, preview.system.chars)}
				</div>
			</div>
			<div class="rounded-lg border border-line px-2 py-1.5">
				<ExpandedText value={preview.system} />
			</div>
		</section>
	{:else if loading}
		<div class="flex items-center gap-2 text-xs text-content-muted">
			<span class="i-lucide-loader-circle size-4 block animate-spin"></span>
			{$i18n.t('Loading...')}
		</div>
	{/if}
</div>

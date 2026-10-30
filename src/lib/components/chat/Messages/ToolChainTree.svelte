<script lang="ts">
	// tool の中で順に読み込んだ・実行したものの木(results[i].chain — 2026-10-30)。
	// load_recipe: requires を書いた順の行(recipe は入れ子)→ 本文。
	// load_skill(/command・自動ロード): before の行 → 本文 → after の行。
	// 1 行 = 種類・対象・状態(済 / 読み込み済み / 警告 / ERROR)。警告・ERROR は理由を常に出し、
	// 行を開くとその出力(と入れ子の中身)を見せる。既定は閉じる。
	// データはモデルに渡した本文とは別のメタデータ(モデルの context には入らない)。
	// XSS 不変条件: 対象・理由・出力は素のテキストで、本文だけ Markdown(DOMPurify)で描く。
	import { getContext } from 'svelte';

	import type { ToolChain, ToolChainNode, ToolChainStatus } from '$lib/utils/tool-render';
	import Markdown from './Markdown.svelte';

	const i18n = getContext('i18n');

	let { id, chain }: { id: string; chain: ToolChain } = $props();

	// 開いている行(木の位置で引く)
	let expanded = $state<Record<string, boolean>>({});

	const hasPhase = $derived(chain.entries.some((node) => node.phase));
	const beforeEntries = $derived(
		hasPhase ? chain.entries.filter((node) => node.phase !== 'after') : chain.entries
	);
	const afterEntries = $derived(
		hasPhase ? chain.entries.filter((node) => node.phase === 'after') : []
	);

	const STATUS_LOOK: Record<ToolChainStatus, { icon: string; color: string }> = {
		ok: { icon: 'i-lucide-circle-check', color: 'text-green-600 dark:text-green-400' },
		loaded: { icon: 'i-lucide-history', color: 'text-content-muted' },
		warning: { icon: 'i-lucide-triangle-alert', color: 'text-amber-600 dark:text-amber-400' },
		error: { icon: 'i-lucide-circle-x', color: 'text-red-600 dark:text-red-400' }
	};

	const statusLabel = (status: ToolChainStatus) =>
		status === 'ok'
			? $i18n.t('Ran')
			: status === 'loaded'
				? $i18n.t('Already loaded')
				: status === 'warning'
					? $i18n.t('Warning')
					: 'ERROR';

	const kindLabel = (node: ToolChainNode) =>
		node.phase ? `${node.phase === 'before' ? 'before' : 'after'} · ${node.kind}` : node.kind;

	const expandable = (node: ToolChainNode) =>
		Boolean(node.output) ||
		Boolean(node.children?.length) ||
		Boolean(node.body) ||
		Boolean(node.notes?.length);
</script>

{#snippet notesList(notes: string[] | undefined)}
	{#each notes ?? [] as note, i (i)}
		<div class="flex items-start gap-1.5 text-xs {STATUS_LOOK.warning.color}">
			<span class="i-lucide-triangle-alert size-3.5 block shrink-0 mt-px"></span>
			<span class="min-w-0 break-words">{note}</span>
		</div>
	{/each}
{/snippet}

{#snippet bodyBlock(body: string | undefined, key: string)}
	{#if body}
		<div class="mt-1">
			<div class="text-xs font-medium text-content-muted mb-0.5">{$i18n.t('Body')}</div>
			<div class="text-sm px-0.5" dir="auto">
				<Markdown id={`${id}-${key}-body`} content={body} />
			</div>
		</div>
	{/if}
{/snippet}

{#snippet row(node: ToolChainNode, key: string)}
	{@const look = STATUS_LOOK[node.status] ?? STATUS_LOOK.error}
	{@const canOpen = expandable(node)}
	<div data-chain-row={key}>
		<button
			type="button"
			class="w-full flex items-center gap-1.5 text-left rounded-sm px-0.5 py-0.5 {canOpen
				? 'hover:bg-gray-100 dark:hover:bg-gray-850 cursor-pointer'
				: 'cursor-default'}"
			aria-expanded={canOpen ? Boolean(expanded[key]) : undefined}
			onclick={() => {
				if (canOpen) expanded[key] = !expanded[key];
			}}
		>
			<span class="{look.icon} {look.color} size-3.5 block shrink-0"></span>
			<span
				class="text-xs font-mono shrink-0 rounded-sm bg-gray-100 dark:bg-gray-850 px-1 text-content-muted"
				>{kindLabel(node)}</span
			>
			<span class="text-xs font-mono min-w-0 flex-1 break-all" dir="ltr">{node.target}</span>
			<span class="text-xs shrink-0 {look.color}">{statusLabel(node.status)}</span>
			{#if canOpen}
				<span
					class="{expanded[key]
						? 'i-lucide-chevron-up'
						: 'i-lucide-chevron-down'} size-3 block shrink-0 text-content-muted"
				></span>
			{/if}
		</button>
		{#if node.reason && (node.status === 'warning' || node.status === 'error')}
			<div class="ps-5 text-xs break-words {look.color}">{node.reason}</div>
		{/if}
		{#if canOpen && expanded[key]}
			<div class="ms-1.5 ps-3 border-s border-line my-0.5 space-y-0.5">
				{#if node.output}
					<pre
						class="text-xs font-mono whitespace-pre-wrap break-all max-h-80 overflow-auto rounded-sm bg-gray-50 dark:bg-gray-900 px-2 py-1"
						dir="ltr">{node.output}</pre>
				{/if}
				{#each node.children ?? [] as child, i (i)}
					{@render row(child, `${key}.${i}`)}
				{/each}
				{@render notesList(node.notes)}
				{@render bodyBlock(node.body, key)}
			</div>
		{/if}
	</div>
{/snippet}

<div class="space-y-0.5" data-tool-chain>
	{#each beforeEntries as node, i (i)}
		{@render row(node, `b${i}`)}
	{/each}
	{@render notesList(chain.notes)}
	{@render bodyBlock(chain.body, 'root')}
	{#each afterEntries as node, i (i)}
		{@render row(node, `a${i}`)}
	{/each}
</div>

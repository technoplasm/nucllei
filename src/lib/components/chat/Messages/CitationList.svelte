<script lang="ts">
	// メッセージ下の出典一覧(#146 簡易版、2026-10-05)。本文の番号チップと同じ番号で
	// `1. ページ` を並べ、本文で使われた節(見出し / 「引用文」)をその下に添える。
	// リンクはすべて新しいタブ。本文のチップに hover 中の番号の行を薄くハイライトする。
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import type { CitationScope } from './citations.svelte';

	const i18n = getContext<Writable<i18nType>>('i18n');

	let { citations }: { citations: CitationScope } = $props();

	const entries = $derived(citations.current.entries);
</script>

{#if entries.length > 0}
	<div class="mt-1.5 mb-0.5 text-xs text-content-muted">
		<div class="mb-0.5 font-medium">{$i18n.t('Sources')}</div>
		<ol class="flex flex-col">
			{#each entries as entry (entry.key)}
				<li
					class="flex gap-1.5 rounded-md px-1 -mx-1 py-0.5 transition-colors {citations.active ===
					entry.n
						? 'bg-black/5 dark:bg-white/10 text-content'
						: ''}"
				>
					<span class="shrink-0 min-w-[1.5em] text-right tabular-nums">{entry.n}.</span>
					<div class="min-w-0 flex-1">
						<a
							href={entry.pageHref}
							target="_blank"
							rel="nofollow"
							class="inline-flex items-start gap-1 max-w-full hover:text-content hover:underline"
							title={entry.title}
						>
							<span
								class="{entry.kind === 'file'
									? 'i-lucide-paperclip'
									: 'i-lucide-file-text'} size-3.5 shrink-0 mt-px"
							></span>
							<span class="break-all">{entry.title}</span>
						</a>
						{#each entry.sections as section (section.href)}
							<div class="pl-[1.125rem]">
								<a
									href={section.href}
									target="_blank"
									rel="nofollow"
									class="inline-block max-w-full break-all hover:text-content hover:underline"
									>› {section.label}</a
								>
							</div>
						{/each}
					</div>
				</li>
			{/each}
		</ol>
	</div>
{/if}

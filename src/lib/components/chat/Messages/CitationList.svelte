<script lang="ts">
	// メッセージ下の出典一覧(#146 簡易版、2026-10-05)。本文の番号チップと同じ番号で
	// `1. ページ` を並べ、本文で使われた節(見出し / 「引用文」)をその下に添える。
	// リンクはすべて新しいタブ。本文のチップに hover 中の番号の行を薄くハイライトする。
	// 開けない出典(entry.openable が false — 判定は resolveBracketRef)はリンクにせず、打ち消し線の
	// 表示名と「開けない出典」の札を出す。開けない理由は title と読み上げ用の文。
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import type { CitationEntry } from '$lib/utils/citations';
	import type { CitationScope } from './citations.svelte';

	const i18n = getContext<Writable<i18nType>>('i18n');

	let { citations }: { citations: CitationScope } = $props();

	const entries = $derived(citations.current.entries);

	const unopenableReason = (entry: CitationEntry) =>
		entry.kind === 'sandbox'
			? $i18n.t('It points to a file in the sandbox, which cannot be opened from the chat.')
			: $i18n.t('It points to a file that is not attached to this chat, so it cannot be opened.');
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
						{#if !entry.openable}
							{@const reason = unopenableReason(entry)}
							<div class="flex items-start gap-1" title={reason}>
								<span class="i-lucide-file-x size-3.5 shrink-0 mt-px"></span>
								<span class="min-w-0">
									<span class="break-all line-through">{entry.title}</span>
									<span
										class="inline-block whitespace-nowrap rounded border border-dashed border-black/25 dark:border-white/30 px-1 leading-snug"
										>{$i18n.t('Unopenable source')}</span
									><span class="sr-only">{reason}</span>
								</span>
							</div>
							{#each entry.sections as section (section.label)}
								<div class="pl-[1.125rem] break-all line-through">› {section.label}</div>
							{/each}
						{:else}
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
						{/if}
					</div>
				</li>
			{/each}
		</ol>
	</div>
{/if}

<script lang="ts">
	// プレビュー (POST /models/preview・/skills/preview) の展開結果を、変数の断片ごとに色分けして出す。
	// 利用者ごと (user) と日時で変わる (time) に色を付け、preset の値 (preset) は点線の下線だけ —
	// prefix cache の観点で、どこが preset ごとに固定かを目で確かめるため。その場所では展開しない変数
	// (misplaced — 空になる) は変数名を赤い印で、自分には空に展開される変数は変数名を点線の枠で出す
	// (どちらも送られる文には入らない)。
	import { getContext } from 'svelte';

	import type { PreviewText } from '$lib/apis/models';

	const i18n = getContext('i18n');

	let {
		value,
		misplacedTitle = ''
	}: {
		value: PreviewText;
		// misplaced の印の説明 (どこに書けば展開されるか)
		misplacedTitle?: string;
	} = $props();

	const SCOPE_CLASS: Record<string, string> = {
		user: 'bg-amber-200/70 dark:bg-amber-400/25 rounded-xs',
		time: 'bg-sky-200/70 dark:bg-sky-400/25 rounded-xs',
		preset: 'underline decoration-dotted underline-offset-2'
	};
	const MISPLACED_CLASS =
		'px-0.5 rounded-xs line-through text-red-700 bg-red-100 dark:text-red-300 dark:bg-red-500/20';
	const hasVariables = $derived(value.segments.some((s) => s.scope !== 'fixed'));
</script>

{#if value.text === '' && !hasVariables}
	<div class="text-xs text-content-subtle">{$i18n.t('(empty)')}</div>
{:else}
	<pre
		class="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-content">{#each value.segments as segment, i (i)}{#if segment.scope === 'fixed'}{segment.text}{:else if segment.scope === 'misplaced'}<span
					class={MISPLACED_CLASS}
					title={misplacedTitle || $i18n.t('Not expanded here (it becomes empty).')}
					>{segment.variable}</span
				>{:else if segment.text === ''}<span
					class="px-0.5 rounded-xs border border-dashed border-line text-content-subtle"
					title={$i18n.t('Expands to nothing for you.')}
					>{segment.variable} {$i18n.t('(empty)')}</span
				>{:else}<span class={SCOPE_CLASS[segment.scope]} title={segment.variable}
					>{segment.text}</span
				>{/if}{/each}</pre>
	{#if !value.exact}
		<div class="mt-1 text-xs text-content-muted">
			{$i18n.t(
				'Could not split this into parts (a variable value joins the text around it), so it is shown without marks.'
			)}
		</div>
	{/if}
{/if}

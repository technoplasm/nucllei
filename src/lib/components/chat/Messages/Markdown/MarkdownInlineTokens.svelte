<script lang="ts">
	import { toast } from 'svelte-sonner';

	import { getContext } from 'svelte';

	const i18n = getContext('i18n');

	import { WEBUI_BASE_URL } from '$lib/constants';
	import { copyToClipboard, unescapeHtml } from '$lib/utils';
	import { sanitizeIframeHtml, sanitizeChatHtml } from '$lib/utils/marked/html-token';

	import Image from '$lib/components/common/Image.svelte';
	import KatexRenderer from './KatexRenderer.svelte';
	import MarkdownInlineTokens from './MarkdownInlineTokens.svelte';
	import { citeLabel } from '$lib/utils/citations';
	import { getCitationScope } from '../citations.svelte';

	// 番号付き引用(#146 簡易版)。メッセージ本文の Markdown の下でだけ非 null
	const citations = getCitationScope();

	type Token = { type: string; [key: string]: any };

	let {
		id,
		tokens
	}: {
		id: string;
		tokens: Token[];
	} = $props();
</script>

{#each tokens as token}
	{#if token.type === 'escape'}
		{unescapeHtml(token.text)}
	{:else if token.type === 'html'}
		{@const html = sanitizeChatHtml(token.text)}
		{#if html && html.includes('<video')}
			<!-- eslint-disable-next-line svelte/no-at-html-tags -- #17 XSS sink 監査済み(サニタイズは各経路) -->
			{@html html}
		{:else if token.text.includes(`<iframe src="${WEBUI_BASE_URL}/api/v1/files/`)}
			<!-- eslint-disable-next-line svelte/no-at-html-tags -- #17 XSS sink 監査済み(サニタイズは各経路) -->
			{@html sanitizeIframeHtml(token.text)}
		{:else}
			<!-- eslint-disable-next-line svelte/no-at-html-tags -- #17 XSS sink 監査済み(サニタイズは各経路) -->
			{@html html}
		{/if}
	{:else if token.type === 'link' && token.cite && citations?.numberOf(token.cite.key)}
		<!-- 本文の [/ext/…] / [file_id] → 上付きの番号チップ。同じページは同じ番号、リンク先は
		     その箇所(節)のまま。角括弧は範囲選択のコピーで「[1]」になるよう見えない文字で残す -->
		{@const n = citations.numberOf(token.cite.key)}
		<sup class="cite-ref"
			><a
				href={token.href}
				target="_blank"
				rel="nofollow"
				title={citeLabel(token.cite)}
				aria-label={`[${n}] ${citeLabel(token.cite)}`}
				class="inline-flex items-center justify-center min-w-[1.45em] h-[1.45em] px-[0.35em] mx-[0.15em] rounded-full text-[0.95em] leading-none font-medium tabular-nums no-underline! bg-black/5 dark:bg-white/10 text-content-muted hover:bg-black/10 dark:hover:bg-white/20 hover:text-content transition-colors"
				onmouseenter={() => (citations.active = n ?? null)}
				onmouseleave={() => (citations.active = null)}
				onfocus={() => (citations.active = n ?? null)}
				onblur={() => (citations.active = null)}
				><span class="sr-only">[</span>{n}<span class="sr-only">]</span></a
			></sup
		>
	{:else if token.type === 'link'}
		<!-- #87: {target=_self} / {download=...} の出し分け。target は _self のみ opt-in
		     (SPA からの同一タブ遷移は明示時だけ)、download は same-origin でのみ効く(ブラウザ仕様) -->
		{@const linkTarget = token.target === '_self' ? '_self' : '_blank'}
		{@const linkDownload = typeof token.download === 'string' ? token.download : undefined}
		{#if token.tokens}
			<a
				href={token.href}
				target={linkTarget}
				download={linkDownload}
				rel="nofollow"
				title={token.title}
			>
				<MarkdownInlineTokens id={`${id}-a`} tokens={token.tokens} />
			</a>
		{:else}
			<a
				href={token.href}
				target={linkTarget}
				download={linkDownload}
				rel="nofollow"
				title={token.title}>{token.text}</a
			>
		{/if}
	{:else if token.type === 'image'}
		<Image src={token.href} alt={token.text} />
	{:else if token.type === 'strong'}
		<strong><MarkdownInlineTokens id={`${id}-strong`} tokens={token.tokens} /></strong>
	{:else if token.type === 'em'}
		<em><MarkdownInlineTokens id={`${id}-em`} tokens={token.tokens} /></em>
	{:else if token.type === 'codespan'}
		<!-- svelte-ignore a11y_click_events_have_key_events -->
		<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
		<code
			class="codespan cursor-pointer"
			onclick={() => {
				copyToClipboard(unescapeHtml(token.text));
				toast.success($i18n.t('Copied to clipboard'));
			}}>{unescapeHtml(token.text)}</code
		>
	{:else if token.type === 'br'}
		<br />
	{:else if token.type === 'del'}
		<del><MarkdownInlineTokens id={`${id}-del`} tokens={token.tokens} /></del>
	{:else if token.type === 'inlineKatex'}
		{#if token.text}
			<KatexRenderer content={token.text} displayMode={false} />
		{/if}
	{:else if token.type === 'iframe'}
		<iframe
			src="{WEBUI_BASE_URL}/api/v1/files/{token.fileId}/content"
			title={token.fileId}
			width="100%"
			frameborder="0"
			onload={(e) => {
				const el = e.currentTarget as HTMLIFrameElement;
				const body = el.contentWindow?.document?.body;
				if (body) {
					el.style.height = body.scrollHeight + 20 + 'px';
				}
			}}
		></iframe>
	{:else if token.type === 'text'}
		{token.raw}
	{/if}
{/each}

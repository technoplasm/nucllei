<script lang="ts">
	import DOMPurify from 'dompurify';
	import { toast } from 'svelte-sonner';

	import { getContext } from 'svelte';

	const i18n = getContext('i18n');

	import { WEBUI_BASE_URL } from '$lib/constants';
	import { copyToClipboard, unescapeHtml } from '$lib/utils';
	import { sanitizeIframeHtml } from '$lib/utils/marked/html-token';

	import Image from '$lib/components/common/Image.svelte';
	import KatexRenderer from './KatexRenderer.svelte';
	import Source from './Source.svelte';
	import MarkdownInlineTokens from './MarkdownInlineTokens.svelte';

	type Token = { type: string; [key: string]: any };

	let {
		id,
		tokens,
		onSourceClick = () => {}
	}: {
		id: string;
		tokens: Token[];
		onSourceClick?: (id: string, sourceId?: string) => void;
	} = $props();
</script>

{#each tokens as token}
	{#if token.type === 'escape'}
		{unescapeHtml(token.text)}
	{:else if token.type === 'html'}
		{@const html = DOMPurify.sanitize(token.text)}
		{#if html && html.includes('<video')}
			<!-- eslint-disable-next-line svelte/no-at-html-tags -- #17 XSS sink 監査済み(サニタイズは各経路) -->
			{@html html}
		{:else if token.text.includes(`<iframe src="${WEBUI_BASE_URL}/api/v1/files/`)}
			<!-- eslint-disable-next-line svelte/no-at-html-tags -- #17 XSS sink 監査済み(サニタイズは各経路) -->
			{@html sanitizeIframeHtml(token.text)}
		{:else if token.text.includes(`<source_id`)}
			<Source {id} {token} onClick={onSourceClick} />
		{:else}
			<!-- eslint-disable-next-line svelte/no-at-html-tags -- #17 XSS sink 監査済み(サニタイズは各経路) -->
			{@html html}
		{/if}
	{:else if token.type === 'link'}
		{#if token.tokens}
			<a href={token.href} target="_blank" rel="nofollow" title={token.title}>
				<MarkdownInlineTokens id={`${id}-a`} tokens={token.tokens} {onSourceClick} />
			</a>
		{:else}
			<a href={token.href} target="_blank" rel="nofollow" title={token.title}>{token.text}</a>
		{/if}
	{:else if token.type === 'image'}
		<Image src={token.href} alt={token.text} />
	{:else if token.type === 'strong'}
		<strong
			><MarkdownInlineTokens id={`${id}-strong`} tokens={token.tokens} {onSourceClick} /></strong
		>
	{:else if token.type === 'em'}
		<em><MarkdownInlineTokens id={`${id}-em`} tokens={token.tokens} {onSourceClick} /></em>
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
		<del><MarkdownInlineTokens id={`${id}-del`} tokens={token.tokens} {onSourceClick} /></del>
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

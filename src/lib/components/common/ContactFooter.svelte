<script lang="ts">
	import { getContext } from 'svelte';
	import { config } from '$lib/stores';

	// ログイン画面・承認待ち/無効化の画面に出す配備側の案内
	// (/api/config の footer_text = WEBUI_FOOTER_TEXT、contact_email = WEBUI_CONTACT_EMAIL)。
	// どちらも空なら何も描かない。どちらもテキストノードとして出す(HTML / Markdown・リンクに
	// しない)ので sanitize は要らない。footer_text の改行は whitespace-pre-line で保つ。
	const i18n = getContext('i18n');

	let { class: className = '' }: { class?: string } = $props();

	const footerText = $derived(($config?.footer_text ?? '').trim());
	const contactEmail = $derived(($config?.contact_email ?? '').trim());
</script>

{#if footerText || contactEmail}
	<div class="text-xs text-content-muted break-words {className}" data-testid="contact-footer">
		{#if footerText}
			<p class="whitespace-pre-line">{footerText}</p>
		{/if}
		{#if contactEmail}
			<p class:mt-1={footerText}>
				<!-- キーに `:` があるので名前空間の区切りとして読ませない -->
				{$i18n.t('Contact:', { nsSeparator: false })}
				<span>{contactEmail}</span>
			</p>
		{/if}
	</div>
{/if}

<script lang="ts" module>
	// highlight.js 公式テーマ(github=light / github-dark=dark)を **そのまま** 使う。
	// 配色は手書きしない。tailwind darkMode:'class' に合わせ、公式 CSS の `.hljs`
	// 系セレクタをテーマ別にスコープして 1 回だけ注入する。
	import githubLight from 'highlight.js/styles/docco.css?inline';
	import githubDark from 'highlight.js/styles/lioshi.css?inline';

	let injected = false;
	function injectHljsTheme() {
		if (injected || typeof document === 'undefined') return;
		injected = true;
		const style = document.createElement('style');
		style.setAttribute('data-hljs-theme', 'github-auto');
		style.textContent =
			githubLight.replace(/\.hljs\b/g, ':root:not(.dark) .hljs') +
			'\n' +
			githubDark.replace(/\.hljs\b/g, '.dark .hljs');
		document.head.appendChild(style);
	}
</script>

<script lang="ts">
	// hljs は既に依存(utils/index.ts で使用)なので新規依存ゼロ。コードの静的
	// ハイライトはチャット本文のコードフェンスも tool 引数/結果もこの 1 枚に
	// 一本化する(CodeMirror = 編集用は workspace のエディタ画面だけ)。
	// hljs.highlight は入力を HTML エスケープしてから span で包むため、
	// {@html} でも code 内容由来の XSS は生じない。
	import hljs from 'highlight.js';
	import { getContext, onMount } from 'svelte';
	import { copyToClipboard } from '$lib/utils';
	import { settings } from '$lib/stores';

	const i18n = getContext('i18n');

	let { code = '', lang = 'json', className = '' } = $props();

	// ヘッダ(言語ラベル + 折りたたみ/コピー)は常時 — チャット本文のコード
	// フェンスも tool 引数/結果も同じレンダリング。表示専用で編集機能は持たない
	// (ここで直したコードを実行する経路はない)。展開の初期値は本文コード
	// フェンスと同じ expandCodeBlocks 設定(既定 ON)。
	let open = $state($settings?.expandCodeBlocks ?? true);
	let copied = $state(false);

	onMount(injectHljsTheme);

	const copyCode = async () => {
		copied = true;
		await copyToClipboard(code);
		setTimeout(() => {
			copied = false;
		}, 1000);
	};

	const highlighted = $derived.by(() => {
		try {
			const language = hljs.getLanguage(lang) ? lang : 'plaintext';
			return hljs.highlight(code ?? '', { language }).value;
		} catch (e) {
			const div = document.createElement('div');
			div.textContent = code ?? '';
			return div.innerHTML;
		}
	});

	// plaintext(言語なし)fence は M PLUS 1 Code 単独(font-mono-cjk)で描画する。
	// 既定の mono スタックは半角=JetBrains Mono(0.60em)+ 全角=M+1 Code(1.00em)の
	// 混成で全角/半角比が 1.667 になり、アスキーアート・罫線表の桁が必ずずれる。
	// M+1 Code 単独なら 0.50/1.00 = ちょうど 2:1 で罫線も半角幅に揃う。
	// 言語付き(=コード)は字面優先で従来スタックのまま。
	const isPlaintext = $derived(!lang || !hljs.getLanguage(lang));
</script>

<div
	class="hljs rounded-lg text-xs my-1 {isPlaintext ? 'font-mono-cjk' : ''} {className}"
	dir="ltr"
>
	<div class="flex items-center justify-between px-3 pt-1.5">
		<div class="font-medium opacity-60">{lang || 'plaintext'}</div>

		<div class="flex items-center gap-2">
			<button
				class="flex gap-1 items-center opacity-60 hover:opacity-100"
				onclick={() => {
					open = !open;
				}}
			>
				<span class="i-lucide-chevrons-up-down size-3 block"></span>
				{open ? $i18n.t('Collapse') : $i18n.t('Expand')}
			</button>

			<button
				class="copy-code-button flex gap-1 items-center opacity-60 hover:opacity-100"
				onclick={copyCode}
			>
				<span class="{copied ? 'i-lucide-check' : 'i-lucide-copy'} size-3 block"></span>
				{copied ? $i18n.t('Copied') : $i18n.t('Copy')}
			</button>
		</div>
	</div>

	{#if open}
		<!-- eslint-disable svelte/no-at-html-tags -- #17 XSS sink 監査済み(hljs 出力) -->
		<pre class="overflow-x-auto p-3 pt-1.5"><code class="language-{lang}"
				>{@html highlighted}</code
			></pre>
		<!-- eslint-enable svelte/no-at-html-tags -->
	{:else}
		<div class="px-3 pt-1 pb-2 italic opacity-60">
			{$i18n.t('{{COUNT}} hidden lines', { COUNT: (code ?? '').split('\n').length })}
		</div>
	{/if}
</div>

<script lang="ts" context="module">
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
	// hljs は既に依存(utils/index.ts で使用)なので新規依存ゼロ。編集不要な構造
	// データ(tool 引数/結果など)を CodeMirror(重い CodeBlock)ではなく hljs で
	// 静的にハイライトして出す。hljs.highlight は入力を HTML エスケープしてから
	// span で包むため、{@html} でも code 内容由来の XSS は生じない。
	import hljs from 'highlight.js';
	import { onMount } from 'svelte';

	export let code = '';
	export let lang = 'json';
	export let className = '';

	onMount(injectHljsTheme);

	$: highlighted = (() => {
		try {
			const language = hljs.getLanguage(lang) ? lang : 'plaintext';
			return hljs.highlight(code ?? '', { language }).value;
		} catch (e) {
			const div = document.createElement('div');
			div.textContent = code ?? '';
			return div.innerHTML;
		}
	})();
</script>

<pre
	class="hljs overflow-x-auto rounded-lg text-xs p-3 my-1 scrollbar-hidden {className}"
	dir="ltr"><code class="language-{lang}">{@html highlighted}</code></pre>

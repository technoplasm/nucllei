<script lang="ts">
	import mermaid from 'mermaid';

	import { v4 as uuidv4 } from 'uuid';

	import { onMount } from 'svelte';

	import HljsCode from '$lib/components/common/HljsCode.svelte';
	import SvgPanZoom from '$lib/components/common/SVGPanZoom.svelte';

	type Token = { type: string; [key: string]: any };

	let {
		token = null,
		lang = '',
		code = '',
		className = 'my-2'
	}: {
		id?: string;
		token?: Token | null;
		lang?: string;
		code?: string;
		className?: string;
	} = $props();

	let _token: Token | null = $state.raw(null);

	let mermaidHtml: string | null = $state(null);

	// llens(#67/#23): UI からのコード実行(Run ボタン)も code_interpreter の
	// 実行結果表示(attributes.output)も撤去 — コード実行は tool 一本で、
	// 結果は tool_calls ブロック側に表示される。
	// llens(hljs 一本化): CodeMirror(CodeEditor)による表示・編集も撤去。
	// 表示は tool 引数/結果と同じ HljsCode(header 付き)に統一 — チャット内の
	// コードは表示専用で、ここで編集したものを実行する経路はない。

	const drawMermaidDiagram = async () => {
		try {
			if (await mermaid.parse(code)) {
				const { svg } = await mermaid.render(`mermaid-${uuidv4()}`, code);
				mermaidHtml = svg;
			}
		} catch (error) {
			console.error('Error:', error);
		}
	};

	const render = async () => {
		if (lang === 'mermaid' && (token?.raw ?? '').slice(-4).includes('```')) {
			await drawMermaidDiagram();
		}
	};

	$effect(() => {
		if (token && JSON.stringify(token) !== JSON.stringify(_token)) {
			_token = token;
		}
	});

	$effect(() => {
		if (_token) {
			render();
		}
	});

	onMount(() => {
		// llens(GHSA-v8qj-hxv7-mgvv): securityLevel 'loose' はラベル内 HTML と click バインドを
		// 許可し、モデル生成の mermaid コードから XSS が成立する。'strict' でラベルを
		// エスケープ・click を無効化する(SVG は SVGPanZoom 側で DOMPurify も適用=多層防御)。
		if (document.documentElement.classList.contains('dark')) {
			mermaid.initialize({
				startOnLoad: true,
				theme: 'dark',
				securityLevel: 'strict'
			});
		} else {
			mermaid.initialize({
				startOnLoad: true,
				theme: 'default',
				securityLevel: 'strict'
			});
		}
	});
</script>

<div class="relative {className} flex flex-col rounded-lg" dir="ltr">
	{#if lang === 'mermaid'}
		{#if mermaidHtml}
			<SvgPanZoom
				className=" border border-gray-100 dark:border-gray-850 rounded-lg max-h-fit overflow-hidden"
				svg={mermaidHtml}
				content={_token?.text ?? ''}
			/>
		{:else}
			<pre class="mermaid">{code}</pre>
		{/if}
	{:else}
		<HljsCode {lang} {code} className="my-0" />
	{/if}
</div>

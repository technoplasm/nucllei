<script lang="ts">
	import mermaid from 'mermaid';

	import { v4 as uuidv4 } from 'uuid';

	import { getContext, onMount } from 'svelte';
	import { copyToClipboard } from '$lib/utils';

	import CodeEditor from '$lib/components/common/CodeEditor.svelte';
	import SvgPanZoom from '$lib/components/common/SVGPanZoom.svelte';

	const i18n = getContext('i18n');

	type Token = { type: string; [key: string]: any };

	let {
		id = '',
		onSave = () => {},
		save = false,
		collapsed = false,
		token = null,
		lang = '',
		code = '',
		className = 'my-2',
		editorClassName = '',
		stickyButtonsClassName = 'top-8'
	}: {
		id?: string;
		onSave?: (code: string) => void;
		save?: boolean;
		collapsed?: boolean;
		token?: Token | null;
		lang?: string;
		code?: string;
		className?: string;
		editorClassName?: string;
		stickyButtonsClassName?: string;
	} = $props();

	let _code = $state('');
	$effect(() => {
		if (code) {
			_code = code;
		}
	});

	let _token: Token | null = $state.raw(null);

	let mermaidHtml: string | null = $state(null);

	// llens(#67/#23): UI からのコード実行(Run ボタン)も code_interpreter の
	// 実行結果表示(attributes.output)も撤去 — コード実行は tool 一本で、
	// 結果は tool_calls ブロック側に表示される。
	let copied = $state(false);
	let saved = $state(false);

	const collapseCodeBlock = () => {
		collapsed = !collapsed;
	};

	const saveCode = () => {
		saved = true;

		code = _code;
		onSave(code);

		setTimeout(() => {
			saved = false;
		}, 1000);
	};

	const copyCode = async () => {
		copied = true;
		await copyToClipboard(code);

		setTimeout(() => {
			copied = false;
		}, 1000);
	};

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

<div>
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
			<div class="text-text-300 absolute pl-4 py-1.5 text-xs font-medium dark:text-white">
				{lang}
			</div>

			<div
				class="sticky {stickyButtonsClassName} mb-1 py-1 pr-2.5 flex items-center justify-end z-10 text-xs text-black dark:text-white"
			>
				<div class="flex items-center gap-0.5 translate-y-[1px]">
					<button
						class="flex gap-1 items-center bg-none border-none bg-gray-50 hover:bg-gray-100 dark:bg-gray-850 dark:hover:bg-gray-800 rounded-md px-1.5 py-0.5"
						onclick={collapseCodeBlock}
					>
						<div>
							<span class="i-lucide-chevrons-up-down size-3 block"></span>
						</div>

						<div>
							{collapsed ? $i18n.t('Expand') : $i18n.t('Collapse')}
						</div>
					</button>

					{#if save}
						<button
							class="save-code-button bg-none border-none bg-gray-50 hover:bg-gray-100 dark:bg-gray-850 dark:hover:bg-gray-800 rounded-md px-1.5 py-0.5"
							onclick={saveCode}
						>
							{saved ? $i18n.t('Saved') : $i18n.t('Save')}
						</button>
					{/if}

					<button
						class="copy-code-button bg-none border-none bg-gray-50 hover:bg-gray-100 dark:bg-gray-850 dark:hover:bg-gray-800 rounded-md px-1.5 py-0.5"
						onclick={copyCode}>{copied ? $i18n.t('Copied') : $i18n.t('Copy')}</button
					>
				</div>
			</div>

			<div
				class="language-{lang} rounded-t-lg -mt-8 {editorClassName
					? editorClassName
					: 'rounded-b-lg'} overflow-hidden"
			>
				<div class=" pt-7 bg-gray-50 dark:bg-gray-850"></div>

				{#if !collapsed}
					<CodeEditor
						value={code}
						{id}
						{lang}
						onSave={() => {
							saveCode();
						}}
						onChange={(value) => {
							_code = value;
						}}
					/>
				{:else}
					<div
						class="bg-gray-50 dark:bg-black dark:text-white rounded-b-lg! pt-2 pb-2 px-4 flex flex-col gap-2 text-xs"
					>
						<span class="text-content-muted italic">
							{$i18n.t('{{COUNT}} hidden lines', {
								COUNT: code.split('\n').length
							})}
						</span>
					</div>
				{/if}
			</div>
		{/if}
	</div>
</div>

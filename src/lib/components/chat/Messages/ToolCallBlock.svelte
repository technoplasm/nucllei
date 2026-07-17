<script lang="ts">
	// tool call 1 件の表示(content_blocks の tool_calls ブロック配下、#17/#20)。
	// 旧・汎用 Collapsible の attributes 経由レンダリングから独立させた専用コンポーネント。
	//
	// レイアウト: ヘッダ(Tool の使用 NAME)は開くと枠の上辺 tab に合体し、
	// Arguments と Result は同じ 1 枚の枠の中の区画(Result は内部罫線 + tab)。
	// 引数の無い tool は Arguments 区画にグレーの「引数なし」を出す。
	// 閉じるハンドルは枠の下辺・左側、すべて閉じる(メッセージ内一括)は右側。
	// ヘッダ・Result tab・閉じるハンドルのどれをクリックしても閉じられる。
	// tab は上側だけ rounded・下辺なしで、-mb-px/-mt-px で枠線に重ねて一体化する。
	import { getContext } from 'svelte';
	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';

	import { settings } from '$lib/stores';
	import {
		classifyToolResult,
		formatJSONString,
		parseToolArgumentsStreaming,
		type ToolCallRender
	} from '$lib/utils/tool-render';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import HljsCode from '$lib/components/common/HljsCode.svelte';
	import Image from '$lib/components/common/Image.svelte';
	import Markdown from './Markdown.svelte';

	const i18n = getContext('i18n');

	type ToolCall = {
		id: string;
		function?: { name?: string; arguments?: string };
		render?: ToolCallRender;
	};
	type ToolResult = {
		tool_call_id: string;
		content?: string;
		files?: unknown;
		render?: { kind: string };
	};

	let {
		id = '',
		call,
		result = null,
		// メッセージ確定後(中断含む)は result が無くてもスピナーを止める保険
		done = false,
		// メッセージ内一括開閉(ContentBlocks が採番)。seq が進んだら open に追従する
		expandAll = null,
		onExpandAll = undefined
	}: {
		id?: string;
		call: ToolCall;
		result?: ToolResult | null;
		done?: boolean;
		expandAll?: { seq: number; open: boolean } | null;
		onExpandAll?: (open: boolean) => void;
	} = $props();

	// tool call の自動展開(実行中だけ開く #20 ステップ5)は OFF —
	// reasoning と同じく expandDetails 設定を初期値に、手動の開閉を維持する。
	let open = $state($settings?.expandDetails ?? false);
	let rootElement: HTMLElement | null = $state(null);
	// 開閉コントロール(バッジ/tab)hover 中は開閉単位のボーダー全部を強調する
	let toggleHover = $state(false);

	// 初期値キャプチャは意図(seq 比較の lastState ガード — 変化時のみ $effect で追従)
	// svelte-ignore state_referenced_locally
	let lastExpandSeq = expandAll?.seq ?? 0;
	$effect(() => {
		if (expandAll && expandAll.seq !== lastExpandSeq) {
			lastExpandSeq = expandAll.seq;
			open = expandAll.open;
		}
	});

	const name = $derived(call?.function?.name ?? '');
	const args = $derived(call?.function?.arguments ?? '');
	const isDone = $derived(Boolean(result) || done);

	// 引数はディスクリプタ(call.render.args、#20)どおりに引数単位で描画。
	// ストリーミング中の未閉鎖 JSON は寛容パーサで部分値を逐次描画し、
	// object 以外の壊れ方のみ一枚岩 JSON にフォールバック(argDisplays === null)。
	const argDisplays = $derived(parseToolArgumentsStreaming(args, call?.render?.args));
	const hasArgs = $derived(argDisplays === null || argDisplays.length > 0);

	// 結果の表示種別: 戻り型宣言(call.render.result)→ backend が実行時の実型から
	// 同梱した result.render(dict/list → json)→ text(中身からの動的解釈はしない)
	const resultDisplay = $derived(
		result ? classifyToolResult(result.content ?? '', call?.render?.result ?? result.render) : null
	);
	const files = $derived(Array.isArray(result?.files) ? (result.files as string[]) : []);

	// llens(#17): 長い展開の末尾からも閉じられるようにする
	const collapseFromBottom = () => {
		open = false;
		requestAnimationFrame(() => {
			rootElement?.scrollIntoView({ block: 'nearest' });
		});
	};
</script>

<!-- 注意: ルートに space-y を置かない — ヘッダ tab は -mb-px で枠上辺に密着させる -->
<div {id} class="w-full text-sm -ml-2" bind:this={rootElement}>
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<!-- svelte-ignore a11y_click_events_have_key_events -->
	<div
		class="w-fit cursor-pointer text-content-muted hover:text-content transition ms-2 px-2 py-0.5 border {toggleHover
			? 'border-line-hover'
			: 'border-line'} bg-gray-100 dark:bg-gray-850 {open
			? 'relative z-[1] -mb-px rounded-t-md border-b-0'
			: 'rounded-md'}"
		onpointerenter={() => (toggleHover = true)}
		onpointerleave={() => (toggleHover = false)}
		onpointerup={() => {
			open = !open;
		}}
	>
		<div class="font-medium flex items-center gap-2 {isDone ? '' : 'shimmer'}">
			{#if !isDone}
				<div>
					<Spinner className="size-4" />
				</div>
			{:else}
				<div class="text-green-500 dark:text-green-400 shrink-0">
					<span class="i-lucide-circle-check size-4 block"></span>
				</div>
			{/if}

			<div>
				{#if isDone}
					<Markdown
						id={`${id}-header`}
						content={$i18n.t('Tool used **{{NAME}}**', { NAME: name })}
					/>
				{:else}
					<Markdown
						id={`${id}-header-executing`}
						content={$i18n.t('Executing **{{NAME}}**...', { NAME: name })}
					/>
				{/if}
			</div>

			<div class="flex self-center translate-y-[1px]">
				{#if open}
					<span class="i-lucide-chevron-up size-3.5 block"></span>
				{:else}
					<span class="i-lucide-chevron-down size-3.5 block"></span>
				{/if}
			</div>
		</div>
	</div>

	{#if open}
		<div transition:slide={{ duration: 300, easing: quintOut, axis: 'y' }}>
			<div class="border {toggleHover ? 'border-line-hover' : 'border-line'} rounded-lg transition">
				<div class="px-3 py-2">
					{#if !hasArgs}
						<div class="text-xs text-content-muted italic my-0.5 px-0.5">
							{$i18n.t('No arguments')}
						</div>
					{:else if argDisplays === null}
						<HljsCode lang="json" code={formatJSONString(args)} />
					{:else}
						{#each argDisplays as arg (arg.name)}
							{#if arg.kind === 'scalar'}
								<div class="text-sm font-mono my-0.5 px-0.5" dir="ltr">
									<span class="text-content-muted">{arg.name}:</span>
									<span class="whitespace-pre-wrap">{arg.value}</span>
								</div>
							{:else}
								<div class="text-content-muted text-sm mt-1 mb-0.5 font-mono">
									{arg.name}:
								</div>
								<HljsCode
									lang={arg.kind === 'code' ? (arg.language ?? 'plaintext') : 'json'}
									code={arg.value}
								/>
							{/if}
						{/each}
					{/if}
				</div>

				{#if resultDisplay}
					<div class="flex ms-2 mt-1">
						<!-- Result tab: クリックでも閉じられる -->
						<!-- svelte-ignore a11y_no_static_element_interactions -->
						<!-- svelte-ignore a11y_click_events_have_key_events -->
						<div
							class="relative z-[1] -mb-px cursor-pointer inline-flex items-center gap-1 text-xs font-medium text-content-muted hover:text-content transition bg-gray-100 dark:bg-gray-850 border border-b-0 {toggleHover
								? 'border-line-hover'
								: 'border-line'} rounded-t-md px-1.5 py-0.5"
							onpointerenter={() => (toggleHover = true)}
							onpointerleave={() => (toggleHover = false)}
							onpointerup={(e) => {
								e.stopPropagation();
								collapseFromBottom();
							}}
						>
							<span class="i-lucide-corner-down-left size-3 block text-emerald-500"></span>
							Result
						</div>
					</div>
					<div
						class="border-t {toggleHover
							? 'border-line-hover'
							: 'border-line'} transition px-3 py-2"
					>
						{#if resultDisplay.kind === 'skill'}
							<!--
								load_skill の構造化結果(#35): 指示本文(md レンダリング)+ 導入 tool 一覧。
								XSS 不変条件: tool 結果は外部入力なのでサニタイズ必須 — Markdown は
								MarkdownTokens/InlineTokens 内で DOMPurify.sanitize を通す(ReasoningBlock と同じ)。
							-->
							<div class="text-sm px-0.5" dir="auto">
								<Markdown id={`${id || call.id}-skill`} content={resultDisplay.value} />
							</div>
							{#if resultDisplay.tools.length > 0}
								<div class="mt-1.5 mb-1">
									<span
										class="inline-block text-xs font-medium text-content-muted bg-gray-100 dark:bg-gray-850 rounded-md px-1.5 py-0.5"
									>
										{$i18n.t('Tools loaded')}
									</span>
								</div>
								<div class="flex flex-wrap gap-1 my-0.5 px-0.5">
									{#each resultDisplay.tools as toolName}
										<span
											class="text-xs font-mono rounded-md bg-gray-100 dark:bg-gray-850 px-1.5 py-0.5"
											>{toolName}</span
										>
									{/each}
								</div>
							{/if}
						{:else if resultDisplay.kind === 'text'}
							<div class="text-sm font-mono whitespace-pre-wrap my-0.5 px-0.5" dir="ltr">
								{resultDisplay.value}
							</div>
						{:else if resultDisplay.kind === 'markdown'}
							<!-- Markdown 戻り型: md としてレンダリング(サニタイズは skill と同じ経路) -->
							<div class="text-sm px-0.5" dir="auto">
								<Markdown id={`${id || call.id}-result`} content={resultDisplay.value} />
							</div>
						{:else if resultDisplay.kind === 'code'}
							<HljsCode lang={resultDisplay.language} code={resultDisplay.value} />
						{:else}
							<HljsCode lang="json" code={resultDisplay.value} />
						{/if}
					</div>
				{/if}
			</div>

			<div class="flex justify-between mx-2">
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<!-- svelte-ignore a11y_click_events_have_key_events -->
				<div
					class="relative cursor-pointer -mt-px flex items-center gap-1 text-xs font-medium text-content-muted hover:text-content transition bg-gray-100 dark:bg-gray-850 border border-t-0 {toggleHover
						? 'border-line-hover'
						: 'border-line'} rounded-b-md px-1.5 py-0.5"
					onpointerenter={() => (toggleHover = true)}
					onpointerleave={() => (toggleHover = false)}
					onpointerup={(e) => {
						e.stopPropagation();
						collapseFromBottom();
					}}
				>
					<span class="i-lucide-chevron-up size-3 block"></span>
					{$i18n.t('Close')}
				</div>

				{#if onExpandAll}
					<!-- svelte-ignore a11y_no_static_element_interactions -->
					<!-- svelte-ignore a11y_click_events_have_key_events -->
					<div
						class="relative cursor-pointer -mt-px flex items-center gap-1 text-xs font-medium text-content-muted hover:text-content transition bg-gray-100 dark:bg-gray-850 border border-t-0 {toggleHover
							? 'border-line-hover'
							: 'border-line'} rounded-b-md px-1.5 py-0.5"
						onpointerenter={() => (toggleHover = true)}
						onpointerleave={() => (toggleHover = false)}
						onpointerup={(e) => {
							e.stopPropagation();
							onExpandAll?.(false);
						}}
					>
						<span class="i-lucide-chevrons-down-up size-3 block"></span>
						{$i18n.t('Collapse all')}
					</div>
				{/if}
			</div>
		</div>
	{/if}

	{#if isDone && files.length > 0}
		<div class="mt-1 space-y-1">
			{#each files as file, idx}
				{#if file.startsWith('data:image/')}
					<Image src={file} alt="Image" />
				{/if}
			{/each}
		</div>
	{/if}
</div>

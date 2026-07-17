<script lang="ts">
	import { v4 as uuidv4 } from 'uuid';

	import { getContext, type Snippet } from 'svelte';
	const i18n = getContext('i18n');

	import dayjs from '$lib/dayjs';
	import duration from 'dayjs/plugin/duration';
	import relativeTime from 'dayjs/plugin/relativeTime';

	dayjs.extend(duration);
	dayjs.extend(relativeTime);

	async function loadLocale(locales) {
		for (const locale of locales) {
			try {
				dayjs.locale(locale);
				break; // Stop after successfully loading the first available locale
			} catch (error) {
				console.error(`Could not load locale '${locale}':`, error);
			}
		}
	}

	// Assuming $i18n.languages is an array of language codes
	$effect(() => {
		loadLocale($i18n.languages);
	});

	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';

	import ChevronUp from '../icons/ChevronUp.svelte';
	import ChevronDown from '../icons/ChevronDown.svelte';
	import CheckCircle from '../icons/CheckCircle.svelte';
	import Spinner from './Spinner.svelte';
	import Markdown from '../chat/Messages/Markdown.svelte';
	import HljsCode from './HljsCode.svelte';
	import Image from './Image.svelte';

	import {
		classifyToolResult,
		formatJSONString,
		parseToolArgumentsStreaming
	} from '$lib/utils/tool-render';

	let {
		open = $bindable(false),
		className = '',
		buttonClassName = 'w-fit text-content-muted hover:text-content transition',
		id = '',
		title = null,
		attributes = null,
		chevron = false,
		grow = false,
		disabled = false,
		hide = false,
		onChange = undefined,
		children,
		content
	}: {
		open?: boolean;
		className?: string;
		buttonClassName?: string;
		id?: string;
		title?: string | null;
		attributes?: Record<string, any> | null;
		chevron?: boolean;
		grow?: boolean;
		disabled?: boolean;
		hide?: boolean;
		onChange?: (open: boolean) => void;
		children?: Snippet;
		content?: Snippet;
	} = $props();

	// Switch.svelte と同じガード: 初期値では発火させず、
	// open が実際に変化したときだけ onChange を呼ぶ。
	let lastOpen = open;
	$effect(() => {
		if (open !== lastOpen) {
			lastOpen = open;
			onChange?.(open);
		}
	});

	const collapsibleId = uuidv4();

	// llens(#17): reasoning / tool 結果を下端からも閉じられるようにする
	// (開閉ボタンがヘッダにしか無いと、長い展開の末尾から先頭へ戻る必要がある)
	let rootElement: HTMLElement | null = $state(null);
	const showBottomCollapse = $derived(['reasoning', 'tool_calls'].includes(attributes?.type));

	const collapseFromBottom = () => {
		open = false;
		requestAnimationFrame(() => {
			rootElement?.scrollIntoView({ block: 'nearest' });
		});
	};
</script>

<div {id} class={className} bind:this={rootElement}>
	{#if title !== null}
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<!-- svelte-ignore a11y_click_events_have_key_events -->
		<div
			class="{buttonClassName} cursor-pointer"
			onpointerup={() => {
				if (!disabled) {
					open = !open;
				}
			}}
		>
			<div
				class=" w-full font-medium flex items-center justify-between gap-2 {attributes?.done &&
				attributes?.done !== 'true'
					? 'shimmer'
					: ''}
			"
			>
				{#if attributes?.done && attributes?.done !== 'true'}
					<div>
						<Spinner className="size-4" />
					</div>
				{:else if attributes?.type === 'tool_calls' && attributes?.done === 'true'}
					<div class="text-green-500 dark:text-green-400 shrink-0">
						<CheckCircle className="size-4" strokeWidth="2" />
					</div>
				{:else if attributes?.type === 'reasoning' && attributes?.done === 'true'}
					<div class="text-blue-500 dark:text-blue-400 shrink-0">
						<CheckCircle className="size-4" strokeWidth="2" />
					</div>
				{/if}

				<div class="">
					{#if attributes?.type === 'reasoning'}
						<!-- duration は 0 秒がありうる(数値 0 / 文字列 "0" とも thought 扱い) -->
						{#if attributes?.done === 'true' && attributes?.duration != null}
							{#if attributes.duration < 60}
								{$i18n.t('Thought for {{DURATION}} seconds', {
									DURATION: attributes.duration
								})}
							{:else}
								{$i18n.t('Thought for {{DURATION}}', {
									DURATION: dayjs.duration(attributes.duration, 'seconds').humanize()
								})}
							{/if}
						{:else}
							{$i18n.t('Thinking...')}
						{/if}
					{:else if attributes?.type === 'tool_calls'}
						{#if attributes?.done === 'true'}
							<Markdown
								id={`${collapsibleId}-tool-calls-${attributes?.id}`}
								content={$i18n.t('Tool used **{{NAME}}**', {
									NAME: attributes.name
								})}
							/>
						{:else}
							<Markdown
								id={`${collapsibleId}-tool-calls-${attributes?.id}-executing`}
								content={$i18n.t('Executing **{{NAME}}**...', {
									NAME: attributes.name
								})}
							/>
						{/if}
					{:else}
						{title}
					{/if}
				</div>

				<div class="flex self-center translate-y-[1px]">
					{#if open}
						<ChevronUp strokeWidth="3.5" className="size-3.5" />
					{:else}
						<ChevronDown strokeWidth="3.5" className="size-3.5" />
					{/if}
				</div>
			</div>
		</div>
	{:else}
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<!-- svelte-ignore a11y_click_events_have_key_events -->
		<div
			class="{buttonClassName} cursor-pointer"
			onpointerup={() => {
				if (!disabled) {
					open = !open;
				}
			}}
		>
			<div>
				<div class="flex items-start justify-between">
					{@render children?.()}

					{#if chevron}
						<div class="flex self-start translate-y-1">
							{#if open}
								<ChevronUp strokeWidth="3.5" className="size-3.5" />
							{:else}
								<ChevronDown strokeWidth="3.5" className="size-3.5" />
							{/if}
						</div>
					{/if}
				</div>

				{#if grow}
					{#if open && !hide}
						<div
							transition:slide={{ duration: 300, easing: quintOut, axis: 'y' }}
							onpointerup={(e) => {
								e.stopPropagation();
							}}
						>
							{@render content?.()}
						</div>
					{/if}
				{/if}
			</div>
		</div>
	{/if}

	{#if attributes?.type === 'tool_calls'}
		<!-- content_blocks 経路(#17 L0)は生の文字列/配列がそのまま来る
		     (旧 details 経路の entity decode は撤去 — 旧データ互換なし)。 -->
		{@const args = attributes?.arguments ?? ''}
		{@const result = attributes?.result ?? ''}
		{@const files = Array.isArray(attributes?.files) ? attributes.files : []}

		{#if !grow}
			{#if open && !hide}
				<div transition:slide={{ duration: 300, easing: quintOut, axis: 'y' }}>
					<div class="border-s-2 border-gray-200 dark:border-gray-800 ps-3">
						{#if attributes?.type === 'tool_calls'}
							<!-- 引数/結果は構造データ。バックエンドが型宣言から同梱した
						     ディスクリプタ(attributes.render、#20)の通りに引数単位で描画する。
						     宣言なしは型に忠実(scalar は key: value、object は JSON)。
						     ストリーミング中の未閉鎖 JSON は寛容パーサで部分値を逐次描画
						     (#20 ステップ5)。object 以外の壊れ方のみ一枚岩フォールバック。 -->
							{@const argDisplays = parseToolArgumentsStreaming(args, attributes?.render?.args)}
							<!-- Arguments / Result は tab 型バッジ + 枠線で一体化して区切る
							     (tool call が連続したときの境界の視認性)。バッジは上側だけ
							     rounded・下辺なしで枠の上辺に -mb-px で重ね、枠は四隅 rounded。 -->
							{#if argDisplays === null || argDisplays.length > 0}
								<div class="mt-2">
									<div class="flex ms-2">
										<span
											class="relative z-[1] -mb-px inline-flex items-center gap-1 text-xs font-medium text-content-muted bg-gray-100 dark:bg-gray-850 border border-b-0 border-line rounded-t-md px-1.5 py-0.5"
										>
											<span class="i-lucide-braces size-3 block text-sky-500"></span>
											Arguments
										</span>
									</div>
									<div class="border border-line rounded-lg px-2 py-1">
										{#if argDisplays === null}
											<HljsCode lang="json" code={formatJSONString(args)} />
										{:else}
											{#each argDisplays as arg (arg.name)}
												{#if arg.kind === 'scalar'}
													<div class="text-s font-mono my-0.5 px-0.5" dir="ltr">
														<span class="text-content-muted">{arg.name}:</span>
														<span class="whitespace-pre-wrap">{arg.value}</span>
													</div>
												{:else}
													<div class="text-content-muted text-s mt-1 mb-0.5 font-mono">
														{arg.name}
													</div>
													<HljsCode
														lang={arg.kind === 'code' ? (arg.language ?? 'plaintext') : 'json'}
														code={arg.value}
													/>
												{/if}
											{/each}
										{/if}
									</div>
								</div>
							{/if}
							{#if attributes?.done === 'true'}
								{@const resultDisplay = classifyToolResult(result, attributes?.render?.result)}
								<div class="mt-2 mb-1">
									<div class="flex ms-2">
										<span
											class="relative z-[1] -mb-px inline-flex items-center gap-1 text-xs font-medium text-content-muted bg-gray-100 dark:bg-gray-850 border border-b-0 border-line rounded-t-md px-1.5 py-0.5"
										>
											<span class="i-lucide-corner-down-left size-3 block text-emerald-500"></span>
											Result
										</span>
									</div>
									<div class="border border-line rounded-lg px-2 py-1">
										{#if resultDisplay.kind === 'skill'}
											<!-- load_skill の構造化結果(#35): 指示本文(markdown ハイライト)+ 導入 tool 一覧 -->
											<HljsCode lang="markdown" code={resultDisplay.value} />
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
											<div class="text-s font-mono whitespace-pre-wrap my-0.5 px-0.5" dir="ltr">
												{resultDisplay.value}
											</div>
										{:else if resultDisplay.kind === 'code'}
											<HljsCode lang={resultDisplay.language} code={resultDisplay.value} />
										{:else}
											<HljsCode lang="json" code={resultDisplay.value} />
										{/if}
									</div>
								</div>
							{/if}
						{:else}
							{@render content?.()}
						{/if}
					</div>

					{#if showBottomCollapse}
						<!-- svelte-ignore a11y_no_static_element_interactions -->
						<!-- svelte-ignore a11y_click_events_have_key_events -->
						<div
							class="{buttonClassName} font-medium flex items-center gap-2 py-0.5 cursor-pointer"
							onpointerup={(e) => {
								e.stopPropagation();
								collapseFromBottom();
							}}
						>
							<ChevronUp strokeWidth="3.5" className="size-3.5" />
							{$i18n.t('Close')}
						</div>
					{/if}
				</div>
			{/if}

			{#if attributes?.done === 'true'}
				{#if typeof files === 'object'}
					{#each files ?? [] as file, idx}
						{#if file.startsWith('data:image/')}
							<Image
								id={`${collapsibleId}-tool-calls-${attributes?.id}-result-${idx}`}
								src={file}
								alt="Image"
							/>
						{/if}
					{/each}
				{/if}
			{/if}
		{/if}
	{:else if !grow}
		{#if open && !hide}
			<div transition:slide={{ duration: 300, easing: quintOut, axis: 'y' }}>
				{@render content?.()}

				{#if showBottomCollapse}
					<!-- svelte-ignore a11y_no_static_element_interactions -->
					<!-- svelte-ignore a11y_click_events_have_key_events -->
					<div
						class="{buttonClassName} font-medium flex items-center gap-2 py-0.5 cursor-pointer"
						onpointerup={(e) => {
							e.stopPropagation();
							collapseFromBottom();
						}}
					>
						<ChevronUp strokeWidth="3.5" className="size-3.5" />
						{$i18n.t('Close')}
					</div>
				{/if}
			</div>
		{/if}
	{/if}
</div>

<script lang="ts">
	import { decode } from 'html-entities';
	import { v4 as uuidv4 } from 'uuid';

	import { getContext, createEventDispatcher, onMount } from 'svelte';
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
	$: loadLocale($i18n.languages);

	const dispatch = createEventDispatcher();

	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';

	import ChevronUp from '../icons/ChevronUp.svelte';
	import ChevronDown from '../icons/ChevronDown.svelte';
	import Spinner from './Spinner.svelte';
	import CodeBlock from '../chat/Messages/CodeBlock.svelte';
	import Markdown from '../chat/Messages/Markdown.svelte';
	import Image from './Image.svelte';

	export let open = false;

	// Switch.svelte と同じ Svelte 5 互換モード対策: 初期化時の $: dispatch が
	// 親に届くため、open が実際に変化したときだけ change を発火する。
	let lastOpen = open;
	$: if (open !== lastOpen) {
		lastOpen = open;
		dispatch('change', open);
	}

	export let className = '';
	export let buttonClassName =
		'w-fit text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition';

	export let id = '';
	export let title = null;
	export let attributes = null;

	export let chevron = false;
	export let grow = false;

	export let disabled = false;
	export let hide = false;

	const collapsibleId = uuidv4();

	// llens(#17): reasoning / tool 結果を下端からも閉じられるようにする
	// (開閉ボタンがヘッダにしか無いと、長い展開の末尾から先頭へ戻る必要がある)
	let rootElement: HTMLElement | null = null;
	$: showBottomCollapse = ['reasoning', 'tool_calls', 'code_interpreter'].includes(
		attributes?.type
	);

	const collapseFromBottom = () => {
		open = false;
		requestAnimationFrame(() => {
			rootElement?.scrollIntoView({ block: 'nearest' });
		});
	};

	function parseJSONString(str) {
		try {
			return parseJSONString(JSON.parse(str));
		} catch (e) {
			return str;
		}
	}

	function formatJSONString(str) {
		try {
			const parsed = parseJSONString(str);
			// If parsed is an object/array, then it's valid JSON
			if (typeof parsed === 'object') {
				return JSON.stringify(parsed, null, 2);
			} else {
				// It's a primitive value like a number, boolean, etc.
				return `${JSON.stringify(String(parsed))}`;
			}
		} catch (e) {
			// Not valid JSON, return as-is
			return str;
		}
	}
</script>

<div {id} class={className} bind:this={rootElement}>
	{#if title !== null}
		<!-- svelte-ignore a11y-no-static-element-interactions -->
		<!-- svelte-ignore a11y-click-events-have-key-events -->
		<div
			class="{buttonClassName} cursor-pointer"
			on:pointerup={() => {
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
					{:else if attributes?.type === 'code_interpreter'}
						{#if attributes?.done === 'true'}
							{$i18n.t('Analyzed')}
						{:else}
							{$i18n.t('Analyzing...')}
						{/if}
					{:else if attributes?.type === 'tool_calls'}
						{#if attributes?.done === 'true'}
							<Markdown
								id={`${collapsibleId}-tool-calls-${attributes?.id}`}
								content={$i18n.t('View Result from **{{NAME}}**', {
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
		<!-- svelte-ignore a11y-no-static-element-interactions -->
		<!-- svelte-ignore a11y-click-events-have-key-events -->
		<div
			class="{buttonClassName} cursor-pointer"
			on:pointerup={() => {
				if (!disabled) {
					open = !open;
				}
			}}
		>
			<div>
				<div class="flex items-start justify-between">
					<slot />

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
							on:pointerup={(e) => {
								e.stopPropagation();
							}}
						>
							<slot name="content" />
						</div>
					{/if}
				{/if}
			</div>
		</div>
	{/if}

	{#if attributes?.type === 'tool_calls'}
		{@const args = decode(attributes?.arguments)}
		{@const result = decode(attributes?.result ?? '')}
		{@const files = parseJSONString(decode(attributes?.files ?? ''))}

		{#if !grow}
			{#if open && !hide}
				<div transition:slide={{ duration: 300, easing: quintOut, axis: 'y' }}>
					{#if attributes?.type === 'tool_calls'}
						{#if attributes?.done === 'true'}
							<Markdown
								id={`${collapsibleId}-tool-calls-${attributes?.id}-result`}
								content={`> \`\`\`json
> ${formatJSONString(args)}
> ${formatJSONString(result)}
> \`\`\``}
							/>
						{:else}
							<Markdown
								id={`${collapsibleId}-tool-calls-${attributes?.id}-result`}
								content={`> \`\`\`json
> ${formatJSONString(args)}
> \`\`\``}
							/>
						{/if}
					{:else}
						<slot name="content" />
					{/if}

					{#if showBottomCollapse}
						<!-- svelte-ignore a11y-no-static-element-interactions -->
						<!-- svelte-ignore a11y-click-events-have-key-events -->
						<div
							class="{buttonClassName} font-medium flex items-center gap-2 py-0.5 cursor-pointer"
							on:pointerup|stopPropagation={collapseFromBottom}
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
				<slot name="content" />

				{#if showBottomCollapse}
					<!-- svelte-ignore a11y-no-static-element-interactions -->
					<!-- svelte-ignore a11y-click-events-have-key-events -->
					<div
						class="{buttonClassName} font-medium flex items-center gap-2 py-0.5 cursor-pointer"
						on:pointerup|stopPropagation={collapseFromBottom}
					>
						<ChevronUp strokeWidth="3.5" className="size-3.5" />
						{$i18n.t('Close')}
					</div>
				{/if}
			</div>
		{/if}
	{/if}
</div>

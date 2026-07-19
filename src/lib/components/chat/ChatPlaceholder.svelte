<script lang="ts">
	import { DEFAULT_MODEL_IMAGE_URL } from '$lib/constants';
	import { render } from '$lib/utils/markdown-it';
	import DOMPurify from 'dompurify';

	import { user, models as _models } from '$lib/stores';
	import { onMount, getContext } from 'svelte';

	import { fade } from 'svelte/transition';

	import Suggestions from './Suggestions.svelte';
	import { sanitizeResponseContent } from '$lib/utils';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n = getContext('i18n');

	let {
		modelIds = [],
		atSelectedModel,
		submitPrompt
	}: {
		modelIds?: string[];
		atSelectedModel?: any;
		submitPrompt: (prompt: string) => void | Promise<void>;
	} = $props();

	const models = $derived(modelIds.map((id) => $_models.find((m) => m.id === id)));

	let mounted = $state(false);
	let selectedModelIdx = $state(0);

	$effect(() => {
		if (modelIds.length > 0) {
			selectedModelIdx = models.length - 1;
		}
	});

	onMount(() => {
		mounted = true;
	});
</script>

{#key mounted}
	<div class="m-auto w-full max-w-6xl px-8 lg:px-20">
		<div class="flex justify-start">
			<div class="flex -space-x-4 mb-0.5" in:fade={{ duration: 200 }}>
				{#each models as model, modelIdx (modelIdx)}
					<button
						onclick={() => {
							selectedModelIdx = modelIdx;
						}}
					>
						<Tooltip
							content={render(
								sanitizeResponseContent(models[selectedModelIdx]?.info?.meta?.description ?? '')
							)}
							placement="right"
						>
							<img
								crossorigin="anonymous"
								src={model?.info?.meta?.profile_image_url ?? DEFAULT_MODEL_IMAGE_URL}
								class=" size-[2.7rem] rounded-full border border-line"
								alt="logo"
								draggable="false"
							/>
						</Tooltip>
					</button>
				{/each}
			</div>
		</div>

		<div
			class=" mt-2 mb-4 text-3xl text-content font-medium text-left flex items-center gap-4 font-primary"
		>
			<div>
				<div class=" capitalize line-clamp-1" in:fade={{ duration: 200 }}>
					{#if models[selectedModelIdx]?.name}
						{models[selectedModelIdx]?.name}
					{:else}
						{$i18n.t('Hello, {{name}}', { name: $user?.name })}
					{/if}
				</div>

				<div in:fade={{ duration: 200, delay: 200 }}>
					{#if models[selectedModelIdx]?.info?.meta?.description ?? null}
						<div class="mt-0.5 text-base font-normal text-content-muted line-clamp-3 markdown">
							<!-- llens(GHSA-gf5m-wcrh-7928): モデル説明の XSS 対策。marked 生成 HTML を
							     DOMPurify で無害化(javascript: リンク等を除去)。 -->
							<!-- eslint-disable-next-line svelte/no-at-html-tags -- #17 XSS sink 監査済み(サニタイズは各経路) -->
							{@html DOMPurify.sanitize(
								render(
									sanitizeResponseContent(models[selectedModelIdx]?.info?.meta?.description ?? '')
								)
							)}
						</div>
						{#if models[selectedModelIdx]?.info?.meta?.user}
							<div class="mt-0.5 text-sm font-normal text-content-subtle">
								By
								{models[selectedModelIdx]?.info?.meta?.user?.name
									? models[selectedModelIdx]?.info?.meta?.user?.name
									: `@${models[selectedModelIdx]?.info?.meta?.user?.username}`}
							</div>
						{/if}
					{:else}
						<div class=" font-medium text-content-subtle line-clamp-1 font-p">
							{$i18n.t('How can I help you today?')}
						</div>
					{/if}
				</div>
			</div>
		</div>

		<div class=" w-full font-primary" in:fade={{ duration: 200, delay: 300 }}>
			<!-- llens(#70): Suggestions は callback prop 化済み — 旧 on:select のままだと
			     クリックが無反応になる(dispatcher 移行の取り残しバグを修正) -->
			<Suggestions
				className="grid grid-cols-2"
				suggestionPrompts={atSelectedModel?.info?.meta?.suggestion_prompts ??
					models[selectedModelIdx]?.info?.meta?.suggestion_prompts ??
					[]}
				onSelect={(content) => {
					submitPrompt(content);
				}}
			/>
		</div>
	</div>
{/key}

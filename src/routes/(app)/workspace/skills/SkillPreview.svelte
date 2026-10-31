<script lang="ts">
	// skill の編集画面のプレビュー。
	// 編集中の本文を自分として展開し、before → 本文 → after の並びで見せる (POST /skills/preview —
	// 読み込みと同じ展開の関数。before / after は実行せず、何を実行するかの一覧だけ)。
	import { getContext, onMount } from 'svelte';

	import { models } from '$lib/stores';
	import { previewSkill, type SkillPreview } from '$lib/apis/skills';
	import { getPromptVariables } from '$lib/utils';
	import { estimateTokens } from '$lib/utils/prompt-preview';
	import ExpandedText from '$lib/components/workspace/common/ExpandedText.svelte';
	import PreviewLegend from '$lib/components/workspace/common/PreviewLegend.svelte';

	const i18n = getContext('i18n');

	let {
		skillId = '',
		content,
		before,
		after
	}: {
		skillId?: string;
		// 編集中の本文と before / after (JSON の配列の文字列。空欄は無し)
		content: () => string;
		before: () => string;
		after: () => string;
	} = $props();

	// {{BASE_MODEL_ID}}・{{SKILLS}}・{{USER_RECIPES}} の元にする preset。既定はこの skill を割り当てた
	// 最初の preset(無ければ無し — それらは Unknown / 空)
	const presets = $derived(
		($models ?? []).filter((m: any) => (m?.info?.meta?.skillIds ?? []).includes(skillId))
	);
	let modelId = $state('');

	let preview = $state<SkillPreview | null>(null);
	let loading = $state(false);
	let error = $state('');

	const parseHooks = (text: string, label: string): unknown[] => {
		if (text.trim() === '') return [];
		let value: unknown;
		try {
			value = JSON.parse(text);
		} catch {
			throw $i18n.t('{{name}} must be a JSON array.', { name: label });
		}
		if (!Array.isArray(value)) throw $i18n.t('{{name}} must be a JSON array.', { name: label });
		return value;
	};

	const load = async () => {
		loading = true;
		error = '';
		try {
			preview = await previewSkill({
				content: content(),
				before: parseHooks(before(), $i18n.t('Run before')),
				after: parseHooks(after(), $i18n.t('Run after')),
				model_id: modelId || null,
				// 日時の TZ (チャットの送信と同じ)
				variables: getPromptVariables()
			});
		} catch (e) {
			preview = null;
			error = `${e}`;
		}
		loading = false;
	};

	onMount(() => {
		modelId = presets[0]?.id ?? '';
		load();
	});
</script>

{#snippet hookList(entries: SkillPreview['before'], label: string)}
	{#if entries.length > 0}
		<section class="flex flex-col gap-1">
			<div class="font-semibold">{label}</div>
			{#each entries as entry (entry.n)}
				<div class="rounded-lg border border-line px-2 py-1 min-w-0">
					<div class="flex items-center gap-2 text-xs">
						<span class="text-content-subtle">{entry.n}.</span>
						<span class="font-mono font-medium">{entry.tool}</span>
					</div>
					<pre
						class="mt-0.5 whitespace-pre-wrap break-all font-mono text-xs text-content-muted">{JSON.stringify(
							entry.args ?? {},
							null,
							2
						)}</pre>
				</div>
			{/each}
		</section>
	{/if}
{/snippet}

<div class="flex flex-col gap-3 text-sm">
	<div class="flex flex-wrap items-center gap-2">
		<label class="text-xs text-content-muted" for="skill-preview-model">
			{$i18n.t('Model for the variables')}
		</label>
		<select
			id="skill-preview-model"
			class="min-w-0 max-w-full text-xs px-2 py-1 bg-surface-form rounded-xs outline-hidden"
			bind:value={modelId}
			onchange={load}
		>
			<option value="">{$i18n.t('None')}</option>
			{#each presets as preset (preset.id)}
				<option value={preset.id}>{preset.name}</option>
			{/each}
		</select>
		<button
			class="ml-auto shrink-0 flex items-center gap-1 px-2 py-1 rounded-lg text-xs text-content-muted hover:text-content hover:bg-surface-hover"
			type="button"
			disabled={loading}
			onclick={load}
		>
			<span class="i-lucide-refresh-cw size-3.5 block {loading ? 'animate-spin' : ''}"></span>
			{$i18n.t('Refresh')}
		</button>
	</div>
	<div class="text-xs text-content-muted">
		{$i18n.t(
			'Expanded as you, as it is put into the chat when loaded: the results of Run before, the skill content, then the results of Run after. Run before / Run after are not run here; only what would run is listed. Without a model, {{baseModel}} is Unknown and {{skills}} / {{recipes}} are empty.',
			{ baseModel: '{{BASE_MODEL_ID}}', skills: '{{SKILLS}}', recipes: '{{USER_RECIPES}}' }
		)}
	</div>

	<PreviewLegend />

	{#if error}
		<div class="text-xs text-red-600 dark:text-red-400">
			{$i18n.t('Could not load the preview: {{error}}', { error })}
		</div>
	{:else if preview}
		{@render hookList(preview.before, $i18n.t('Run before'))}
		<section class="flex flex-col gap-1">
			<div class="flex flex-wrap items-baseline gap-x-2">
				<div class="font-semibold">{$i18n.t('Skill Content')}</div>
				<div class="text-xs text-content-muted">
					{$i18n.t('{{chars}} chars · ~{{tokens}} tokens', {
						chars: preview.content.chars.toLocaleString(),
						tokens: estimateTokens(preview.content.text).toLocaleString()
					})}
				</div>
			</div>
			<div class="rounded-lg border border-line px-2 py-1.5">
				<ExpandedText value={preview.content} />
			</div>
		</section>
		{@render hookList(preview.after, $i18n.t('Run after'))}
	{:else if loading}
		<div class="flex items-center gap-2 text-xs text-content-muted">
			<span class="i-lucide-loader-circle size-4 block animate-spin"></span>
			{$i18n.t('Loading...')}
		</div>
	{/if}
</div>

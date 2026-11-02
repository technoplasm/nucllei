<script lang="ts">
	import { getContext } from 'svelte';
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { render } from '$lib/utils/markdown-it';

	const i18n = getContext('i18n');

	const helpText: Record<string, string> = {
		vision: $i18n.t('Model accepts image inputs'),
		audio: $i18n.t(
			'Model hears audio: attached audio (except microphone dictation recordings) is sent as audio, and the feed_audio tool is offered. Turn this on only for models whose API accepts audio input — other APIs reject the whole request'
		),
		usage: $i18n.t(
			'Request measured usage (token consumption) via stream_options.include_usage. Turn this off only for OpenAI-compatible APIs that reject it as an unknown parameter'
		),
		preserve_reasoning: $i18n.t(
			"Resend this turn's reasoning (reasoning_content) on the follow-up requests of the tool loop, so the model keeps its own thinking across tool calls. Turn this off only for OpenAI-compatible APIs that reject or misread reasoning_content"
		)
	};

	// キーは vision / audio / usage / preserve_reasoning(usage はベースモデル依存でキーごと消える)。
	// audio は既定 OFF(backend の is_audio_model と対 — 音声を受けない API はリクエストごと落とす)。
	// preserve_reasoning は既定 ON(backend の is_reasoning_preserved と対 — 2026-10-19)。
	// llens(#70): 機能「スキル」(meta.features.skills、既定 ON)のトグルも先頭に置く。
	// OFF でこのモデルの manifest(load_skill)と /command 発火が両方止まる。
	// llens(#140): 旧「サブエージェント」トグル(meta.features.subagent)は撤去 —
	// builtin_subagent を toolIds に入れるか(ToolsSelector)に吸収した。
	// 機能「レシピ」(meta.features.recipes、既定 ON)。
	// ON でこのモデルに load_recipe / save_recipe / edit_recipe と見えるレシピの一覧が入る
	// (backend の is_recipes_feature_enabled。sandbox の無い配備では入らない)。
	let {
		capabilities = $bindable({}),
		featureSkills = $bindable(true),
		featureRecipes = $bindable(true)
	}: {
		capabilities?: Record<string, boolean | undefined>;
		featureSkills?: boolean;
		featureRecipes?: boolean;
	} = $props();
</script>

<div>
	<div class="flex w-full justify-between mb-1">
		<div class=" self-center text-sm font-semibold">{$i18n.t('Capabilities')}</div>
	</div>
	<!-- 狭い幅では折り返す(折り返さないと 390px で項目名が 1 文字ずつ縦に潰れる) -->
	<div class="flex flex-wrap gap-y-1">
		<!-- llens(#49): label で行クリックトグル -->
		<label class=" flex items-center gap-2 mr-3 cursor-pointer">
			<span class="flex items-center">
				<Checkbox
					state={featureSkills ? 'checked' : 'unchecked'}
					onChange={(state) => {
						featureSkills = state === 'checked';
					}}
				/>
			</span>

			<div class=" py-0.5 text-sm capitalize">
				<Tooltip
					content={$i18n.t('Expose the skill list to the model (load_skill) + /command activation')}
				>
					{$i18n.t('Skills')}
				</Tooltip>
			</div>
		</label>

		<label class=" flex items-center gap-2 mr-3 cursor-pointer">
			<span class="flex items-center">
				<Checkbox
					state={featureRecipes ? 'checked' : 'unchecked'}
					onChange={(state) => {
						featureRecipes = state === 'checked';
					}}
				/>
			</span>

			<div class=" py-0.5 text-sm capitalize">
				<Tooltip
					content={$i18n.t(
						'Give the model the recipe tools (load_recipe / save_recipe / edit_recipe) with the list of recipes the user can see. Needs the sandbox'
					)}
				>
					{$i18n.t('Recipes')}
				</Tooltip>
			</div>
		</label>

		{#each Object.keys(capabilities) as capability}
			<!-- llens(#49): label で行クリックトグル -->
			<label class=" flex items-center gap-2 mr-3 cursor-pointer">
				<span class="flex items-center">
					<Checkbox
						state={capabilities[capability] ? 'checked' : 'unchecked'}
						onChange={(state) => {
							capabilities[capability] = state === 'checked';
						}}
					/>
				</span>

				<div class=" py-0.5 text-sm capitalize">
					<Tooltip content={render(helpText[capability])}>
						{$i18n.t(capability)}
					</Tooltip>
				</div>
			</label>
		{/each}
	</div>
</div>

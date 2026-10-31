<script lang="ts" module>
	import { getRecipes as fetchRecipes, type RecipeList } from '$lib/apis/recipes';

	// 一覧は利用者ごと・置き場の中身で変わるので長くは持たない(補完を開き直すたびに聞かない程度)
	const RECIPES_TTL_MS = 30_000;
	let recipesCache: { at: number; list: RecipeList } | null = null;

	const getCachedRecipes = async (): Promise<RecipeList> => {
		if (recipesCache && Date.now() - recipesCache.at < RECIPES_TTL_MS) {
			return recipesCache.list;
		}
		const list = await fetchRecipes();
		recipesCache = { at: Date.now(), list };
		return list;
	};
</script>

<script lang="ts">
	import { skills } from '$lib/stores';

	import { removeLastWordFromString } from '$lib/utils';
	import { getSkills } from '$lib/apis/skills';
	import type { RecipeItem } from '$lib/apis/recipes';

	import SlashCommands from './Commands/SlashCommands.svelte';
	import Models from './Commands/Models.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	let {
		prompt = $bindable(''),
		// llens(#42): 選択モデルに紐づく skill id(補完のスコープ)。null は無制限(後方互換)
		allowedSkillIds = null,
		// 選んでいるモデルのどれかで能力「レシピ」が ON か(OFF ならレシピは補完に出さない)
		recipesEnabled = false,
		onSelect = () => {}
	}: {
		prompt?: string;
		allowedSkillIds?: string[] | null;
		recipesEnabled?: boolean;
		onSelect?: (data: { type: string; data: any }) => void;
	} = $props();

	// 見えるレシピの一覧。`/` を打つたびに sandbox へ聞かないよう、少しの間だけ使い回す
	let recipes = $state<RecipeItem[] | null>(null);
	let recipesError = $state(false);
	// `/` で発火できる skill(持ち主か read 可視)。一覧の API が返す — 取れないときは null(絞らない)
	let invocableSkillIds = $state<string[] | null>(null);

	let loading = $state(false);
	let commandElement = $state<any>(null);

	export const selectUp = () => {
		commandElement?.selectUp();
	};

	export const selectDown = () => {
		commandElement?.selectDown();
	};

	const command = $derived(prompt?.split('\n').pop()?.split(' ')?.pop() ?? '');

	// llens(issue #35): `/command` はサーバが「入力全体の先頭行の行頭」でしか発火しない。
	// そのため skill picker は入力全体が最初のトークン(= `/` に続く command)編集中の
	// ときだけ出す。従来の「最終行の最終単語」判定だとメッセージ途中の `/` でも picker が
	// 出てしまい、サーバの実挙動と乖離する。@(model) は従来判定のまま。
	// llens(#52): #(knowledge) picker は RAG 撤去に伴い廃止。
	// レシピの名前 (フォルダ名) は日本語なども使うので、`/` から空白までの 1 語の編集中に出す。
	const showSkills = $derived(/^\/\S*$/.test(prompt ?? ''));

	const menuSkills = $derived(
		($skills ?? []).filter(
			(s) =>
				(allowedSkillIds === null || allowedSkillIds.includes(s.id)) &&
				(invocableSkillIds === null || invocableSkillIds.includes(s.id))
		)
	);

	const show = $derived(showSkills || command?.charAt(0) === '@');

	$effect(() => {
		if (show) {
			init();
		}
	});

	const init = async () => {
		loading = true;
		const [skillList] = await Promise.all([getSkills(), recipesEnabled ? loadRecipes() : null]);
		skills.set(skillList);
		if (!recipesEnabled) {
			recipes = null;
			recipesError = false;
			invocableSkillIds = null;
		}
		loading = false;
	};

	const loadRecipes = async () => {
		try {
			const list = await getCachedRecipes();
			recipes = list.available && !list.error ? list.recipes : null;
			recipesError = list.available && !!list.error;
			invocableSkillIds = list.skill_ids ?? null;
		} catch {
			recipes = null;
			recipesError = true;
			invocableSkillIds = null;
		}
	};
</script>

{#if show}
	{#if !loading}
		{#if showSkills}
			<SlashCommands
				bind:this={commandElement}
				bind:prompt
				command={prompt}
				skills={menuSkills}
				{recipes}
				{recipesError}
			/>
		{:else if command?.charAt(0) === '@'}
			<Models
				bind:this={commandElement}
				{command}
				onSelect={(model) => {
					prompt = removeLastWordFromString(prompt, command);

					onSelect({
						type: 'model',
						data: model
					});
				}}
			/>
		{/if}
	{:else}
		<!-- 読み込み中は commands-container にしない(キー操作は普段どおり — Enter で選べる行が無いまま送らない) -->
		<div class="px-2 mb-2 text-left w-full absolute bottom-0 left-0 right-0 z-10">
			<div class="flex w-full rounded-xl border border-gray-100 dark:border-gray-850">
				<div
					class="max-h-60 flex flex-col w-full rounded-xl bg-white dark:bg-gray-900 text-content"
				>
					<Spinner />
				</div>
			</div>
		</div>
	{/if}
{/if}

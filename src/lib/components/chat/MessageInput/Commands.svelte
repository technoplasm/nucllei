<script>
	import { createEventDispatcher, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	const dispatch = createEventDispatcher();

	import { skills } from '$lib/stores';

	import { removeLastWordFromString } from '$lib/utils';
	import { getSkills } from '$lib/apis/skills';

	import Skills from './Commands/Skills.svelte';
	import Models from './Commands/Models.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	export let prompt = '';
	export let files = [];
	// llens(#42): 選択モデルに紐づく skill id(補完のスコープ)。null は無制限(後方互換)
	export let allowedSkillIds = null;

	let loading = false;
	let commandElement = null;

	export const selectUp = () => {
		commandElement?.selectUp();
	};

	export const selectDown = () => {
		commandElement?.selectDown();
	};

	let command = '';
	$: command = prompt?.split('\n').pop()?.split(' ')?.pop() ?? '';

	// llens(issue #35): `/command` はサーバが「入力全体の先頭行の行頭」でしか発火しない。
	// そのため skill picker は入力全体が最初のトークン(= `/` に続く command)編集中の
	// ときだけ出す。従来の「最終行の最終単語」判定だとメッセージ途中の `/` でも picker が
	// 出てしまい、サーバの実挙動と乖離する。@(model) は従来判定のまま。
	// llens(#52): #(knowledge) picker は RAG 撤去に伴い廃止。
	let showSkills = false;
	$: showSkills = /^\/[a-zA-Z0-9_-]*$/.test(prompt ?? '');

	let show = false;
	$: show = showSkills || command?.charAt(0) === '@';

	$: if (show) {
		init();
	}

	const init = async () => {
		loading = true;
		skills.set(await getSkills());
		loading = false;
	};
</script>

{#if show}
	{#if !loading}
		{#if showSkills}
			<Skills bind:this={commandElement} bind:prompt command={prompt} {allowedSkillIds} />
		{:else if command?.charAt(0) === '@'}
			<Models
				bind:this={commandElement}
				{command}
				on:select={(e) => {
					prompt = removeLastWordFromString(prompt, command);

					dispatch('select', {
						type: 'model',
						data: e.detail
					});
				}}
			/>
		{/if}
	{:else}
		<div
			id="commands-container"
			class="px-2 mb-2 text-left w-full absolute bottom-0 left-0 right-0 z-10"
		>
			<div class="flex w-full rounded-xl border border-gray-100 dark:border-gray-850">
				<div
					class="max-h-60 flex flex-col w-full rounded-xl bg-white dark:bg-gray-900 dark:text-gray-100"
				>
					<Spinner />
				</div>
			</div>
		</div>
	{/if}
{/if}

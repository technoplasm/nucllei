<script lang="ts">
	import { skills } from '$lib/stores';

	import { removeLastWordFromString } from '$lib/utils';
	import { getSkills } from '$lib/apis/skills';

	import Skills from './Commands/Skills.svelte';
	import Models from './Commands/Models.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	let {
		prompt = $bindable(''),
		// llens(#42): 選択モデルに紐づく skill id(補完のスコープ)。null は無制限(後方互換)
		allowedSkillIds = null,
		onSelect = () => {}
	}: {
		prompt?: string;
		allowedSkillIds?: string[] | null;
		onSelect?: (data: { type: string; data: any }) => void;
	} = $props();

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
	const showSkills = $derived(/^\/[a-zA-Z0-9_-]*$/.test(prompt ?? ''));

	const show = $derived(showSkills || command?.charAt(0) === '@');

	$effect(() => {
		if (show) {
			init();
		}
	});

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
		<div
			id="commands-container"
			class="px-2 mb-2 text-left w-full absolute bottom-0 left-0 right-0 z-10"
		>
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

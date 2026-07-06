<script>
	import { createEventDispatcher, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	const dispatch = createEventDispatcher();

	import { knowledge, skills } from '$lib/stores';

	import { removeLastWordFromString } from '$lib/utils';
	import { getSkills } from '$lib/apis/skills';
	import { getKnowledgeBases } from '$lib/apis/knowledge';

	import Skills from './Commands/Skills.svelte';
	import Knowledge from './Commands/Knowledge.svelte';
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
	// 出てしまい、サーバの実挙動と乖離する。#(knowledge)/@(model) は従来判定のまま。
	let showSkills = false;
	$: showSkills = /^\/[a-zA-Z0-9_-]*$/.test(prompt ?? '');

	let show = false;
	$: show = showSkills || ['#', '@'].includes(command?.charAt(0)) || '\\#' === command.slice(0, 2);

	$: if (show) {
		init();
	}

	const init = async () => {
		loading = true;
		await Promise.all([
			(async () => {
				skills.set(await getSkills(localStorage.token));
			})(),
			(async () => {
				knowledge.set(await getKnowledgeBases(localStorage.token));
			})()
		]);
		loading = false;
	};
</script>

{#if show}
	{#if !loading}
		{#if showSkills}
			<Skills bind:this={commandElement} bind:prompt command={prompt} {allowedSkillIds} />
		{:else if (command?.charAt(0) === '#' && command.startsWith('#') && !command.includes('# ')) || ('\\#' === command.slice(0, 2) && command.startsWith('#') && !command.includes('# '))}
			<Knowledge
				bind:this={commandElement}
				bind:prompt
				command={command.includes('\\#') ? command.slice(2) : command}
				on:youtube={(e) => {
					console.log(e);
					dispatch('upload', {
						type: 'youtube',
						data: e.detail
					});
				}}
				on:url={(e) => {
					console.log(e);
					dispatch('upload', {
						type: 'web',
						data: e.detail
					});
				}}
				on:select={(e) => {
					console.log(e);
					if (files.find((f) => f.id === e.detail.id)) {
						return;
					}

					files = [
						...files,
						{
							...e.detail,
							status: 'processed'
						}
					];

					dispatch('select');
				}}
			/>
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

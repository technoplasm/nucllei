<script lang="ts">
	import { skills, type Skill } from '$lib/stores';
	import { tick } from 'svelte';

	// picker は補完のみ(issue #35)。Prompts.svelte の変数展開・CLIPBOARD・{{}}選択ジャンプは
	// 一切持ち込まず、部分トークン(`/par` など)を `/command ` トークンに置き換えるだけ。
	let {
		prompt = $bindable(''),
		command = '', // `/` を含む先頭トークン(Commands.svelte から入力全体を渡す)
		// llens(#42): 選択モデルに紐づく skill のみ補完(null = 無制限)。サーバ側の発火条件
		//(モデルで機能有効 ∧ skillIds 紐づけ ∧ read 可視)と一致させる
		allowedSkillIds = null
	}: {
		prompt?: string;
		command?: string;
		allowedSkillIds?: string[] | null;
	} = $props();

	let selectedIdx = $state(0);

	// command の `/` を除いた部分で command 前方(部分)一致フィルタ。
	const filteredSkills = $derived(
		($skills ?? [])
			.filter((s) => allowedSkillIds === null || allowedSkillIds.includes(s.id))
			.filter((s) => s.id.toLowerCase().includes(command.slice(1).toLowerCase()))
			.sort((a, b) => a.name.localeCompare(b.name))
	);

	$effect(() => {
		if (command) {
			selectedIdx = 0;
		}
	});

	export const selectUp = () => {
		selectedIdx = Math.max(0, selectedIdx - 1);
	};

	export const selectDown = () => {
		selectedIdx = Math.min(selectedIdx + 1, filteredSkills.length - 1);
	};

	const confirmSkill = async (skill: Skill) => {
		// 先頭行の行頭トークンだけを `/command ` に置換(サーバの発火位置と一致)。
		// テンプレート全文挿入はしない(置換されるのは `/par` のような入力中の部分トークンのみ)。
		const token = `/${skill.id} `;

		const lines = prompt.split('\n');
		const firstLine = lines.shift() ?? '';
		const replacedFirst = firstLine.replace(/^\/[a-zA-Z0-9_-]*/, token);

		prompt = [replacedFirst, ...lines].join('\n');

		const chatInputElement = document.getElementById('chat-input');

		await tick();
		if (chatInputElement) {
			chatInputElement.focus();
			chatInputElement.dispatchEvent(new Event('input'));
		}
	};
</script>

{#if filteredSkills.length > 0}
	<div
		id="commands-container"
		class="px-2 mb-2 text-left w-full absolute bottom-0 left-0 right-0 z-10"
	>
		<div class="flex w-full rounded-xl border border-gray-100 dark:border-gray-850">
			<div class="max-h-60 flex flex-col w-full rounded-xl bg-white dark:bg-gray-900 text-content">
				<div class="m-1 overflow-y-auto p-1 space-y-0.5">
					{#each filteredSkills as skill, idx}
						<button
							class=" px-3 py-1.5 rounded-xl w-full text-left {idx === selectedIdx
								? '  bg-gray-50 dark:bg-gray-850 selected-command-option-button'
								: ''}"
							type="button"
							onclick={() => {
								confirmSkill(skill);
							}}
							onmousemove={() => {
								selectedIdx = idx;
							}}
							onfocus={() => {}}
						>
							<div class=" font-medium text-black text-content">
								/{skill.id}
							</div>

							<div class=" text-xs text-content-muted">
								{skill.name}
							</div>

							{#if skill.description}
								<div class=" text-xs text-content-subtle line-clamp-1">
									{skill.description}
								</div>
							{/if}
						</button>
					{/each}
				</div>
			</div>
		</div>
	</div>
{/if}

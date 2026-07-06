<script lang="ts">
	import { skills, settings } from '$lib/stores';
	import { tick } from 'svelte';

	// picker は補完のみ(issue #35)。Prompts.svelte の変数展開・CLIPBOARD・{{}}選択ジャンプは
	// 一切持ち込まず、部分トークン(`/par` など)を `/command ` トークンに置き換えるだけ。
	export let prompt = '';
	export let command = ''; // `/` を含む先頭トークン(Commands.svelte から入力全体を渡す)

	let selectedIdx = 0;
	let filteredSkills = [];

	// command の `/` を除いた部分で command 前方(部分)一致フィルタ。
	$: filteredSkills = ($skills ?? [])
		.filter((s) => s.command.toLowerCase().includes(command.slice(1).toLowerCase()))
		.sort((a, b) => a.title.localeCompare(b.title));

	$: if (command) {
		selectedIdx = 0;
	}

	export const selectUp = () => {
		selectedIdx = Math.max(0, selectedIdx - 1);
	};

	export const selectDown = () => {
		selectedIdx = Math.min(selectedIdx + 1, filteredSkills.length - 1);
	};

	const confirmSkill = async (skill) => {
		// 先頭行の行頭トークンだけを `/command ` に置換(サーバの発火位置と一致)。
		// テンプレート全文挿入はしない(置換されるのは `/par` のような入力中の部分トークンのみ)。
		const token = `/${skill.command} `;

		const lines = prompt.split('\n');
		const firstLine = lines.shift() ?? '';
		const replacedFirst = firstLine.replace(/^\/[a-zA-Z0-9_-]*/, token);

		// richTextInput(既定)は改行を <br/> で保持する。プレーン textarea は生改行。
		if ($settings?.richTextInput ?? true) {
			prompt = [replacedFirst, ...lines].join('<br/>');
		} else {
			prompt = [replacedFirst, ...lines].join('\n');
		}

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
			<div
				class="max-h-60 flex flex-col w-full rounded-xl bg-white dark:bg-gray-900 dark:text-gray-100"
			>
				<div class="m-1 overflow-y-auto p-1 space-y-0.5 scrollbar-hidden">
					{#each filteredSkills as skill, idx}
						<button
							class=" px-3 py-1.5 rounded-xl w-full text-left {idx === selectedIdx
								? '  bg-gray-50 dark:bg-gray-850 selected-command-option-button'
								: ''}"
							type="button"
							on:click={() => {
								confirmSkill(skill);
							}}
							on:mousemove={() => {
								selectedIdx = idx;
							}}
							on:focus={() => {}}
						>
							<div class=" font-medium text-black dark:text-gray-100">
								/{skill.command}
							</div>

							<div class=" text-xs text-gray-600 dark:text-gray-100">
								{skill.title}
							</div>

							{#if skill.description}
								<div class=" text-xs text-gray-400 dark:text-gray-500 line-clamp-1">
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

<script lang="ts">
	import { getContext } from 'svelte';
	import Modal from '$lib/components/common/Modal.svelte';

	const i18n = getContext('i18n');

	let { show = $bindable(false) }: { show?: boolean } = $props();

	// ラベルは i18n キー(展開は $i18n.t(row.label) — Settings/General と同流儀)
	type Row = { label: string; keys: string[] };

	const SHORTCUT_COLUMNS: Row[][] = [
		[
			{ label: 'Open new chat', keys: ['Ctrl/⌘', 'Shift', 'O'] },
			{ label: 'Focus chat input', keys: ['Shift', 'Esc'] },
			{ label: 'Copy last code block', keys: ['Ctrl/⌘', 'Shift', ';'] },
			{ label: 'Copy last response', keys: ['Ctrl/⌘', 'Shift', 'C'] },
			{ label: 'Generate prompt pair', keys: ['Ctrl/⌘', 'Shift', 'Enter'] }
		],
		[
			{ label: 'Toggle settings', keys: ['Ctrl/⌘', '.'] },
			{ label: 'Toggle sidebar', keys: ['Ctrl/⌘', 'Shift', 'S'] },
			{ label: 'Delete chat', keys: ['Ctrl/⌘', 'Shift', '⌫/Delete'] },
			{ label: 'Show shortcuts', keys: ['Ctrl/⌘', '/'] }
		]
	];

	// llens: '#'(知識添付)は RAG 撤去(#52)、autocomplete 生成も撤去済み — 現行の実挙動のみ載せる
	const COMMAND_ROWS: Row[] = [
		{ label: 'Trigger a skill', keys: ['/'] },
		{ label: 'Talk to model', keys: ['@'] },
		{ label: 'Jump to prompt variable', keys: ['TAB'] }
	];
</script>

{#snippet shortcutRow(row: Row)}
	<div class="w-full flex justify-between items-center">
		<div class="text-sm">{$i18n.t(row.label)}</div>

		<div class="flex space-x-1 text-xs">
			{#each row.keys as key (key)}
				<div
					class="h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-line capitalize"
				>
					{key}
				</div>
			{/each}
		</div>
	</div>
{/snippet}

<Modal bind:show>
	<div class="text-content">
		<div class="flex justify-between px-5 pt-4">
			<div class="text-lg font-medium self-center">{$i18n.t('Keyboard shortcuts')}</div>
			<button class="self-center" aria-label={$i18n.t('Close')} onclick={() => (show = false)}>
				<span class="i-lucide-x size-5 block"></span>
			</button>
		</div>

		<div class="flex flex-col md:flex-row w-full p-5 md:space-x-4">
			<div class="flex flex-col w-full sm:flex-row sm:justify-center sm:space-x-6">
				{#each SHORTCUT_COLUMNS as column, i (i)}
					<div class="flex flex-col space-y-3 w-full self-start">
						{#each column as row (row.label)}
							{@render shortcutRow(row)}
						{/each}
					</div>
				{/each}
			</div>
		</div>

		<div class="flex justify-between px-5">
			<div class="text-lg font-medium self-center">{$i18n.t('Input commands')}</div>
		</div>

		<div class="flex flex-col md:flex-row w-full p-5 md:space-x-4">
			<div class="flex flex-col w-full sm:flex-row sm:justify-center sm:space-x-6">
				<div class="flex flex-col space-y-3 w-full self-start">
					{#each COMMAND_ROWS as row (row.label)}
						{@render shortcutRow(row)}
					{/each}
				</div>

				<div class="w-full self-start"></div>
			</div>
		</div>
	</div>
</Modal>

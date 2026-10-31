<script lang="ts">
	import { getContext, tick, untrack } from 'svelte';

	import type { RecipeItem } from '$lib/apis/recipes';
	import {
		buildSlashMenu,
		replaceCommandToken,
		selectableEntries,
		type SlashBlocked,
		type SlashEntry,
		type SlashSectionId,
		type SlashSkill
	} from '$lib/utils/slash-commands';

	const i18n = getContext('i18n');

	// `/` の補完(レシピとスキル)。補完だけで、入るのは `/name ` だけ(発火はサーバ — 送信した発言の
	// 先頭行の行頭の `/name`)。どれが使われるかの規則は $lib/utils/slash-commands.ts。
	let {
		prompt = $bindable(''),
		command = '', // `/` を含む先頭トークン(Commands.svelte から入力全体を渡す)
		skills = [],
		recipes = null,
		recipesError = false
	}: {
		prompt?: string;
		command?: string;
		skills?: SlashSkill[];
		recipes?: RecipeItem[] | null;
		recipesError?: boolean;
	} = $props();

	let selectedIdx = $state(0);

	const sections = $derived(buildSlashMenu({ skills, recipes, query: command.slice(1) }));
	const selectable = $derived(selectableEntries(sections));
	const selectedKey = $derived(selectable[selectedIdx]?.key ?? null);

	// 打った名前と同じ行があればそれ、無ければ先頭の選べる行から
	$effect(() => {
		if (command) {
			const query = command.slice(1).normalize('NFC');
			selectedIdx = Math.max(
				0,
				untrack(() => selectable).findIndex((e) => e.name === query)
			);
		}
	});

	// 選んだ行を見える所へ(上下キーで灰色の行を飛ばしても、選んだ行の全体が見えるように)
	$effect(() => {
		if (selectedKey === null) {
			return;
		}
		tick().then(() => {
			document
				.querySelector('#commands-container .selected-command-option-button')
				?.scrollIntoView({ block: 'nearest' });
		});
	});

	export const selectUp = () => {
		selectedIdx = Math.max(0, selectedIdx - 1);
	};

	export const selectDown = () => {
		selectedIdx = Math.max(0, Math.min(selectedIdx + 1, selectable.length - 1));
	};

	const sectionLabel = (id: SlashSectionId) =>
		({
			personal: $i18n.t('Personal recipes'),
			group: $i18n.t('Group recipes'),
			public: $i18n.t('Recipes for everyone'),
			skill: $i18n.t('Skills')
		})[id];

	const blockedNote = (blocked: SlashBlocked) => {
		switch (blocked.kind) {
			case 'skill':
				return $i18n.t('The skill "{{name}}" is used', { name: blocked.skill });
			case 'recipe':
				if (blocked.broken) {
					return blocked.place === 'personal'
						? $i18n.t('Your personal recipe comes first (it is broken, so nothing is called)')
						: $i18n.t(
								'The recipe of group "{{group}}" comes first (it is broken, so nothing is called)',
								{ group: blocked.group }
							);
				}
				return blocked.place === 'personal'
					? $i18n.t('Your personal recipe is used')
					: $i18n.t('The recipe of group "{{group}}" is used', { group: blocked.group });
			case 'broken':
				return $i18n.t('Broken: {{reason}}', { reason: blocked.reasons.join('、') });
			case 'space':
				return $i18n.t('The name has a space, so it cannot be called with /');
		}
	};

	const confirm = async (entry: SlashEntry) => {
		// 先頭行の行頭の入力中の `/…` だけを `/name ` に置き換える(テンプレート全文は入れない)
		prompt = replaceCommandToken(prompt, entry.name);

		const chatInputElement = document.getElementById('chat-input');

		await tick();
		if (chatInputElement) {
			chatInputElement.focus();
			chatInputElement.dispatchEvent(new Event('input'));
		}
	};
</script>

{#snippet row(entry: SlashEntry, note: string)}
	<div class="flex items-baseline gap-2 min-w-0">
		<div class="font-medium text-content truncate">/{entry.name}</div>
		{#if entry.title}
			<div class="text-xs text-content-muted truncate">{entry.title}</div>
		{/if}
		{#if entry.place === 'group'}
			<div
				class="ml-auto shrink-0 text-xs text-content-muted rounded-md border border-gray-200 dark:border-gray-700 px-1.5"
			>
				{entry.group}
			</div>
		{/if}
	</div>
	{#if entry.description}
		<div class="text-xs text-content-subtle line-clamp-1">{entry.description}</div>
	{/if}
	{#if note}
		<div class="flex items-center gap-1 text-xs text-content-muted">
			<span class="i-lucide-ban size-3 block shrink-0"></span>
			<span class="line-clamp-2">{note}</span>
		</div>
	{:else if entry.warnings.length > 0}
		<div class="flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400">
			<span class="i-lucide-triangle-alert size-3 block shrink-0"></span>
			<span class="line-clamp-2"
				>{$i18n.t('Warning: {{reason}}', { reason: entry.warnings.join('、') })}</span
			>
		</div>
	{/if}
{/snippet}

{#if sections.length > 0 || recipesError}
	<div
		id="commands-container"
		class="px-2 mb-2 text-left w-full absolute bottom-0 left-0 right-0 z-10"
	>
		<div class="flex w-full rounded-xl border border-gray-100 dark:border-gray-850">
			<div class="max-h-72 flex flex-col w-full rounded-xl bg-white dark:bg-gray-900 text-content">
				<div class="m-1 overflow-y-auto p-1 space-y-0.5">
					{#each sections as section (section.id)}
						<div class="px-3 pt-1.5 pb-0.5 text-xs font-medium text-content-muted">
							{sectionLabel(section.id)}
						</div>
						{#each section.entries as entry (entry.key)}
							{#if entry.blocked}
								{@const note = blockedNote(entry.blocked)}
								<div
									class="px-3 py-1.5 rounded-xl w-full text-left opacity-50 cursor-not-allowed"
									aria-disabled="true"
									title={note}
								>
									{@render row(entry, note)}
								</div>
							{:else}
								<button
									class="px-3 py-1.5 rounded-xl w-full text-left {entry.key === selectedKey
										? 'bg-gray-50 dark:bg-gray-850 selected-command-option-button'
										: ''}"
									type="button"
									onclick={() => {
										confirm(entry);
									}}
									onmousemove={() => {
										selectedIdx = selectable.findIndex((e) => e.key === entry.key);
									}}
								>
									{@render row(entry, '')}
								</button>
							{/if}
						{/each}
					{/each}
					{#if recipesError}
						<div class="px-3 py-1.5 text-xs text-content-muted">
							{$i18n.t('Could not get the recipe list')}
						</div>
					{/if}
				</div>
			</div>
		</div>
	</div>
{/if}

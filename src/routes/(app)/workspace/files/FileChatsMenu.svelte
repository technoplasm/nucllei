<script lang="ts">
	// llens(#69): file を参照しているチャットへの導線(チャットアイコン → ドロップダウン)。
	// 一覧は開いたときに遅延取得。自分のチャットは /c/、他人の分(admin のみ返る)は /s/ で開く
	// (admin は ENABLE_ADMIN_CHAT_ACCESS 下で /s/{chat_id} から任意のチャットを閲覧できる)。
	import { DropdownMenu } from 'bits-ui';
	import { getContext } from 'svelte';
	import { goto } from '$app/navigation';

	import dayjs from 'dayjs';

	import { user } from '$lib/stores';
	import { getFileChats } from '$lib/apis/files';
	import { flyAndScale } from '$lib/utils/transitions';

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n = getContext('i18n');

	let { fileId, buttonClass = '' }: { fileId: string; buttonClass?: string } = $props();

	let show = $state(false);
	let chats = $state<any[] | null>(null);

	$effect(() => {
		if (show && chats === null) {
			getFileChats(fileId)
				.then((res) => {
					chats = res ?? [];
				})
				.catch(() => {
					chats = [];
				});
		}
	});
</script>

<Dropdown bind:show>
	<Tooltip content={$i18n.t('Related chats')}>
		<div class={buttonClass}>
			<span class="i-lucide-message-square size-4 block"></span>
		</div>
	</Tooltip>

	<div slot="content">
		<DropdownMenu.Content
			class="max-w-[280px] rounded-xl px-1 py-1.5 border border-gray-300/30 dark:border-gray-700/50 z-50 bg-white dark:bg-gray-850 text-content shadow-sm"
			sideOffset={-2}
			side="bottom"
			align="end"
			transition={flyAndScale}
		>
			{#if chats === null}
				<div class="flex justify-center px-3 py-2">
					<Spinner className="size-4" />
				</div>
			{:else if chats.length === 0}
				<div class="px-3 py-2 text-sm text-content-muted whitespace-nowrap">
					{$i18n.t('No chats found')}
				</div>
			{:else}
				{#each chats as chat (chat.id)}
					<DropdownMenu.Item
						class="flex flex-col items-start px-3 py-1.5 text-sm cursor-pointer hover:bg-surface-hover rounded-lg"
						on:click={() => {
							goto(chat.user_id === $user?.id ? `/c/${chat.id}` : `/s/${chat.id}`);
						}}
					>
						<div class="w-full font-medium line-clamp-1">
							{chat.title}
						</div>
						<div class="text-xs text-content-muted">
							{dayjs((chat.updated_at ?? 0) * 1000).format('YYYY-MM-DD HH:mm')}
						</div>
					</DropdownMenu.Item>
				{/each}
			{/if}
		</DropdownMenu.Content>
	</div>
</Dropdown>

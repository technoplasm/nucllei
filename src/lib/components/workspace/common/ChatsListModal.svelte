<script lang="ts">
	// llens(#69): チャット一覧モーダル(汎用)。「file に紐づくチャット」(files の関連チャット)と
	// 「user に紐づくチャット」(admin の UserChatsModal)が同じ見た目でこれを消費する。
	// データは BE の Chats.get_chat_previews の形({id, user_id, user_name, title, snippet,
	// created_at, updated_at})。null = 読み込み中 / [] = なし。
	// 行リンクは自分のチャット = /c/、他人の分 = /s/(admin は ENABLE_ADMIN_CHAT_ACCESS 下で
	// /s/{chat_id} を閲覧できる)。onDelete を渡したときだけ削除列が出る。
	import { getContext } from 'svelte';
	import dayjs from '$lib/dayjs';
	import localizedFormat from 'dayjs/plugin/localizedFormat';
	dayjs.extend(localizedFormat);

	import { user } from '$lib/stores';

	import Modal from '$lib/components/common/Modal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n = getContext('i18n');

	let {
		show = $bindable(false),
		title,
		subtitle = '',
		chats,
		showOwner = undefined,
		onDelete = undefined
	}: {
		show?: boolean;
		title: string;
		subtitle?: string;
		chats: any[] | null;
		// 所有者列: 未指定なら「他人のチャットが混ざるとき」だけ出す(UserChatsModal は
		// 相手が自明なので false を渡す)
		showOwner?: boolean;
		onDelete?: (chat: any) => void;
	} = $props();

	let sortKey = $state<'title' | 'updated_at'>('updated_at');
	let sortOrder = $state<'asc' | 'desc'>('desc');

	const setSortKey = (key: typeof sortKey) => {
		if (sortKey === key) {
			sortOrder = sortOrder === 'asc' ? 'desc' : 'asc';
		} else {
			sortKey = key;
			sortOrder = key === 'title' ? 'asc' : 'desc';
		}
	};

	const sortedChats = $derived(
		[...(chats ?? [])].sort((a, b) => {
			if (a[sortKey] < b[sortKey]) return sortOrder === 'asc' ? -1 : 1;
			if (a[sortKey] > b[sortKey]) return sortOrder === 'asc' ? 1 : -1;
			return 0;
		})
	);

	const ownerColumn = $derived(showOwner ?? (chats ?? []).some((c) => c.user_id !== $user?.id));
</script>

<Modal size="lg" bind:show>
	<div class="flex justify-between text-content px-5 pt-4">
		<div class="flex items-center gap-2.5 min-w-0 text-lg font-medium self-center">
			<span class="shrink-0">{title}</span>
			{#if subtitle}
				<span class="text-sm text-content-muted truncate">{subtitle}</span>
			{/if}
		</div>
		<button
			class="self-center"
			aria-label={$i18n.t('Close')}
			onclick={() => {
				show = false;
			}}
		>
			<span class="i-lucide-x size-5 block"></span>
		</button>
	</div>

	<div class="flex flex-col w-full px-5 pt-2 pb-4 text-content">
		{#if chats === null}
			<div class="flex justify-center py-8">
				<Spinner />
			</div>
		{:else if chats.length === 0}
			<div class="text-left text-sm w-full mb-8 text-content-muted">
				{$i18n.t('No chats found')}
			</div>
		{:else}
			<div class="text-left text-sm w-full mb-4 max-h-[22rem] overflow-y-auto">
				<div class="relative overflow-x-auto">
					<table class="w-full text-sm text-left text-content-muted table-auto">
						<thead class="text-xs text-content uppercase bg-transparent border-b-2 border-line">
							<tr>
								<th scope="col" class="px-3 py-2 select-none">
									<button
										class="flex gap-1 items-center cursor-pointer uppercase"
										onclick={() => setSortKey('title')}
									>
										{$i18n.t('Title')}
										<span
											class="size-2.5 block {sortKey === 'title'
												? sortOrder === 'asc'
													? 'i-lucide-chevron-up'
													: 'i-lucide-chevron-down'
												: 'i-lucide-chevron-up invisible'}"
										></span>
									</button>
								</th>
								{#if ownerColumn}
									<th scope="col" class="px-3 py-2 select-none">{$i18n.t('User')}</th>
								{/if}
								<th scope="col" class="px-3 py-2 hidden md:table-cell select-none">
									<button
										class="flex gap-1 items-center cursor-pointer uppercase justify-end w-full"
										onclick={() => setSortKey('updated_at')}
									>
										{$i18n.t('Updated at')}
										<span
											class="size-2.5 block {sortKey === 'updated_at'
												? sortOrder === 'asc'
													? 'i-lucide-chevron-up'
													: 'i-lucide-chevron-down'
												: 'i-lucide-chevron-up invisible'}"
										></span>
									</button>
								</th>
								{#if onDelete}
									<th scope="col" class="px-3 py-2 text-right"></th>
								{/if}
							</tr>
						</thead>
						<tbody>
							{#each sortedChats as chat, idx (chat.id)}
								{@const own = chat.user_id === $user?.id}
								<tr
									class="bg-transparent {idx !== sortedChats.length - 1 &&
										'border-b'} border-line text-xs"
								>
									<td class="px-3 py-2">
										<a href={own ? `/c/${chat.id}` : `/s/${chat.id}`}>
											<div class="underline line-clamp-1 max-w-96">
												{chat.title}
											</div>
										</a>
										{#if chat.snippet}
											<div class="text-content-muted line-clamp-1 max-w-96 mt-0.5">
												{chat.snippet}
											</div>
										{/if}
									</td>

									{#if ownerColumn}
										<td class="px-3 py-2 whitespace-nowrap">
											{chat.user_name ?? chat.user_id}
										</td>
									{/if}

									<td class="px-3 py-2 hidden md:table-cell text-right whitespace-nowrap align-top">
										{dayjs((chat.updated_at ?? 0) * 1000).format('LLL')}
									</td>

									{#if onDelete}
										<td class="px-3 py-1 text-right align-top">
											<div class="flex justify-end w-full">
												<Tooltip content={$i18n.t('Delete Chat')}>
													<button
														class="self-center w-fit text-sm px-2 py-2 hover:bg-surface-hover rounded-xl"
														aria-label={$i18n.t('Delete Chat')}
														onclick={() => onDelete(chat)}
													>
														<span class="i-lucide-trash-2 size-4 block"></span>
													</button>
												</Tooltip>
											</div>
										</td>
									{/if}
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</div>
		{/if}
	</div>
</Modal>

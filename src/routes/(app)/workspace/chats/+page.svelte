<script lang="ts">
	// llens(#19, #69 A2): admin チャット閲覧ビュー(一覧テーブルのみ — 詳細閲覧・共有編集は後続)。
	// 一覧はサーバーページング(GET /chats/admin/list)。導出列(snippet・ターン数・
	// 添付数)はページ分だけサーバーで chat JSON をパースした軽量行で返る。
	// token は #57 が Chat.meta["usage"] に記録した保存値の表示(無い旧チャットは「-」)。
	// 行クリック: 自分のチャット=/c/、他人の分=/s/(admin は ENABLE_ADMIN_CHAT_ACCESS
	// 経由で chat id を共有ビューで開ける)。
	import { onMount, getContext } from 'svelte';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';

	import dayjs from 'dayjs';

	import { WEBUI_NAME, user } from '$lib/stores';

	import { getAdminChatList, getAdminChatUsers, getAdminChatModels } from '$lib/apis/chats';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import ShareChatModal from '$lib/components/chat/ShareChatModal.svelte';

	const i18n = getContext('i18n');

	const LIMIT = 60;

	let loaded = $state(false);
	let loading = $state(false);

	let chats = $state<any[]>([]);
	let total = $state(0);

	let query = $state('');
	let queryDebounceTimeout: ReturnType<typeof setTimeout> | null = null;

	// ソートは updated_at 固定(向きのみ切替)
	let direction = $state<'desc' | 'asc'>('desc');

	// フィルタ: ユーザーは単一選択(空 = 全ユーザー)、モデルも単一選択(空 = 全モデル)。
	// 期間は updated_at の日付範囲(両端含む・ローカルタイムの日単位)
	let owners = $state<any[]>([]);
	let selectedUserId = $state('');
	let models = $state<any[]>([]);
	let selectedModel = $state('');
	let dateFrom = $state('');
	let dateTo = $state('');
	// llens(#31): soft delete 済みチャットの扱い(既定 = 含めて表示、行に「削除済み」バッジ)
	let deletedFilter = $state<'include' | 'exclude' | 'only'>('include');
	// llens(#19): 共有状態フィルタ(院内に何が共有されているかの把握用)
	let sharedFilter = $state<'include' | 'exclude' | 'only'>('include');

	// llens(#19): admin からの共有権限編集 — 行の共有ボタンで ShareChatModal を開く。
	// モーダル内で共有状態が変わりうるので、閉じたら一覧を再取得してバッジを追従させる
	let shareChatId = $state('');
	let showShareModal = $state(false);
	const openShareModal = (chat: any) => {
		shareChatId = chat.id;
		showShareModal = true;
	};
	const onShareModalClose = () => {
		shareChatId = '';
		fetchChats(false);
	};

	// フィルタ連打時のフェッチ競合ガード(古い応答が後着で最新結果を上書きするのを防ぐ)
	let fetchSeq = 0;

	const fetchChats = async (append = false) => {
		const seq = ++fetchSeq;
		loading = true;

		const res = await getAdminChatList({
			limit: LIMIT,
			offset: append ? chats.length : 0,
			order_by: 'updated_at',
			direction,
			user_id: selectedUserId !== '' ? selectedUserId : undefined,
			model: selectedModel !== '' ? selectedModel : undefined,
			title: query !== '' ? query : undefined,
			updated_from: dateFrom !== '' ? dayjs(dateFrom).startOf('day').unix() : undefined,
			updated_to: dateTo !== '' ? dayjs(dateTo).endOf('day').unix() : undefined,
			deleted: deletedFilter !== 'include' ? deletedFilter : undefined,
			shared: sharedFilter !== 'include' ? sharedFilter : undefined
		}).catch((err) => {
			toast.error(`${err}`);
			return null;
		});

		if (seq !== fetchSeq) {
			return;
		}

		if (res) {
			// append 中にサーバー側の並びが変わりうる(更新日時ソート中の新着等)。
			// 同一 id の重複は keyed each を壊すので除外する。
			chats = append
				? [...chats, ...res.items.filter((item: any) => !chats.some((c) => c.id === item.id))]
				: res.items;
			total = res.total;
		}

		loading = false;
	};

	// タイトル検索(部分一致・サーバーフィルタ・debounce)
	const onQueryInput = () => {
		if (queryDebounceTimeout) {
			clearTimeout(queryDebounceTimeout);
		}
		queryDebounceTimeout = setTimeout(() => {
			fetchChats(false);
		}, 300);
	};

	// ソート(最終更新ヘッダのみ)
	const toggleUpdatedSort = () => {
		direction = direction === 'desc' ? 'asc' : 'desc';
		fetchChats(false);
	};

	// 自分のチャットは通常ビュー(/c — サイドバー維持・編集可)で開く。
	// 他人のと削除済みは共有ビュー(/s — admin は ENABLE_ADMIN_CHAT_ACCESS 経由で閲覧のみ。
	// 削除済みは /c だと本人向け取得から除外されて開けないため自分のでも /s で開く — #31)。
	const openChat = (chat: any) => {
		goto(chat.user_id === $user?.id && !chat.deleted_at ? `/c/${chat.id}` : `/s/${chat.id}`);
	};

	const formatTimestamp = (ts: number | null | undefined) =>
		dayjs((ts ?? 0) * 1000).format('YYYY-MM-DD HH:mm');

	const formatTokens = (n: number | null | undefined) => {
		if (n == null) {
			return '-';
		}
		if (n >= 1_000_000) {
			return `${(n / 1_000_000).toFixed(1)}M`;
		}
		if (n >= 1_000) {
			return `${(n / 1_000).toFixed(1)}k`;
		}
		return `${n}`;
	};

	const usageTooltip = (usage: any) => {
		if (!usage) {
			return '';
		}
		const parts = [`IN ${usage.prompt_tokens ?? '-'}`, `OUT ${usage.completion_tokens ?? '-'}`];
		if (usage.context_size) {
			const percent = ((100 * (usage.prompt_tokens ?? 0)) / usage.context_size).toFixed(1);
			parts.push(`${percent}% / ${formatTokens(usage.context_size)}`);
		}
		return parts.join(' · ');
	};

	onMount(async () => {
		const [ownersRes, modelsRes] = await Promise.all([
			getAdminChatUsers().catch(() => null),
			getAdminChatModels().catch(() => null)
		]);
		owners = ownersRes ?? [];
		models = modelsRes ?? [];

		await fetchChats(false);
		loaded = true;
	});
</script>

<svelte:head>
	<title>
		{$i18n.t('Chats')} | {$WEBUI_NAME}
	</title>
</svelte:head>

<ShareChatModal bind:show={showShareModal} chatId={shareChatId} onClose={onShareModalClose} />

{#if loaded}
	<div class="flex flex-col gap-1 mt-1.5">
		<div class="flex items-center text-xl font-medium px-0.5">
			{$i18n.t('Chats')}
		</div>

		<div class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2">
			<div class="flex items-center px-2 bg-surface-form rounded-xs">
				<span class="i-lucide-search size-3.5 block shrink-0"></span>
				<input
					class="w-full sm:w-48 text-sm px-2 py-1 bg-transparent outline-hidden"
					bind:value={query}
					oninput={onQueryInput}
					placeholder={$i18n.t('Search Chats')}
				/>
			</div>
		</div>

		<!-- フィルタ: ユーザー(単一選択・空 = 全員)/ モデル / 更新日時範囲 -->
		<div class="mt-1 flex flex-wrap items-center gap-x-4 gap-y-2 px-0.5 text-xs text-content-muted">
			<select
				class="bg-surface-form hover:bg-surface-hover w-fit px-2 py-0.5 pr-6 rounded-xs text-sm outline-hidden {selectedUserId
					? ''
					: 'text-content-subtle'}"
				bind:value={selectedUserId}
				onchange={() => {
					fetchChats(false);
				}}
			>
				<option class=" text-content" value="">{$i18n.t('All Users')}</option>
				{#each owners as owner (owner.id)}
					<option class=" text-content" value={owner.id}>{owner.name} ({owner.chat_count})</option>
				{/each}
			</select>

			<select
				class="bg-surface-form hover:bg-surface-hover w-fit px-2 py-0.5 pr-6 rounded-xs text-sm outline-hidden {selectedModel
					? ''
					: 'text-content-subtle'}"
				bind:value={selectedModel}
				onchange={() => {
					fetchChats(false);
				}}
			>
				<option class=" text-content" value="">{$i18n.t('All Models')}</option>
				{#each models as model (model.model)}
					<option class=" text-content" value={model.model}>
						{model.model} ({model.chat_count})
					</option>
				{/each}
			</select>

			<select
				class="bg-surface-form hover:bg-surface-hover w-fit px-2 py-0.5 pr-6 rounded-xs text-sm outline-hidden {deletedFilter !==
				'include'
					? ''
					: 'text-content-subtle'}"
				bind:value={deletedFilter}
				onchange={() => {
					fetchChats(false);
				}}
			>
				<option class=" text-content" value="include">{$i18n.t('Include deleted')}</option>
				<option class=" text-content" value="exclude">{$i18n.t('Exclude deleted')}</option>
				<option class=" text-content" value="only">{$i18n.t('Deleted only')}</option>
			</select>

			<select
				class="bg-surface-form hover:bg-surface-hover w-fit px-2 py-0.5 pr-6 rounded-xs text-sm outline-hidden {sharedFilter !==
				'include'
					? ''
					: 'text-content-subtle'}"
				bind:value={sharedFilter}
				onchange={() => {
					fetchChats(false);
				}}
			>
				<option class=" text-content" value="include">{$i18n.t('Include shared')}</option>
				<option class=" text-content" value="exclude">{$i18n.t('Not shared')}</option>
				<option class=" text-content" value="only">{$i18n.t('Shared only')}</option>
			</select>

			<div class="flex items-center gap-1">
				<input
					type="date"
					class="bg-surface-form hover:bg-surface-hover w-fit px-2 py-0.5 rounded-xs text-sm outline-hidden"
					bind:value={dateFrom}
					onchange={() => {
						fetchChats(false);
					}}
				/>
				<span>–</span>
				<input
					type="date"
					class="bg-surface-form hover:bg-surface-hover w-fit px-2 py-0.5 rounded-xs text-sm outline-hidden"
					bind:value={dateTo}
					onchange={() => {
						fetchChats(false);
					}}
				/>
			</div>
		</div>
	</div>

	{#if chats.length === 0}
		<div class="w-full flex justify-center items-center py-16 text-sm text-content-subtle">
			{$i18n.t('No chats found')}
		</div>
	{:else}
		<div
			class="relative whitespace-nowrap overflow-x-auto max-w-full rounded-sm mt-6 mb-5"
		>
			<table class="w-full text-sm text-left text-content-muted table-auto max-w-full rounded-sm">
				<thead class="text-xs text-content-muted uppercase bg-surface-muted">
					<tr>
						<th scope="col" class="px-3 py-1.5 select-none">{$i18n.t('Title')}</th>
						<th scope="col" class="px-3 py-1.5 select-none">{$i18n.t('User')}</th>
						<th scope="col" class="px-3 py-1.5 select-none">{$i18n.t('Model')}</th>
						<th scope="col" class="px-3 py-1.5 select-none text-right">{$i18n.t('Turns')}</th>
						<th scope="col" class="px-3 py-1.5 select-none text-right">{$i18n.t('Files')}</th>
						<th scope="col" class="px-3 py-1.5 select-none text-right">{$i18n.t('Tokens')}</th>
						<th
							scope="col"
							class="px-3 py-1.5 cursor-pointer select-none"
							onclick={toggleUpdatedSort}
						>
							<div class="flex gap-1.5 items-center">
								{$i18n.t('Updated at')}
								<span
									class="size-2.5 block {direction === 'asc'
										? 'i-lucide-chevron-up'
										: 'i-lucide-chevron-down'}"
								></span>
							</div>
						</th>
						<th scope="col" class="px-3 py-1.5 select-none"></th>
					</tr>
				</thead>
				<tbody>
					{#each chats as chat (chat.id)}
						<tr
							class="bg-surface-raised text-xs cursor-pointer hover:bg-surface-hover"
							onclick={() => openChat(chat)}
						>
							<td class="px-3 py-1 font-medium text-content">
								<div class="max-w-80">
									<div class="flex items-center gap-1.5">
										<div class="font-medium truncate">{chat.title}</div>
										{#if chat.share_id}
											<span
												class="px-1.5 py-0.5 rounded-full bg-surface-muted text-[10px] font-normal text-content-muted shrink-0"
												>{$i18n.t('Shared')}</span
											>
										{/if}
										{#if chat.archived}
											<span
												class="px-1.5 py-0.5 rounded-full bg-surface-muted text-[10px] font-normal text-content-muted shrink-0"
												>{$i18n.t('Archived')}</span
											>
										{/if}
										{#if chat.pinned}
											<span
												class="px-1.5 py-0.5 rounded-full bg-surface-muted text-[10px] font-normal text-content-muted shrink-0"
												>{$i18n.t('Pinned')}</span
											>
										{/if}
										{#if chat.deleted_at}
											<span
												class="px-1.5 py-0.5 rounded-full bg-red-500/20 text-[10px] font-normal text-red-700 dark:text-red-200 shrink-0"
												>{$i18n.t('Deleted')}</span
											>
										{/if}
									</div>
									{#if chat.snippet}
										<div class="font-normal text-content-muted truncate mt-0.5">{chat.snippet}</div>
									{/if}
								</div>
							</td>
							<td class="px-3 py-1">
								<div class="max-w-32 truncate">{chat.user_name ?? chat.user_id}</div>
							</td>
							<td class="px-3 py-1">
								<div class="max-w-40 truncate">{chat.models.join(', ') || '-'}</div>
							</td>
							<td class="px-3 py-1 text-right">{chat.turn_count}</td>
							<td class="px-3 py-1 text-right">{chat.file_count > 0 ? chat.file_count : '-'}</td>
							<td class="px-3 py-1 text-right">
								{#if chat.usage}
									<Tooltip content={usageTooltip(chat.usage)}>
										{formatTokens(chat.usage.prompt_tokens)}
									</Tooltip>
								{:else}
									-
								{/if}
							</td>
							<td class="px-3 py-1">{formatTimestamp(chat.updated_at)}</td>
							<td class="px-1 py-1 text-right">
								<!-- llens(#19): admin からの共有権限編集(リンク発行/解除は ShareChatModal 経由)。
								     削除済みチャットは共有対象にしない -->
								{#if !chat.deleted_at}
									<Tooltip content={$i18n.t('Share')}>
										<button
											class="p-1.5 rounded-lg hover:bg-surface-muted text-content-muted hover:text-content"
											type="button"
											aria-label={$i18n.t('Share')}
											onclick={(e) => {
												e.stopPropagation();
												openShareModal(chat);
											}}
										>
											<span class="i-lucide-share-2 size-3.5 block"></span>
										</button>
									</Tooltip>
								{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}

	{#if chats.length < total}
		<div class="w-full flex justify-center pb-6 -mt-2">
			<button
				class="px-3.5 py-1.5 text-sm font-medium rounded-lg hover:bg-surface-hover text-content-muted hover:text-content flex items-center gap-2"
				type="button"
				disabled={loading}
				onclick={() => fetchChats(true)}
			>
				{#if loading}
					<Spinner className="size-3.5" />
				{/if}
				{$i18n.t('Load More')}
			</button>
		</div>
	{/if}
{:else}
	<div class="w-full h-full flex justify-center items-center">
		<Spinner />
	</div>
{/if}

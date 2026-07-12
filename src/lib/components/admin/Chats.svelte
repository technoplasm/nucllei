<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import dayjs from 'dayjs';

	import { WEBUI_NAME } from '$lib/stores';

	import { getAdminChatList, getAdminChatUsers, getAdminChatModels } from '$lib/apis/chats';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	import Search from '$lib/components/icons/Search.svelte';
	import ChevronUp from '$lib/components/icons/ChevronUp.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';

	// llens(#19): admin チャット閲覧ビュー(一覧テーブルのみ — 詳細閲覧・共有編集は後続)。
	// 一覧はサーバーページング(GET /chats/admin/list)。導出列(snippet・ターン数・
	// 添付数)はページ分だけサーバーで chat JSON をパースした軽量行で返る。
	// token は #57 が Chat.meta["usage"] に記録した保存値の表示(無い旧チャットは「-」)。
	const i18n = getContext('i18n');

	let loaded = false;
	let loading = false;

	let chats = [];
	let total = 0;

	let query = '';
	let queryDebounceTimeout: ReturnType<typeof setTimeout> | null = null;

	let orderBy: 'created_at' | 'updated_at' = 'updated_at';
	let direction: 'desc' | 'asc' = 'desc';
	let limit = 60;

	// フィルタ: ユーザーは単一選択(空 = 全ユーザー)、モデルも単一選択(空 = 全モデル)
	let owners = [];
	let selectedUserId = '';
	let models = [];
	let selectedModel = '';

	const fetchChats = async (append = false) => {
		loading = true;

		const res = await getAdminChatList(localStorage.token, {
			limit,
			offset: append ? chats.length : 0,
			order_by: orderBy,
			direction,
			user_id: selectedUserId !== '' ? selectedUserId : undefined,
			model: selectedModel !== '' ? selectedModel : undefined,
			title: query !== '' ? query : undefined
		}).catch((err) => {
			toast.error(`${err}`);
			return null;
		});

		if (res) {
			// append 中にサーバー側の並びが変わりうる(更新日時ソート中の新着等)。
			// 同一 id の重複は keyed each を壊すので除外する。
			chats = append
				? [...chats, ...res.items.filter((item) => !chats.some((c) => c.id === item.id))]
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

	// ソート(テーブルヘッダ)。キー変更時は desc から
	const setSortKey = (key: 'created_at' | 'updated_at') => {
		if (orderBy === key) {
			direction = direction === 'desc' ? 'asc' : 'desc';
		} else {
			orderBy = key;
			direction = 'desc';
		}
		fetchChats(false);
	};

	const formatTimestamp = (ts) => dayjs((ts ?? 0) * 1000).format('YYYY-MM-DD HH:mm');

	const formatTokens = (n) => {
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

	const usageTooltip = (usage) => {
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
			getAdminChatUsers(localStorage.token).catch(() => null),
			getAdminChatModels(localStorage.token).catch(() => null)
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

{#if loaded}
	<div class="flex flex-col gap-1 my-1.5">
		<div class="flex justify-between items-center">
			<div class="flex md:self-center text-xl font-medium px-0.5 items-center">
				{$i18n.t('Chats')}
				<div class="flex self-center w-[1px] h-6 mx-2.5 bg-gray-50 dark:bg-gray-850" />
				<span class="text-lg font-medium text-gray-500 dark:text-gray-300">{total}</span>
			</div>
		</div>

		<div class=" flex w-full space-x-2">
			<div class="flex flex-1">
				<div class=" self-center ml-1 mr-3">
					<Search className="size-3.5" />
				</div>
				<input
					class=" w-full text-sm pr-4 py-1 rounded-r-xl outline-hidden bg-transparent"
					bind:value={query}
					on:input={onQueryInput}
					placeholder={$i18n.t('Search Chats')}
				/>
			</div>
		</div>

		<!-- フィルタ: ユーザー(単一選択・空 = 全員)/ モデル / 読み込み数 -->
		<div class="flex flex-wrap items-center gap-x-3 gap-y-1 px-0.5 text-xs text-gray-500">
			<div class="flex items-center gap-0.5">
				<select
					class="dark:bg-gray-900 w-fit pr-6 rounded-sm text-xs bg-transparent outline-hidden {selectedUserId
						? ''
						: 'text-gray-500'}"
					bind:value={selectedUserId}
					on:change={() => {
						fetchChats(false);
					}}
				>
					<option class=" text-gray-700" value="">{$i18n.t('All Users')}</option>
					{#each owners as owner}
						<option class=" text-gray-700" value={owner.id}
							>{owner.name} ({owner.chat_count})</option
						>
					{/each}
				</select>
			</div>

			<div class="flex items-center gap-0.5">
				<select
					class="dark:bg-gray-900 w-fit pr-6 rounded-sm text-xs bg-transparent outline-hidden {selectedModel
						? ''
						: 'text-gray-500'}"
					bind:value={selectedModel}
					on:change={() => {
						fetchChats(false);
					}}
				>
					<option class=" text-gray-700" value="">{$i18n.t('All Models')}</option>
					{#each models as model}
						<option class=" text-gray-700" value={model.model}
							>{model.model} ({model.chat_count})</option
						>
					{/each}
				</select>
			</div>

			<div class="flex items-center gap-0.5">
				<select
					class="dark:bg-gray-900 w-fit pr-6 rounded-sm text-xs bg-transparent outline-hidden"
					value={`${limit}`}
					on:change={(e) => {
						limit = parseInt(e.target.value);
						fetchChats(false);
					}}
				>
					<option value="30">30</option>
					<option value="60">60</option>
					<option value="120">120</option>
				</select>
			</div>
		</div>
	</div>

	{#if chats.length === 0}
		<div class="w-full flex justify-center items-center py-16 text-sm text-gray-500">
			{$i18n.t('No chats found')}
		</div>
	{:else}
		<div
			class="scrollbar-hidden relative whitespace-nowrap overflow-x-auto max-w-full rounded-sm pt-0.5 mb-5"
		>
			<table
				class="w-full text-sm text-left text-gray-500 dark:text-gray-400 table-auto max-w-full rounded-sm"
			>
				<thead
					class="text-xs text-gray-700 uppercase bg-gray-50 dark:bg-gray-850 dark:text-gray-400 -translate-y-0.5"
				>
					<tr class="">
						<th scope="col" class="px-3 py-1.5 select-none">{$i18n.t('User')}</th>
						<th scope="col" class="px-3 py-1.5 select-none">{$i18n.t('Title')}</th>
						<th scope="col" class="px-3 py-1.5 select-none">{$i18n.t('Model')}</th>
						<th scope="col" class="px-3 py-1.5 select-none text-right">{$i18n.t('Turns')}</th>
						<th scope="col" class="px-3 py-1.5 select-none text-right">{$i18n.t('Files')}</th>
						<th scope="col" class="px-3 py-1.5 select-none text-right">{$i18n.t('Tokens')}</th>
						<th
							scope="col"
							class="px-3 py-1.5 cursor-pointer select-none"
							on:click={() => setSortKey('updated_at')}
						>
							<div class="flex gap-1.5 items-center">
								{$i18n.t('Updated at')}
								{#if orderBy === 'updated_at'}
									<span class="font-normal">
										{#if direction === 'asc'}
											<ChevronUp className="size-2" />
										{:else}
											<ChevronDown className="size-2" />
										{/if}
									</span>
								{:else}
									<span class="invisible">
										<ChevronUp className="size-2" />
									</span>
								{/if}
							</div>
						</th>
						<th
							scope="col"
							class="px-3 py-1.5 cursor-pointer select-none"
							on:click={() => setSortKey('created_at')}
						>
							<div class="flex gap-1.5 items-center">
								{$i18n.t('Created at')}
								{#if orderBy === 'created_at'}
									<span class="font-normal">
										{#if direction === 'asc'}
											<ChevronUp className="size-2" />
										{:else}
											<ChevronDown className="size-2" />
										{/if}
									</span>
								{:else}
									<span class="invisible">
										<ChevronUp className="size-2" />
									</span>
								{/if}
							</div>
						</th>
						<th scope="col" class="px-3 py-1.5 select-none" />
					</tr>
				</thead>
				<tbody class="">
					{#each chats as chat (chat.id)}
						<tr
							class="bg-white dark:bg-gray-900 dark:border-gray-850 text-xs hover:bg-black/5 dark:hover:bg-white/5 transition"
						>
							<td class="px-3 py-1">
								<div class="max-w-32 truncate">{chat.user_name ?? chat.user_id}</div>
							</td>
							<td class="px-3 py-1 font-medium text-gray-900 dark:text-white">
								<div class="max-w-80">
									<div class="font-medium truncate">{chat.title}</div>
									{#if chat.snippet}
										<div class="font-normal text-gray-500 truncate mt-0.5">{chat.snippet}</div>
									{/if}
								</div>
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
							<td class="px-3 py-1">{formatTimestamp(chat.created_at)}</td>
							<td class="px-3 py-1">
								<div class="flex gap-1">
									{#if chat.share_id}
										<span
											class="px-1.5 py-0.5 rounded-full bg-gray-100 dark:bg-gray-850 text-[10px]"
											>{$i18n.t('Shared')}</span
										>
									{/if}
									{#if chat.archived}
										<span
											class="px-1.5 py-0.5 rounded-full bg-gray-100 dark:bg-gray-850 text-[10px]"
											>{$i18n.t('Archived')}</span
										>
									{/if}
									{#if chat.pinned}
										<span
											class="px-1.5 py-0.5 rounded-full bg-gray-100 dark:bg-gray-850 text-[10px]"
											>{$i18n.t('Pinned')}</span
										>
									{/if}
								</div>
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
				class="px-3.5 py-1.5 text-sm font-medium rounded-xl hover:bg-black/5 dark:hover:bg-white/5 dark:text-gray-300 dark:hover:text-white transition flex items-center gap-2"
				type="button"
				disabled={loading}
				on:click={() => fetchChats(true)}
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

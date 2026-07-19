<script lang="ts">
	// llens(#48): Admin を Workspace に統合。admin ゲートは workspace layout 側。
	import { getContext, onMount } from 'svelte';

	import dayjs from '$lib/dayjs';
	import relativeTime from 'dayjs/plugin/relativeTime';
	import localizedFormat from 'dayjs/plugin/localizedFormat';
	dayjs.extend(relativeTime);
	dayjs.extend(localizedFormat);

	import { WEBUI_BASE_URL } from '$lib/constants';
	import { config, user as sessionUser } from '$lib/stores';
	import { getUserMetaFields, getUsers } from '$lib/apis/users';

	import Badge from '$lib/components/common/Badge.svelte';
	import Pagination from '$lib/components/common/Pagination.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	import Groups from './Groups.svelte';
	import UserModal from './UserModal.svelte';
	import UserChatsModal from './UserChatsModal.svelte';

	const i18n = getContext('i18n');

	const TABS = [
		{ id: 'overview', label: 'Users', icon: 'i-lucide-users' },
		{ id: 'groups', label: 'Groups', icon: 'i-lucide-users-round' }
	];

	let selectedTab = $state('overview');

	let users = $state<any[]>([]);
	let search = $state('');
	let page = $state(1);

	// llens(#55): 宣言済みの追加 meta キー(ADDITIONAL_USER_META)。一覧の列と
	// 編集モーダルの入力欄が自動で生える。
	let metaFields = $state<{ key: string; label: string }[]>([]);

	let selectedUser = $state<any>(null);
	let showUserChatsModal = $state(false);

	// llens(#55): 作成/編集は単一モーダル(UserModal)。editTarget=null で作成モード
	let showUserModal = $state(false);
	let editTarget = $state<any>(null);

	let sortKey = $state('created_at');
	let sortOrder = $state('asc');

	const SORTABLE_COLUMNS = [
		{ key: 'name', label: 'Name' },
		{ key: 'role', label: 'Role' },
		{ key: 'email', label: 'Email' },
		{ key: 'last_active_at', label: 'Last Active' },
		{ key: 'created_at', label: 'Created at' }
	];

	// 日付表示(fromNow / LL)を UI 言語に追従させる。dayjs.locale はグローバル設定
	// ($lib/dayjs で全ロケール登録済み。ja-JP → ja のように登録名へ丸める。
	// locale() は未登録名だと現在値を返すので、適用できた時点で打ち切る)
	$effect(() => {
		const lower = ($i18n.language ?? 'en').toLowerCase();
		for (const candidate of [lower, lower.split('-')[0], 'en']) {
			if (dayjs.locale(candidate) === candidate) {
				break;
			}
		}
	});

	function setSortKey(key: string) {
		if (sortKey === key) {
			sortOrder = sortOrder === 'asc' ? 'desc' : 'asc';
		} else {
			sortKey = key;
			sortOrder = 'asc';
		}
	}

	const filteredUsers = $derived(
		users
			.filter((u) => {
				if (search === '') {
					return true;
				}
				const query = search.toLowerCase();
				return u.name.toLowerCase().includes(query) || u.email.toLowerCase().includes(query);
			})
			.sort((a, b) => {
				if (a[sortKey] < b[sortKey]) return sortOrder === 'asc' ? -1 : 1;
				if (a[sortKey] > b[sortKey]) return sortOrder === 'asc' ? 1 : -1;
				return 0;
			})
			.slice((page - 1) * 20, page * 20)
	);

	const refreshUsers = async () => {
		users = await getUsers();
	};

	// タブ切替のたびに再取得(旧挙動の維持)
	$effect(() => {
		void selectedTab;
		refreshUsers();
	});

	onMount(async () => {
		metaFields = (await getUserMetaFields().catch(() => null)) ?? [];
	});
</script>

<UserModal
	bind:show={showUserModal}
	selectedUser={editTarget}
	{metaFields}
	sessionUser={$sessionUser}
	onSave={refreshUsers}
/>
<UserChatsModal bind:show={showUserChatsModal} user={selectedUser} />

<div class="flex flex-col lg:flex-row w-full h-full pb-2 lg:space-x-4">
	<div
		class=" flex flex-row overflow-x-auto gap-2.5 max-w-full lg:gap-1 lg:flex-col lg:flex-none lg:w-40 text-content text-sm font-medium text-left scrollbar-none"
		onwheel={(event) => {
			// 横スクロールタブは縦ホイールでも流せるようにする(狭幅時)
			if (event.deltaY !== 0) {
				event.currentTarget.scrollLeft += event.deltaY;
			}
		}}
	>
		{#each TABS as tab (tab.id)}
			<button
				class="px-0.5 py-1 min-w-fit rounded-lg lg:flex-none flex text-right {selectedTab === tab.id
					? ''
					: ' text-content-subtle hover:text-content'}"
				onclick={() => {
					selectedTab = tab.id;
				}}
			>
				<div class=" self-center mr-2">
					<span class="{tab.icon} size-4 block"></span>
				</div>
				<div class=" self-center">{$i18n.t(tab.label)}</div>
			</button>
		{/each}
	</div>

	<div class="flex-1 mt-1 lg:mt-0 overflow-y-scroll">
		{#if selectedTab === 'overview'}
			<!-- ヘッダ様式: 見出しで 1 行(count なし)/ 2 行目に Add pill + 検索 -->
			<div class="flex flex-col gap-1 mt-0.5 mb-2">
				<div class="flex items-center text-xl font-medium px-0.5">
					{$i18n.t('Users')}
				</div>

				<div class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2">
					<button
						class="shrink-0 whitespace-nowrap inline-flex items-center py-0.5 pl-2 pr-3 rounded-full bg-primary hover:bg-primary-hover text-primary-content font-medium text-sm"
						onclick={() => {
							editTarget = null;
							showUserModal = true;
						}}
					>
						<span class="i-lucide-plus size-3.5 block mr-1"></span>
						{$i18n.t('Add User')}
					</button>

					<div class="flex items-center px-2 bg-surface-form rounded-xs">
						<span class="i-lucide-search size-3.5 block shrink-0"></span>
						<input
							class="w-full sm:w-48 text-sm px-2 py-1 bg-transparent outline-hidden"
							bind:value={search}
							placeholder={$i18n.t('Search')}
						/>
					</div>
				</div>
			</div>

			<div
				class="relative whitespace-nowrap overflow-x-auto max-w-full rounded-sm pt-0.5"
			>
				<table class="w-full text-sm text-left text-content-muted table-auto max-w-full rounded-sm">
					<thead class="text-xs text-content-muted uppercase bg-surface-muted">
						<tr>
							{#each SORTABLE_COLUMNS as column (column.key)}
								<th scope="col" class="px-3 py-1.5 select-none">
									<button
										class="flex gap-1.5 items-center cursor-pointer uppercase"
										onclick={() => setSortKey(column.key)}
									>
										{$i18n.t(column.label)}

										{#if sortKey === column.key}
											<span class="font-normal">
												{#if sortOrder === 'asc'}
													<span class="i-lucide-chevron-up size-2 block"></span>
												{:else}
													<span class="i-lucide-chevron-down size-2 block"></span>
												{/if}
											</span>
										{:else}
											<span class="invisible">
												<span class="i-lucide-chevron-up size-2 block"></span>
											</span>
										{/if}
									</button>
								</th>
							{/each}

							{#each metaFields as field (field.key)}
								<th scope="col" class="px-3 py-1.5 select-none">
									<div class="flex gap-1.5 items-center">
										{field.label}
									</div>
								</th>
							{/each}

							<th scope="col" class="px-3 py-2 text-right"></th>
						</tr>
					</thead>
					<tbody>
						{#each filteredUsers as user (user.id)}
							<!-- 行クリック=編集モーダル(行末アイコンはチャットのみ — 2026-07-16)。
							 削除導線は持たない: user の hard delete は BE ごと撤去済み(#31 — チャット証憑の保全)。
							     無効化は編集で role=disabled にする(全 API 遮断 + 無効化案内画面) -->
							<tr
								class="bg-surface-raised text-xs cursor-pointer [&:hover:not(:has(button:hover))]:bg-surface-hover"
								onclick={() => {
									editTarget = user;
									showUserModal = true;
								}}
							>
								<td class="px-3 py-1 font-medium text-content w-max">
									<div class="flex flex-row w-max">
										<img
											class=" rounded-full w-6 h-6 object-cover mr-2.5"
											src={user.profile_image_url.startsWith(WEBUI_BASE_URL) ||
											user.profile_image_url.startsWith('https://www.gravatar.com/avatar/') ||
											user.profile_image_url.startsWith('data:')
												? user.profile_image_url
												: `/user.png`}
											alt="user"
										/>

										<div class=" font-medium self-center">{user.name}</div>
									</div>
								</td>
								<td class="px-3 py-1 min-w-[7rem] w-28">
									<Badge
										type={user.role === 'admin'
											? 'info'
											: user.role === 'user'
												? 'success'
												: user.role === 'disabled'
													? 'error'
													: 'muted'}
										content={$i18n.t(user.role)}
									/>
								</td>
								<td class=" px-3 py-1"> {user.email} </td>

								<td class=" px-3 py-1">
									{dayjs(user.last_active_at * 1000).fromNow()}
								</td>

								<td class=" px-3 py-1">
									{dayjs(user.created_at * 1000).format('LL')}
								</td>

								{#each metaFields as field (field.key)}
									<td class=" px-3 py-1"> {user.info?.[field.key] ?? ''} </td>
								{/each}

								<td class="px-3 py-1 text-right">
									<div class="flex justify-end w-full">
										<!-- llens(#69): admin 同士でも閲覧可に(user.role !== 'admin' の除外を撤去。
										 BE は元々 target の role を見ていない — 隠していたのは FE だけ) -->
										{#if $config?.features?.enable_admin_chat_access}
											<Tooltip content={$i18n.t('Chats')}>
												<button
													class="self-center w-fit text-sm px-2 py-2 hover:bg-surface-hover rounded-xl"
													aria-label={$i18n.t('Chats')}
													onclick={(e) => {
														e.stopPropagation();
														showUserChatsModal = !showUserChatsModal;
														selectedUser = user;
													}}
												>
													<span class="i-lucide-messages-square size-4 block"></span>
												</button>
											</Tooltip>
										{/if}
									</div>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>

			<Pagination bind:page count={users.length} />
		{:else if selectedTab === 'groups'}
			<Groups {users} />
		{/if}
	</div>
</div>

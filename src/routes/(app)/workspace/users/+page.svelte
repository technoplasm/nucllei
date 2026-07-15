<script lang="ts">
	// llens(#48): Admin を Workspace に統合。admin ゲートは workspace layout 側。
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import dayjs from '$lib/dayjs';
	import relativeTime from 'dayjs/plugin/relativeTime';
	import localizedFormat from 'dayjs/plugin/localizedFormat';
	dayjs.extend(relativeTime);
	dayjs.extend(localizedFormat);

	import { WEBUI_BASE_URL } from '$lib/constants';
	import { config, user as sessionUser } from '$lib/stores';
	import { deleteUserById, getUserMetaFields, getUsers } from '$lib/apis/users';

	import Badge from '$lib/components/common/Badge.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Pagination from '$lib/components/common/Pagination.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	import Groups from './Groups.svelte';
	import UserModal from './UserModal.svelte';
	import UserChatsModal from './UserChatsModal.svelte';

	const i18n = getContext('i18n');

	const TABS = [
		{ id: 'overview', label: 'Overview', icon: 'i-lucide-users' },
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
	let showDeleteConfirmDialog = $state(false);
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

	const deleteUserHandler = async (id: string) => {
		const res = await deleteUserById(id).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			await refreshUsers();
		}
	};

	onMount(async () => {
		metaFields = (await getUserMetaFields().catch(() => null)) ?? [];
	});
</script>

<ConfirmDialog
	bind:show={showDeleteConfirmDialog}
	on:confirm={() => {
		deleteUserHandler(selectedUser.id);
	}}
/>

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
				class="px-0.5 py-1 min-w-fit rounded-lg lg:flex-none flex text-right transition {selectedTab ===
				tab.id
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
			<div class="mt-0.5 mb-2 gap-1 flex flex-col md:flex-row justify-between">
				<div class="flex md:self-center text-lg font-medium px-0.5">
					<div class="flex-shrink-0">
						{$i18n.t('Users')}
					</div>
					<div class="flex self-center w-[1px] h-6 mx-2.5 bg-gray-50 dark:bg-gray-850"></div>

					<span class="text-lg font-medium text-content">{users.length}</span>
				</div>

				<div class="flex gap-1">
					<div class=" flex w-full space-x-2">
						<div class="flex flex-1">
							<div class=" self-center ml-1 mr-3">
								<span class="i-lucide-search size-4 block"></span>
							</div>
							<input
								class=" w-full text-sm pr-4 py-1 rounded-r-xl outline-hidden bg-transparent"
								bind:value={search}
								placeholder={$i18n.t('Search')}
							/>
						</div>

						<div>
							<Tooltip content={$i18n.t('Add User')}>
								<button
									class=" p-2 rounded-xl hover:bg-gray-100 dark:bg-gray-900 dark:hover:bg-gray-850 transition font-medium text-sm flex items-center space-x-1"
									aria-label={$i18n.t('Add User')}
									onclick={() => {
										editTarget = null;
										showUserModal = true;
									}}
								>
									<span class="i-lucide-plus size-3.5 block"></span>
								</button>
							</Tooltip>
						</div>
					</div>
				</div>
			</div>

			<div
				class="scrollbar-hidden relative whitespace-nowrap overflow-x-auto max-w-full rounded-sm pt-0.5"
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
							<!-- 行クリック=編集モーダル。キーボード操作は行末の編集ボタン(aria-label 付き)が担う -->
							<tr
								class="bg-surface-raised text-xs cursor-pointer hover:bg-black/5 dark:hover:bg-white/5 transition"
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
													class="self-center w-fit text-sm px-2 py-2 hover:bg-black/5 dark:hover:bg-white/5 rounded-xl"
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

										<Tooltip content={$i18n.t('Edit User')}>
											<button
												class="self-center w-fit text-sm px-2 py-2 hover:bg-black/5 dark:hover:bg-white/5 rounded-xl"
												aria-label={$i18n.t('Edit User')}
												onclick={(e) => {
													e.stopPropagation();
													editTarget = user;
													showUserModal = true;
												}}
											>
												<span class="i-lucide-pencil size-4 block"></span>
											</button>
										</Tooltip>

										{#if user.role !== 'admin'}
											<Tooltip content={$i18n.t('Delete User')}>
												<button
													class="self-center w-fit text-sm px-2 py-2 hover:bg-black/5 dark:hover:bg-white/5 rounded-xl"
													aria-label={$i18n.t('Delete User')}
													onclick={(e) => {
														e.stopPropagation();
														showDeleteConfirmDialog = true;
														selectedUser = user;
													}}
												>
													<span class="i-lucide-trash-2 size-4 block"></span>
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

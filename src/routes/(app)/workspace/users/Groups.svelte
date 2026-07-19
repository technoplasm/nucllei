<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { getContext, onMount } from 'svelte';

	import { createNewGroup, deleteGroupById, getGroups, updateGroupById } from '$lib/apis/groups';
	import { getUserDefaultPermissions, updateUserDefaultPermissions } from '$lib/apis/users';

	import Modal from '$lib/components/common/Modal.svelte';
	import EditGroupModal from './EditGroupModal.svelte';
	import GroupPermissions from './GroupPermissions.svelte';

	const i18n = getContext('i18n');

	let { users = [] }: { users?: any[] } = $props();

	let loaded = $state(false);
	let groups = $state<any[]>([]);
	let search = $state('');

	const filteredGroups = $derived(
		groups.filter((group) => {
			if (search === '') {
				return true;
			}
			return group.name.toLowerCase().includes(search.toLowerCase());
		})
	);

	// llens(#66): 権限3層化 — workspace.skills = skill editor ロールのみ。
	let defaultPermissions = $state({
		workspace: {
			skills: false
		},
		chat: {
			controls: true,
			file_upload: true,
			delete: true,
			edit: true,
			multiple_models: true
		}
	});

	let savingDefaultPermissions = $state(false);

	// グループ編集(1 モーダルを選択中グループで使い回す)
	let selectedGroup = $state<any>(null);
	let showEditGroupModal = $state(false);

	// グループ作成(説明は FE から撤去 — 2026-07-16。名前で自明、消費者がいない。
	// BE の description フィールドは必須 str のままなので '' を渡す)
	let showCreateGroupModal = $state(false);
	let newName = $state('');
	let creating = $state(false);

	const setGroups = async () => {
		groups = await getGroups();
	};

	const createGroupHandler = async (e: SubmitEvent) => {
		e.preventDefault();
		creating = true;

		const res = await createNewGroup({ name: newName, description: '' }).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		creating = false;

		if (res) {
			toast.success($i18n.t('Group created successfully'));
			showCreateGroupModal = false;
			newName = '';
			await setGroups();
		}
	};

	const updateGroupHandler = async (group: any) => {
		const res = await updateGroupById(selectedGroup.id, group).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			toast.success($i18n.t('Group updated successfully'));
			await setGroups();
		}
	};

	const deleteGroupHandler = async () => {
		const res = await deleteGroupById(selectedGroup.id).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			toast.success($i18n.t('Group deleted successfully'));
			await setGroups();
		}
	};

	const saveDefaultPermissionsHandler = async () => {
		savingDefaultPermissions = true;
		const res = await updateUserDefaultPermissions(defaultPermissions).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		savingDefaultPermissions = false;

		if (res) {
			toast.success($i18n.t('Default permissions updated successfully'));
			defaultPermissions = await getUserDefaultPermissions();
		}
	};

	onMount(async () => {
		await setGroups();
		defaultPermissions = await getUserDefaultPermissions();
		loaded = true;
	});
</script>

{#if loaded}
	<EditGroupModal
		bind:show={showEditGroupModal}
		edit
		{users}
		group={selectedGroup}
		onSubmit={updateGroupHandler}
		onDelete={deleteGroupHandler}
	/>

	<Modal size="sm" bind:show={showCreateGroupModal}>
		<div>
			<div class=" flex justify-between text-content px-5 pt-4 mb-1.5">
				<div class=" text-lg font-medium self-center font-primary">
					{$i18n.t('Add User Group')}
				</div>
				<button
					class="self-center"
					aria-label={$i18n.t('Close')}
					onclick={() => {
						showCreateGroupModal = false;
					}}
				>
					<span class="i-lucide-x size-5 block"></span>
				</button>
			</div>

			<div class="flex flex-col w-full px-4 pb-4 text-content">
				<form class="flex flex-col w-full" onsubmit={createGroupHandler}>
					<div class="px-1 flex flex-col w-full">
						<div class="flex flex-col w-full">
							<div class=" mb-0.5 text-xs text-content-muted">{$i18n.t('Name')}</div>

							<input
								class="w-full text-sm bg-transparent placeholder:text-content-subtle outline-hidden"
								type="text"
								bind:value={newName}
								placeholder={$i18n.t('Group Name')}
								autocomplete="off"
								required
							/>
						</div>
					</div>

					<div class="flex justify-end pt-3 text-sm font-medium gap-1.5">
						<button
							class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 rounded-full flex flex-row space-x-1 items-center {creating
								? ' cursor-not-allowed'
								: ''}"
							type="submit"
							disabled={creating}
						>
							{$i18n.t('Create')}

							{#if creating}
								<span class="i-lucide-loader-circle size-4 block animate-spin ml-2 self-center"
								></span>
							{/if}
						</button>
					</div>
				</form>
			</div>
		</div>
	</Modal>

	<!-- ヘッダ様式: 見出しで 1 行(count なし)/ 2 行目に Create pill + 検索 -->
	<div class="flex flex-col gap-1 mt-0.5 mb-2">
		<div class="flex items-center text-xl font-medium px-0.5">
			{$i18n.t('Groups')}
		</div>

		<div class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2">
			<button
				class="shrink-0 whitespace-nowrap inline-flex items-center py-0.5 pl-2 pr-3 rounded-full bg-primary hover:bg-primary-hover text-primary-content font-medium text-sm"
				onclick={() => {
					showCreateGroupModal = true;
				}}
			>
				<span class="i-lucide-plus size-3.5 block mr-1"></span>
				{$i18n.t('Create Group')}
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

	<div>
		{#if filteredGroups.length === 0}
			<div class="flex flex-col items-center justify-center h-40">
				<div class="mt-1 text-sm text-content-muted">
					{$i18n.t('Use groups to group your users and assign permissions.')}
				</div>
			</div>
		{:else}
			<!-- users タブと同型のテーブル(行クリック=編集、行末アイコンなし) -->
			<div
				class="relative whitespace-nowrap overflow-x-auto max-w-full rounded-sm pt-0.5"
			>
				<table class="w-full text-sm text-left text-content-muted table-auto max-w-full rounded-sm">
					<thead class="text-xs text-content-muted uppercase bg-surface-muted">
						<tr>
							<th scope="col" class="px-3 py-1.5 select-none">{$i18n.t('Name')}</th>
							<th scope="col" class="px-3 py-1.5 select-none">{$i18n.t('Users')}</th>
						</tr>
					</thead>
					<tbody>
						{#each filteredGroups as group (group.id)}
							<tr
								class="bg-surface-raised text-xs cursor-pointer hover:bg-surface-hover"
								onclick={() => {
									selectedGroup = group;
									showEditGroupModal = true;
								}}
							>
								<td class="px-3 py-2 font-medium text-content">
									<div class="flex items-center gap-2">
										<span class="i-lucide-circle-user-round size-4 block"></span>
										{group.name}
									</div>
								</td>
								<td class="px-3 py-2">
									{group.user_ids.length}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}

		<!-- 既定権限: モーダルに畳まず常時表示(2026-07-16)。user ロール全員のベースなので
		 グループと同じ画面で一望できるほうが権限の全体像を追いやすい -->
		<hr class="my-4 border-line" />

		<div class="flex items-center gap-2.5 px-0.5 mb-2">
			<div class="text-lg font-medium">{$i18n.t('Default permissions')}</div>
			<div class="text-xs text-content-muted">
				{$i18n.t('applies to all users with the "user" role')}
			</div>
		</div>

		<GroupPermissions bind:permissions={defaultPermissions} />

		<div class="flex justify-start pt-3 pb-4">
			<button
				class="px-3.5 py-1.5 text-sm font-medium rounded-full bg-primary hover:bg-primary-hover text-primary-content flex items-center gap-2"
				type="button"
				disabled={savingDefaultPermissions}
				onclick={saveDefaultPermissionsHandler}
			>
				{$i18n.t('Save')}
				{#if savingDefaultPermissions}
					<span class="i-lucide-loader-circle size-4 block animate-spin"></span>
				{/if}
			</button>
		</div>
	</div>
{/if}

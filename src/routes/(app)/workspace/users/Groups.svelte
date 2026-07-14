<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { getContext, onMount } from 'svelte';

	import {
		createNewGroup,
		deleteGroupById,
		getGroups,
		updateGroupById
	} from '$lib/apis/groups';
	import { getUserDefaultPermissions, updateUserDefaultPermissions } from '$lib/apis/users';

	import Modal from '$lib/components/common/Modal.svelte';
	import Textarea from '$lib/components/common/Textarea.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import EditGroupModal from './EditGroupModal.svelte';

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
		},
		features: {
			image_generation: true
		}
	});

	let showDefaultPermissionsModal = $state(false);

	// グループ編集(1 モーダルを選択中グループで使い回す)
	let selectedGroup = $state<any>(null);
	let showEditGroupModal = $state(false);

	// グループ作成
	let showCreateGroupModal = $state(false);
	let newName = $state('');
	let newDescription = $state('');
	let creating = $state(false);

	const setGroups = async () => {
		groups = await getGroups();
	};

	const createGroupHandler = async (e: SubmitEvent) => {
		e.preventDefault();
		creating = true;

		const res = await createNewGroup({ name: newName, description: newDescription }).catch(
			(error) => {
				toast.error(`${error}`);
				return null;
			}
		);

		creating = false;

		if (res) {
			toast.success($i18n.t('Group created successfully'));
			showCreateGroupModal = false;
			newName = '';
			newDescription = '';
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

	const updateDefaultPermissionsHandler = async (group: any) => {
		const res = await updateUserDefaultPermissions(group.permissions).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

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

						<div class="flex flex-col w-full mt-2">
							<div class=" mb-1 text-xs text-content-muted">{$i18n.t('Description')}</div>

							<Textarea
								className="w-full text-sm bg-transparent placeholder:text-content-subtle outline-hidden resize-none"
								rows={2}
								bind:value={newDescription}
								placeholder={$i18n.t('Group Description')}
							/>
						</div>
					</div>

					<div class="flex justify-end pt-3 text-sm font-medium gap-1.5">
						<button
							class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full flex flex-row space-x-1 items-center {creating
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

	<div class="mt-0.5 mb-2 gap-1 flex flex-col md:flex-row justify-between">
		<div class="flex md:self-center text-lg font-medium px-0.5">
			{$i18n.t('Groups')}
			<div class="flex self-center w-[1px] h-6 mx-2.5 bg-gray-50 dark:bg-gray-850"></div>

			<span class="text-lg font-medium text-content-muted">{groups.length}</span>
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
					<Tooltip content={$i18n.t('Create Group')}>
						<button
							class=" p-2 rounded-xl hover:bg-gray-100 dark:bg-gray-900 dark:hover:bg-gray-850 transition font-medium text-sm flex items-center space-x-1"
							aria-label={$i18n.t('Create Group')}
							onclick={() => {
								showCreateGroupModal = true;
							}}
						>
							<span class="i-lucide-plus size-3.5 block"></span>
						</button>
					</Tooltip>
				</div>
			</div>
		</div>
	</div>

	<div>
		{#if filteredGroups.length === 0}
			<div class="flex flex-col items-center justify-center h-40">
				<div class=" text-xl font-medium">
					{$i18n.t('Organize your users')}
				</div>

				<div class="mt-1 text-sm text-content-muted">
					{$i18n.t('Use groups to group your users and assign permissions.')}
				</div>

				<div class="mt-3">
					<button
						class=" px-4 py-1.5 text-sm rounded-full bg-black hover:bg-gray-800 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition font-medium flex items-center space-x-1"
						onclick={() => {
							showCreateGroupModal = true;
						}}
					>
						{$i18n.t('Create Group')}
					</button>
				</div>
			</div>
		{:else}
			<div>
				<div class=" flex items-center gap-3 justify-between text-xs uppercase px-1 font-bold">
					<div class="w-full">{$i18n.t('Group')}</div>

					<div class="w-full">{$i18n.t('Users')}</div>

					<div class="w-full"></div>
				</div>

				<hr class="mt-1.5 border-line" />

				{#each filteredGroups as group (group.id)}
					<div class="my-2">
						<button
							class="flex items-center gap-3 justify-between px-1 text-xs w-full transition"
							onclick={() => {
								selectedGroup = group;
								showEditGroupModal = true;
							}}
						>
							<div class="flex items-center gap-1.5 w-full font-medium">
								<span class="i-lucide-circle-user-round size-4 block"></span>
								{group.name}
							</div>

							<div class="flex items-center gap-1.5 w-full font-medium">
								{group.user_ids.length}

								<span class="i-lucide-user size-3.5 block"></span>
							</div>

							<div class="w-full flex justify-end">
								<div class=" rounded-lg p-1 hover:bg-gray-100 dark:hover:bg-gray-850 transition">
									<span class="i-lucide-pencil size-3.5 block"></span>
								</div>
							</div>
						</button>
					</div>
				{/each}
			</div>
		{/if}

		<hr class="mb-2 border-line" />

		<EditGroupModal
			bind:show={showDefaultPermissionsModal}
			tabs={['permissions']}
			bind:permissions={defaultPermissions}
			custom={false}
			onSubmit={updateDefaultPermissionsHandler}
		/>

		<button
			class="flex items-center justify-between rounded-lg w-full transition pt-1"
			onclick={() => {
				showDefaultPermissionsModal = true;
			}}
		>
			<div class="flex items-center gap-2.5">
				<div class="p-1.5 bg-black/5 dark:bg-white/10 rounded-full">
					<span class="i-lucide-users size-4 block"></span>
				</div>

				<div class="text-left">
					<div class=" text-sm font-medium">{$i18n.t('Default permissions')}</div>

					<div class="flex text-xs mt-0.5">
						{$i18n.t('applies to all users with the "user" role')}
					</div>
				</div>
			</div>

			<div>
				<span class="i-lucide-chevron-right size-4 block"></span>
			</div>
		</button>
	</div>
{/if}

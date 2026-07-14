<script lang="ts">
	import { getContext, onMount } from 'svelte';

	import Modal from '$lib/components/common/Modal.svelte';
	import Textarea from '$lib/components/common/Textarea.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import GroupPermissions from './GroupPermissions.svelte';
	import GroupUsers from './GroupUsers.svelte';

	const i18n = getContext('i18n');

	let {
		show = $bindable(false),
		edit = false,
		users = [],
		group = null,
		// custom=false は「既定権限の編集」モード(group エンティティを持たない)
		custom = true,
		tabs = ['general', 'permissions', 'users'],
		// llens(#66): 権限3層化 — workspace.skills = skill editor ロールのみ。
		permissions = $bindable({
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
		}),
		onSubmit = () => {},
		onDelete = () => {}
	}: {
		show?: boolean;
		edit?: boolean;
		users?: any[];
		group?: any;
		custom?: boolean;
		tabs?: string[];
		permissions?: any;
		onSubmit?: (group: any) => void | Promise<void>;
		onDelete?: () => void;
	} = $props();

	const TAB_ICONS: Record<string, string> = {
		general: 'i-lucide-settings',
		permissions: 'i-lucide-wrench',
		users: 'i-lucide-user-plus'
	};

	let selectedTab = $state('general');
	let loading = $state(false);
	let showDeleteConfirmDialog = $state(false);

	let name = $state('');
	let description = $state('');
	let userIds = $state<string[]>([]);

	const submitHandler = async () => {
		loading = true;

		await onSubmit({
			name,
			description,
			permissions,
			user_ids: userIds
		});

		loading = false;
		show = false;
	};

	const init = () => {
		if (group) {
			name = group.name;
			description = group.description;
			permissions = group?.permissions ?? {};
			userIds = group?.user_ids ?? [];
		}
	};

	// 開くたびに group から編集バッファを作り直す(show の変化検知でガード —
	// 依存追跡で編集中に再実行されるのを防ぐ)
	let lastShow = false;
	$effect(() => {
		if (show === lastShow) return;
		lastShow = show;
		if (show) {
			init();
		}
	});

	onMount(() => {
		selectedTab = tabs[0];
		init();
	});
</script>

<ConfirmDialog
	bind:show={showDeleteConfirmDialog}
	on:confirm={() => {
		onDelete();
		show = false;
	}}
/>

<Modal size="md" bind:show>
	<div>
		<div class=" flex justify-between text-content px-5 pt-4 mb-1.5">
			<div class=" text-lg font-medium self-center font-primary">
				{#if custom}
					{#if edit}
						{$i18n.t('Edit User Group')}
					{:else}
						{$i18n.t('Add User Group')}
					{/if}
				{:else}
					{$i18n.t('Edit Default Permissions')}
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

		<div class="flex flex-col md:flex-row w-full px-4 pb-4 md:space-x-4 text-content">
			<div class=" flex flex-col w-full sm:flex-row sm:justify-center sm:space-x-6">
				<form
					class="flex flex-col w-full"
					onsubmit={(e) => {
						e.preventDefault();
						submitHandler();
					}}
				>
					<div class="flex flex-col lg:flex-row w-full h-full pb-2 lg:space-x-4">
						<div
							class="flex flex-row overflow-x-auto gap-2.5 max-w-full lg:gap-1 lg:flex-col lg:flex-none lg:w-40 text-content text-sm font-medium text-left scrollbar-none"
						>
							{#each tabs as tab (tab)}
								<button
									class="px-0.5 py-1 max-w-fit w-fit rounded-lg flex-1 lg:flex-none flex text-right transition {selectedTab ===
									tab
										? ''
										: ' text-content-subtle hover:text-content'}"
									type="button"
									onclick={() => {
										selectedTab = tab;
									}}
								>
									<div class=" self-center mr-2">
										<span class="{TAB_ICONS[tab]} size-4 block"></span>
									</div>
									<div class=" self-center">
										{#if tab === 'general'}
											{$i18n.t('General')}
										{:else if tab === 'permissions'}
											{$i18n.t('Permissions')}
										{:else if tab === 'users'}
											{$i18n.t('Users')} ({userIds.length})
										{/if}
									</div>
								</button>
							{/each}
						</div>

						<div
							class="flex-1 mt-1 lg:mt-1 lg:h-[22rem] lg:max-h-[22rem] overflow-y-auto scrollbar-hidden"
						>
							{#if selectedTab === 'general'}
								<div class="flex gap-2">
									<div class="flex flex-col w-full">
										<div class=" mb-0.5 text-xs text-content-muted">{$i18n.t('Name')}</div>

										<div class="flex-1">
											<input
												class="w-full text-sm bg-transparent placeholder:text-content-subtle outline-hidden"
												type="text"
												bind:value={name}
												placeholder={$i18n.t('Group Name')}
												autocomplete="off"
												required
											/>
										</div>
									</div>
								</div>

								<div class="flex flex-col w-full mt-2">
									<div class=" mb-1 text-xs text-content-muted">{$i18n.t('Description')}</div>

									<div class="flex-1">
										<Textarea
											className="w-full text-sm bg-transparent placeholder:text-content-subtle outline-hidden resize-none"
											rows={4}
											bind:value={description}
											placeholder={$i18n.t('Group Description')}
										/>
									</div>
								</div>
							{:else if selectedTab === 'permissions'}
								<GroupPermissions bind:permissions />
							{:else if selectedTab === 'users'}
								<GroupUsers bind:userIds {users} />
							{/if}
						</div>
					</div>

					<div class="flex justify-between pt-3 text-sm font-medium gap-1.5">
						{#if edit}
							<button
								class="px-3.5 py-1.5 text-sm font-medium dark:bg-black dark:hover:bg-gray-900 dark:text-white bg-white text-black hover:bg-gray-100 transition rounded-full flex flex-row space-x-1 items-center"
								type="button"
								onclick={() => {
									showDeleteConfirmDialog = true;
								}}
							>
								{$i18n.t('Delete')}
							</button>
						{:else}
							<div></div>
						{/if}

						<button
							class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full flex flex-row space-x-1 items-center {loading
								? ' cursor-not-allowed'
								: ''}"
							type="submit"
							disabled={loading}
						>
							{$i18n.t('Save')}

							{#if loading}
								<span class="i-lucide-loader-circle size-4 block animate-spin ml-2 self-center"
								></span>
							{/if}
						</button>
					</div>
				</form>
			</div>
		</div>
	</div>
</Modal>

<script lang="ts">
	import { getContext, onMount } from 'svelte';

	import Modal from '$lib/components/common/Modal.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import GroupPermissions from './GroupPermissions.svelte';
	import GroupUsers from './GroupUsers.svelte';

	const i18n = getContext('i18n');

	let {
		show = $bindable(false),
		edit = false,
		users = [],
		group = null,
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
			}
		}),
		onSubmit = () => {},
		onDelete = () => {}
	}: {
		show?: boolean;
		edit?: boolean;
		users?: any[];
		group?: any;
		permissions?: any;
		onSubmit?: (group: any) => void | Promise<void>;
		onDelete?: () => void;
	} = $props();

	// llens(2026-07-16): General タブは廃止 — 名前はヘッダでインライン編集、
	// 説明は FE から撤去(値は保持してパススルー)。開いた直後 = Permissions
	const tabs = ['users', 'permissions'];

	const TAB_ICONS: Record<string, string> = {
		permissions: 'i-lucide-wrench',
		users: 'i-lucide-user-plus'
	};

	let selectedTab = $state('users');
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
		selectedTab = tabs[0];
		if (group) {
			name = group.name;
			// 説明は FE から撤去済みだが BE では必須 str — 既存値を保持してパススルー
			description = group.description ?? '';
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
		init();
	});
</script>

<ConfirmDialog
	bind:show={showDeleteConfirmDialog}
	onConfirm={() => {
		onDelete();
		show = false;
	}}
/>

<Modal size="md" bind:show>
	<form
		class="flex flex-col w-full"
		onsubmit={(e) => {
			e.preventDefault();
			submitHandler();
		}}
	>
		<div class=" flex justify-between gap-3 text-content px-5 pt-4 mb-1.5">
			<!-- 名前はタイトル位置でラベル付きインライン編集(General タブ廃止に伴い)。
			 編集できると分かるようコントロールの面を張る(モーダル内 = muted の 2 段ルール)。
			     ラベルは FunctionEditor 同型(単一行なので固定幅 w-24 は取らない) -->
			<div class="flex items-center gap-2 w-full min-w-0">
				<label class="shrink-0 text-sm font-semibold" for="group-name">
					{$i18n.t('Name')}
				</label>
				<input
					id="group-name"
					class="flex-1 min-w-0 text-lg font-medium self-center font-primary bg-surface-muted rounded-xs px-2 py-0.5 placeholder:text-content-subtle outline-hidden"
					type="text"
					bind:value={name}
					placeholder={$i18n.t('Group Name')}
					autocomplete="off"
					required
				/>
			</div>
			<button
				class="self-center"
				type="button"
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
				<div class="flex flex-col w-full">
					<div class="flex flex-col lg:flex-row w-full h-full pb-2 lg:space-x-4">
						<div
							class="flex flex-row overflow-x-auto gap-2.5 max-w-full lg:gap-1 lg:flex-col lg:flex-none lg:w-40 text-content text-sm font-medium text-left scrollbar-none"
						>
							{#each tabs as tab (tab)}
								<button
									class="px-0.5 py-1 max-w-fit w-fit rounded-lg flex-1 lg:flex-none flex text-right {selectedTab ===
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
										{#if tab === 'permissions'}
											{$i18n.t('Permissions')}
										{:else if tab === 'users'}
											{$i18n.t('Users')} ({userIds.length})
										{/if}
									</div>
								</button>
							{/each}
						</div>

						<div class="flex-1 mt-1 lg:mt-1 lg:h-[22rem] lg:max-h-[22rem] overflow-y-auto">
							{#if selectedTab === 'permissions'}
								<GroupPermissions bind:permissions />
							{:else if selectedTab === 'users'}
								<GroupUsers bind:userIds {users} />
							{/if}
						</div>
					</div>

					<div class="flex justify-between pt-3 text-sm font-medium gap-1.5">
						{#if edit}
							<button
								class="px-3.5 py-1.5 text-sm font-medium dark:bg-black dark:hover:bg-gray-900 dark:text-white bg-white text-black hover:bg-gray-100 rounded-full flex flex-row space-x-1 items-center"
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
							class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 rounded-full flex flex-row space-x-1 items-center {loading
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
				</div>
			</div>
		</div>
	</form>
</Modal>

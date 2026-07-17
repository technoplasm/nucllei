<script lang="ts">
	import { getContext, onMount } from 'svelte';

	const i18n = getContext('i18n');

	import { getGroups } from '$lib/apis/groups';

	export let onChange: Function = () => {};

	// llens(#66): ACL は read(使える範囲)一本。write 側の UI・データは廃止
	// (定義編集の権限はロールで決まる — model/tool=admin、skill=editor)。
	type AccessControlValue = {
		read?: { public?: boolean; group_ids?: string[]; user_ids?: string[] };
	};
	export let accessControl: AccessControlValue | null = {};

	export let allowPublic = true;

	// llens(#69): file 用 variant。file の ACL 意味論は他エンティティと逆:
	//   null = 非公開(owner のみ・既定)
	//   {read:{group_ids,user_ids}} = 列挙先に公開(グループ共有)
	//   {read:{public:true}} = 全体公開
	// variant='file' では 3 値セレクタで受け渡しも file 意味論のまま行う
	// (default variant の「null=Public」正規化・onMount 書き換えは通さない)。
	export let variant: 'default' | 'file' = 'default';

	let selectedGroupId = '';
	let groups: { id: string; name: string }[] = [];

	// file variant の現在モード(accessControl から導出 — 二重 state を持たない)
	$: fileMode =
		accessControl === null ? 'private' : accessControl?.read?.public ? 'public' : 'groups';

	const setFileMode = (mode: string) => {
		if (mode === 'private') {
			accessControl = null;
		} else if (mode === 'public') {
			accessControl = { read: { public: true } };
		} else {
			accessControl = {
				read: {
					group_ids: accessControl?.read?.group_ids ?? [],
					user_ids: accessControl?.read?.user_ids ?? []
				}
			};
		}
	};

	$: if (variant === 'default' && !allowPublic && accessControl === null) {
		accessControl = {
			read: {
				group_ids: [],
				user_ids: []
			}
		};
		onChange(accessControl);
	}

	onMount(async () => {
		groups = await getGroups();

		if (variant === 'file') {
			// 意味論は変えず shape だけ正規化(グループ共有時に read.group_ids を保証)
			if (accessControl !== null && !accessControl?.read?.public) {
				accessControl = {
					read: {
						group_ids: accessControl?.read?.group_ids ?? [],
						user_ids: accessControl?.read?.user_ids ?? []
					}
				};
			}
			return;
		}

		if (accessControl === null) {
			if (allowPublic) {
				accessControl = null;
			} else {
				accessControl = {
					read: {
						group_ids: [],
						user_ids: []
					}
				};
				onChange(accessControl);
			}
		} else {
			accessControl = {
				read: {
					group_ids: accessControl?.read?.group_ids ?? [],
					user_ids: accessControl?.read?.user_ids ?? []
				}
			};
		}
	});

	$: onChange(accessControl);

	// グループ選択 UI を出す条件(default: private 選択時 / file: グループ共有選択時)
	$: showGroups = variant === 'file' ? fileMode === 'groups' : accessControl !== null;

	$: if (selectedGroupId) {
		onSelectGroup();
	}

	const onSelectGroup = () => {
		if (selectedGroupId !== '' && accessControl?.read) {
			accessControl.read.group_ids = [...(accessControl.read.group_ids ?? []), selectedGroupId];

			selectedGroupId = '';
		}
	};
</script>

<div class=" rounded-lg flex flex-col gap-2">
	<div class="">
		<div class=" text-sm font-semibold mb-1">{$i18n.t('Visibility')}</div>

		<div class="flex gap-2.5 items-center mb-1">
			<div>
				<div class=" p-2 bg-black/5 dark:bg-white/5 rounded-full">
					{#if variant === 'file' && fileMode === 'groups'}
						<span class="i-lucide-users size-5 block"></span>
					{:else if variant === 'file' ? fileMode === 'private' : accessControl !== null}
						<span class="i-lucide-lock size-5 block"></span>
					{:else}
						<span class="i-lucide-globe size-5 block"></span>
					{/if}
				</div>
			</div>

			<div>
				{#if variant === 'file'}
					<select
						id="models"
						class="outline-hidden bg-surface-muted px-2 py-1 text-sm font-medium rounded-xs block w-fit pr-8 max-w-full"
						value={fileMode}
						on:change={(e) => {
							setFileMode(e.currentTarget.value);
						}}
					>
						<option class=" text-content" value="private" selected>{$i18n.t('Private')}</option>
						<option class=" text-content" value="groups">{$i18n.t('Shared')}</option>
						<option class=" text-content" value="public">{$i18n.t('Public')}</option>
					</select>

					<div class=" text-xs text-content-muted font-medium">
						{#if fileMode === 'private'}
							{$i18n.t('Only you can access')}
						{:else if fileMode === 'groups'}
							{$i18n.t('Only select users and groups with permission can access')}
						{:else}
							{$i18n.t('Accessible to all users')}
						{/if}
					</div>
				{:else}
					<select
						id="models"
						class="outline-hidden bg-surface-muted px-2 py-1 text-sm font-medium rounded-xs block w-fit pr-8 max-w-full"
						value={accessControl !== null ? 'private' : 'public'}
						on:change={(e) => {
							if (e.currentTarget.value === 'public') {
								accessControl = null;
							} else {
								accessControl = {
									read: {
										group_ids: [],
										user_ids: []
									}
								};
							}
						}}
					>
						<option class=" text-content" value="private" selected>{$i18n.t('Private')}</option>
						{#if allowPublic}
							<option class=" text-content" value="public" selected>{$i18n.t('Public')}</option>
						{/if}
					</select>

					<div class=" text-xs text-content-muted font-medium">
						{#if accessControl !== null}
							{$i18n.t('Only select users and groups with permission can access')}
						{:else}
							{$i18n.t('Accessible to all users')}
						{/if}
					</div>
				{/if}
			</div>
		</div>
	</div>
	{#if showGroups}
		{@const accessGroups = groups.filter((group) =>
			(accessControl?.read?.group_ids ?? []).includes(group.id)
		)}
		<div>
			<div class="">
				<div class="flex justify-between mb-1.5">
					<div class="text-sm font-semibold">
						{$i18n.t('Groups')}
					</div>
				</div>

				<div class="mb-1">
					<div class="flex w-full">
						<div class="flex flex-1 items-center">
							<div class="w-full px-0.5">
								<select
									class="outline-hidden bg-surface-muted px-2 py-1 text-sm rounded-xs block w-full pr-8 max-w-full
									{selectedGroupId ? '' : 'text-content-subtle'}"
									bind:value={selectedGroupId}
								>
									<option class=" text-content" value="" disabled selected
										>{$i18n.t('Select a group')}</option
									>
									{#each groups.filter((group) => !(accessControl?.read?.group_ids ?? []).includes(group.id)) as group}
										<option class=" text-content" value={group.id}>{group.name}</option>
									{/each}
								</select>
							</div>
						</div>
					</div>
				</div>

				<hr class=" border-gray-100 dark:border-gray-700/10 mt-1.5 mb-2.5 w-full" />

				<div class="flex flex-col gap-2 mb-1 px-0.5">
					{#if accessGroups.length > 0}
						{#each accessGroups as group}
							<div class="flex items-center gap-3 justify-between text-xs w-full transition">
								<div class="flex items-center gap-1.5 w-full font-medium">
									<div>
										<span class="i-lucide-circle-user size-4 block"></span>
									</div>

									<div>
										{group.name}
									</div>
								</div>

								<div class="w-full flex justify-end items-center gap-0.5">
									<button
										class=" rounded-full p-1 hover:bg-gray-100 dark:hover:bg-gray-850 transition"
										type="button"
										aria-label={$i18n.t('Remove')}
										on:click={() => {
											if (accessControl?.read) {
												accessControl.read.group_ids = (accessControl.read.group_ids ?? []).filter(
													(id) => id !== group.id
												);
											}
										}}
									>
										<span class="i-lucide-x size-3.5 block"></span>
									</button>
								</div>
							</div>
						{/each}
					{:else}
						<div class="flex items-center justify-center">
							<div class="text-content-subtle text-xs text-center py-2 px-10">
								{$i18n.t('No groups with access, add a group to grant access')}
							</div>
						</div>
					{/if}
				</div>
			</div>
		</div>
	{/if}
</div>

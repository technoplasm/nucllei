<script lang="ts">
	import { getContext, onMount, untrack } from 'svelte';

	const i18n = getContext('i18n');

	import { getGroups } from '$lib/apis/groups';
	import { searchUsers } from '$lib/apis/users';

	// llens(#66): ACL は read(使える範囲)一本。write 側の UI・データは廃止
	// (定義編集の権限はロールで決まる — model/tool=admin、skill=editor)。
	// llens(#85): UI を刷新 — 公開範囲はセグメント(常時可視・1 クリック)、対象指定は
	// グループ・ユーザー共通のチップ入力(user_ids がここで初めて編集可能になった)。
	type AccessControlValue = {
		read?: { public?: boolean; group_ids?: string[]; user_ids?: string[] };
	};

	let {
		onChange = () => {},
		accessControl = $bindable({}),
		allowPublic = true,
		// llens(#69): file 用 variant。file の ACL 意味論は他エンティティと逆:
		//   null = 非公開(owner のみ・既定)
		//   {read:{group_ids,user_ids}} = 列挙先に公開(グループ共有)
		//   {read:{public:true}} = 全体公開
		// variant='file' では 3 セグメントで受け渡しも file 意味論のまま行う
		// (default variant の「null=Public」正規化・onMount 書き換えは通さない)。
		variant = 'default'
	}: {
		onChange?: (accessControl: AccessControlValue | null) => void;
		accessControl?: AccessControlValue | null;
		allowPublic?: boolean;
		variant?: 'default' | 'file';
	} = $props();

	let groups = $state<{ id: string; name: string }[]>([]);
	// 既存 ACL の user_ids と検索候補から解決済みのユーザー表示情報を貯める
	let knownUsers = $state<Record<string, { name: string; email: string }>>({});

	// ---- モード(セグメント)----

	// file variant の現在モード(accessControl から導出 — 二重 state を持たない)
	const fileMode = $derived(
		accessControl === null ? 'private' : accessControl?.read?.public ? 'public' : 'groups'
	);
	// default variant: null = 公開 / それ以外 = 限定
	const mode = $derived(
		variant === 'file' ? fileMode : accessControl === null ? 'public' : 'limited'
	);

	const setMode = (next: string) => {
		if (variant === 'file' ? next === 'private' : next === 'public') {
			accessControl = null;
		} else if (next === 'public') {
			// file variant の全体公開
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

	// 公開が先(広い → 狭い、2026-07-19 のユーザー編集の順を踏襲)
	const SEGMENTS = $derived(
		variant === 'file'
			? [
					{ mode: 'public', icon: 'i-lucide-globe', label: 'Public' },
					{ mode: 'groups', icon: 'i-lucide-users', label: 'Shared' },
					{ mode: 'private', icon: 'i-lucide-lock', label: 'Private' }
				]
			: [
					...(allowPublic ? [{ mode: 'public', icon: 'i-lucide-globe', label: 'Public' }] : []),
					{ mode: 'limited', icon: 'i-lucide-lock', label: 'Private' }
				]
	);

	const description = $derived.by(() => {
		if (variant === 'file' && mode === 'private') {
			return $i18n.t('Only you can access');
		}
		if (mode === 'public') {
			return $i18n.t('Accessible to all users');
		}
		return $i18n.t('Only select users and groups with permission can access');
	});

	$effect(() => {
		if (variant === 'default' && !allowPublic && accessControl === null) {
			accessControl = {
				read: {
					group_ids: [],
					user_ids: []
				}
			};
			untrack(() => onChange(accessControl));
		}
	});

	onMount(async () => {
		groups = await getGroups();

		const userIds = accessControl?.read?.user_ids ?? [];
		if (userIds.length > 0) {
			// 既存 ACL のユーザーチップ表示用に名前解決(失敗しても id 表示に落ちるだけ)
			const users = await searchUsers({ ids: userIds }).catch(() => []);
			knownUsers = {
				...knownUsers,
				...Object.fromEntries(users.map((u) => [u.id, { name: u.name, email: u.email }]))
			};
		}

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

	// 増減は新 object 再代入で行う(bindable プロップの set として確実に伝播させ、
	// この effect の依存(accessControl 参照)にも掛ける — 深い mutate だと参照が
	// 変わらず onChange が発火しない)。onChange 本体は untrack に置く — 親コールバック
	// 側の読み書きまで依存に取り込むと自己ループになる(罠(7))。
	$effect(() => {
		void accessControl;
		untrack(() => onChange(accessControl));
	});

	// メンバー編集 UI を出す条件(default: 限定時 / file: グループ共有時)
	const showMembers = $derived(variant === 'file' ? fileMode === 'groups' : accessControl !== null);

	// ---- チップ入力(グループ・ユーザー共通)----

	let searchQuery = $state('');
	let searchInput = $state<HTMLInputElement | null>(null);
	let suggestOpen = $state(false);
	let userResults = $state<{ id: string; name: string; email: string }[]>([]);
	let searchDebounce: ReturnType<typeof setTimeout> | null = null;

	const selectedGroupIds = $derived(accessControl?.read?.group_ids ?? []);
	const selectedUserIds = $derived(accessControl?.read?.user_ids ?? []);

	const groupSuggestions = $derived(
		groups.filter(
			(group) =>
				!selectedGroupIds.includes(group.id) &&
				(searchQuery.trim() === '' ||
					group.name.toLowerCase().includes(searchQuery.trim().toLowerCase()))
		)
	);
	const userSuggestions = $derived(userResults.filter((u) => !selectedUserIds.includes(u.id)));

	const onSearchInput = () => {
		if (searchDebounce) {
			clearTimeout(searchDebounce);
		}
		const q = searchQuery.trim();
		if (q === '') {
			userResults = [];
			return;
		}
		searchDebounce = setTimeout(async () => {
			const users = await searchUsers({ query: q }).catch(() => []);
			knownUsers = {
				...knownUsers,
				...Object.fromEntries(users.map((u) => [u.id, { name: u.name, email: u.email }]))
			};
			userResults = users;
		}, 250);
	};

	const patchRead = (patch: Partial<NonNullable<AccessControlValue['read']>>) => {
		if (accessControl?.read) {
			accessControl = { read: { ...accessControl.read, ...patch } };
		}
	};

	const addGroup = (id: string) => {
		patchRead({ group_ids: [...selectedGroupIds, id] });
		searchQuery = '';
		userResults = [];
		searchInput?.focus();
	};
	const addUser = (id: string) => {
		patchRead({ user_ids: [...selectedUserIds, id] });
		searchQuery = '';
		userResults = [];
		searchInput?.focus();
	};
	const removeGroup = (id: string) => {
		patchRead({ group_ids: selectedGroupIds.filter((g) => g !== id) });
	};
	const removeUser = (id: string) => {
		patchRead({ user_ids: selectedUserIds.filter((u) => u !== id) });
	};

	const onSearchKeydown = (e: KeyboardEvent) => {
		if (e.key === 'Enter') {
			e.preventDefault();
			if (groupSuggestions.length > 0) {
				addGroup(groupSuggestions[0].id);
			} else if (userSuggestions.length > 0) {
				addUser(userSuggestions[0].id);
			}
		} else if (e.key === 'Escape') {
			suggestOpen = false;
		} else if (e.key === 'Backspace' && searchQuery === '') {
			// 空欄で Backspace = 末尾チップ(ユーザー → グループの順)を除去
			if (selectedUserIds.length > 0) {
				removeUser(selectedUserIds[selectedUserIds.length - 1]);
			} else if (selectedGroupIds.length > 0) {
				removeGroup(selectedGroupIds[selectedGroupIds.length - 1]);
			}
		}
	};
</script>

<div class="flex flex-col gap-2.5">
	<div>
		<div class="text-sm font-semibold mb-1.5">{$i18n.t('Visibility')}</div>

		<div
			class="inline-flex gap-0.5 p-0.5 bg-surface-muted rounded-lg"
			role="group"
			aria-label={$i18n.t('Visibility')}
		>
			{#each SEGMENTS as segment (segment.mode)}
				{@const active = mode === segment.mode}
				<button
					class="flex items-center gap-1.5 px-3 py-1 text-sm font-medium rounded-md transition {active
						? 'bg-surface-raised text-content shadow-sm'
						: 'text-content-muted hover:text-content'}"
					type="button"
					aria-pressed={active}
					onclick={() => setMode(segment.mode)}
				>
					<span class="{segment.icon} size-3.5 block"></span>
					{$i18n.t(segment.label)}
				</button>
			{/each}
		</div>

		<div class="text-xs text-content-muted mt-1">{description}</div>
	</div>

	{#if showMembers}
		{@const accessGroups = groups.filter((group) => selectedGroupIds.includes(group.id))}
		<div class="relative">
			<!-- チップ入力: 選択済みのグループ・ユーザー + 検索入力を 1 面に(入力面 = rounded-xs 規律)。
			     label なので余白クリックでネイティブに input へフォーカスが移る -->
			<label
				class="flex flex-wrap items-center gap-1 px-1.5 py-1 bg-surface-muted rounded-xs cursor-text"
			>
				{#each accessGroups as group (group.id)}
					<span
						class="inline-flex items-center gap-1 pl-2 pr-0.5 py-0.5 bg-surface-raised border border-line rounded-full text-xs font-medium"
					>
						<span class="i-lucide-users size-3 block text-content-muted"></span>
						{group.name}
						<button
							class="p-0.5 rounded-full text-content-subtle hover:text-content hover:bg-surface-muted"
							type="button"
							aria-label={$i18n.t('Remove')}
							onclick={(e) => {
								e.stopPropagation();
								removeGroup(group.id);
							}}
						>
							<span class="i-lucide-x size-3 block"></span>
						</button>
					</span>
				{/each}
				{#each selectedUserIds as userId (userId)}
					<span
						class="inline-flex items-center gap-1 pl-2 pr-0.5 py-0.5 bg-surface-raised border border-line rounded-full text-xs font-medium"
						title={knownUsers[userId]?.email ?? userId}
					>
						<span class="i-lucide-circle-user size-3 block text-content-muted"></span>
						{knownUsers[userId]?.name ?? userId}
						<button
							class="p-0.5 rounded-full text-content-subtle hover:text-content hover:bg-surface-muted"
							type="button"
							aria-label={$i18n.t('Remove')}
							onclick={(e) => {
								e.stopPropagation();
								removeUser(userId);
							}}
						>
							<span class="i-lucide-x size-3 block"></span>
						</button>
					</span>
				{/each}
				<input
					class="flex-1 min-w-32 px-1 py-0.5 text-sm bg-transparent outline-hidden placeholder:text-content-subtle"
					bind:this={searchInput}
					bind:value={searchQuery}
					placeholder={$i18n.t('Add groups or users')}
					oninput={onSearchInput}
					onfocus={() => (suggestOpen = true)}
					onblur={() => setTimeout(() => (suggestOpen = false), 150)}
					onkeydown={onSearchKeydown}
				/>
			</label>

			{#if suggestOpen && (groupSuggestions.length > 0 || userSuggestions.length > 0)}
				<div
					class="absolute left-0 right-0 top-full mt-1 z-50 bg-surface-raised border border-line rounded-lg shadow-sm overflow-hidden max-h-56 overflow-y-auto"
				>
					{#if groupSuggestions.length > 0}
						<div
							class="px-2.5 pt-1.5 pb-0.5 text-[10px] font-semibold tracking-wider uppercase text-content-subtle"
						>
							{$i18n.t('Groups')}
						</div>
						{#each groupSuggestions as group (group.id)}
							<button
								class="flex items-center gap-2 w-full px-2.5 py-1.5 text-sm text-left hover:bg-surface-hover"
								type="button"
								onmousedown={(e) => {
									e.preventDefault();
									addGroup(group.id);
								}}
							>
								<span class="i-lucide-users size-3.5 block text-content-muted"></span>
								{group.name}
							</button>
						{/each}
					{/if}
					{#if userSuggestions.length > 0}
						<div
							class="px-2.5 pt-1.5 pb-0.5 text-[10px] font-semibold tracking-wider uppercase text-content-subtle"
						>
							{$i18n.t('Users')}
						</div>
						{#each userSuggestions as suggestUser (suggestUser.id)}
							<button
								class="flex items-center gap-2 w-full px-2.5 py-1.5 text-sm text-left hover:bg-surface-hover"
								type="button"
								onmousedown={(e) => {
									e.preventDefault();
									addUser(suggestUser.id);
								}}
							>
								<span class="i-lucide-circle-user size-3.5 block text-content-muted"></span>
								{suggestUser.name}
								<span class="text-xs text-content-subtle truncate">{suggestUser.email}</span>
							</button>
						{/each}
					{/if}
				</div>
			{/if}
		</div>
	{/if}
</div>

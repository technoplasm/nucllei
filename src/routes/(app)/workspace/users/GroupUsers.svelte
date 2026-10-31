<script lang="ts">
	import { getContext } from 'svelte';

	import { WEBUI_BASE_URL } from '$lib/constants';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Badge from '$lib/components/common/Badge.svelte';

	const i18n = getContext('i18n');

	// writeMode / writers: グループの置き場(/ext/groups/<名前>)への書き込み(2026-10-31 ken、案 A)。
	// 'members' = メンバー全員が書ける(既定)、'listed' = writers のメンバーだけ書ける。
	// 所属(userIds)の意味は変えない。writers は保存時に BE が userIds との積に揃えるが、
	// 画面でもメンバーから外したら writers から落とす
	let {
		users = [],
		userIds = $bindable([]),
		writeMode = $bindable('members'),
		writers = $bindable([])
	}: {
		users?: any[];
		userIds?: string[];
		writeMode?: 'members' | 'listed';
		writers?: string[];
	} = $props();

	let query = $state('');

	const filteredUsers = $derived(
		users
			.filter((user) => {
				// 管理者も候補に出す(ken 指示 2026-10-21。以前は role === 'admin' を隠していたが
				// BE は元々 target の role を見ていない。admin の所属が効くのは {{USER_GROUPS}} だけ:
				// 権限は所属に関係なく全部通り、/ext/groups は admin には全グループ見える)
				if (query === '') {
					return true;
				}

				return (
					user.name.toLowerCase().includes(query.toLowerCase()) ||
					user.email.toLowerCase().includes(query.toLowerCase())
				);
			})
			.sort((a, b) => {
				const aUserIndex = userIds.indexOf(a.id);
				const bUserIndex = userIds.indexOf(b.id);

				// メンバーを先頭に、メンバー同士は userIds 順、非メンバー同士は名前順
				if (aUserIndex !== -1 && bUserIndex === -1) return -1;
				if (bUserIndex !== -1 && aUserIndex === -1) return 1;
				if (aUserIndex !== -1 && bUserIndex !== -1) return aUserIndex - bUserIndex;
				return a.name.localeCompare(b.name);
			})
	);
</script>

<div>
	<div class="flex flex-wrap items-center gap-x-3 gap-y-1.5 mb-3">
		<label class="text-xs font-medium shrink-0" for="group-ext-write">
			{$i18n.t('Writing to the group folder')}
		</label>
		<select
			id="group-ext-write"
			class="min-w-0 rounded-xs py-1 pl-2 pr-7 text-sm text-content bg-surface-muted hover:bg-surface-hover outline-hidden"
			bind:value={writeMode}
		>
			<option value="members">{$i18n.t('All members can write')}</option>
			<option value="listed">{$i18n.t('Only selected members can write')}</option>
		</select>
	</div>
	{#if writeMode === 'listed'}
		<div class="-mt-1.5 mb-3 text-xs text-content-subtle">
			{$i18n.t('Members without “Write” can only read the group folder. Admins can always write.')}
		</div>
	{/if}

	<div class="flex w-full">
		<div class="flex flex-1">
			<div class=" self-center mr-3">
				<span class="i-lucide-search size-4 block"></span>
			</div>
			<input
				class=" w-full text-sm pr-4 rounded-r-xl outline-hidden bg-transparent"
				bind:value={query}
				placeholder={$i18n.t('Search')}
			/>
		</div>
	</div>

	<div class="mt-3 max-h-[22rem] overflow-y-auto">
		<div class="flex flex-col gap-2.5">
			{#if filteredUsers.length > 0}
				{#each filteredUsers as user (user.id)}
					<div class="flex flex-row items-center gap-3 w-full text-sm">
						<div class="flex items-center">
							<Checkbox
								state={userIds.includes(user.id) ? 'checked' : 'unchecked'}
								onChange={(state) => {
									if (state === 'checked') {
										userIds = [...userIds, user.id];
									} else {
										userIds = userIds.filter((id) => id !== user.id);
										writers = writers.filter((id) => id !== user.id);
									}
								}}
							/>
						</div>

						<div class="flex w-full items-center justify-between">
							<Tooltip content={user.email} placement="top-start">
								<div class="flex">
									<img
										class=" rounded-full size-5 object-cover mr-2.5"
										src={user.profile_image_url.startsWith(WEBUI_BASE_URL) ||
										user.profile_image_url.startsWith('https://www.gravatar.com/avatar/') ||
										user.profile_image_url.startsWith('data:')
											? user.profile_image_url
											: `/user.png`}
										alt="user"
									/>

									<div class=" font-medium self-center">{user.name}</div>
								</div>
							</Tooltip>

							<div class="flex items-center gap-1.5">
								{#if writeMode === 'listed' && userIds.includes(user.id)}
									<!-- 管理者は書き込みの指定に関係なく常に書ける(role で決まる)— 固定表示 -->
									<Tooltip
										content={user.role === 'admin' ? $i18n.t('Admins can always write.') : ''}
										placement="top"
									>
										<label
											class="flex items-center gap-1 mr-1 text-xs text-content-muted {user.role ===
											'admin'
												? 'cursor-not-allowed'
												: 'cursor-pointer'}"
										>
											<Checkbox
												state={user.role === 'admin' || writers.includes(user.id)
													? 'checked'
													: 'unchecked'}
												disabled={user.role === 'admin'}
												onChange={(state) => {
													if (state === 'checked') {
														writers = [...writers.filter((id) => id !== user.id), user.id];
													} else {
														writers = writers.filter((id) => id !== user.id);
													}
												}}
											/>
											{$i18n.t('Write access')}
										</label>
									</Tooltip>
								{/if}
								{#if user.role === 'admin'}
									<Badge type="info" content={$i18n.t('admin')} />
								{/if}
								{#if userIds.includes(user.id)}
									<Badge type="success" content={$i18n.t('member')} />
								{/if}
							</div>
						</div>
					</div>
				{/each}
			{:else}
				<div class="text-content-subtle text-xs text-center py-2 px-10">
					{$i18n.t('No users were found.')}
				</div>
			{/if}
		</div>
	</div>
</div>

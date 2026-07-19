<script lang="ts">
	import { getContext } from 'svelte';

	import { WEBUI_BASE_URL } from '$lib/constants';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Badge from '$lib/components/common/Badge.svelte';

	const i18n = getContext('i18n');

	let { users = [], userIds = $bindable([]) }: { users?: any[]; userIds?: string[] } = $props();

	let query = $state('');

	const filteredUsers = $derived(
		users
			.filter((user) => {
				if (user?.role === 'admin') {
					return false;
				}

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

							{#if userIds.includes(user.id)}
								<Badge type="success" content={$i18n.t('member')} />
							{/if}
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

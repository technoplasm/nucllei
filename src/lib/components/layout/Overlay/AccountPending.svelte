<script lang="ts">
	import { getAdminDetails, userSignOut } from '$lib/apis/auths';
	import { onMount, getContext } from 'svelte';
	import { user } from '$lib/stores';

	const i18n = getContext('i18n');

	// llens(#31): pending(承認待ち)と disabled(無効化)で案内文言を分ける。
	// 案内画面の本仕様は #59 — ここは role 分岐の最小実装。
	const isDisabled = $derived($user?.role === 'disabled');

	let adminDetails = $state<{ name?: string; email?: string } | null>(null);

	onMount(async () => {
		adminDetails = await getAdminDetails().catch((err) => {
			console.error(err);
			return null;
		});
	});
</script>

<div class="fixed w-full h-full flex z-999">
	<div
		class="absolute w-full h-full backdrop-blur-lg bg-white/10 dark:bg-gray-900/50 flex justify-center"
	>
		<div class="m-auto pb-10 flex flex-col justify-center">
			<div class="max-w-md">
				{#if isDisabled}
					<div class="text-center dark:text-white text-2xl font-medium z-50">
						{$i18n.t('Account Disabled')}
					</div>

					<div class=" mt-4 text-center text-sm text-content w-full">
						{$i18n.t(
							'Your account has been disabled. Please contact the administrator for details.'
						)}
					</div>
				{:else}
					<div class="text-center dark:text-white text-2xl font-medium z-50">
						{$i18n.t('Account Activation Pending')}<br />
						{$i18n.t('Contact Admin for WebUI Access')}
					</div>

					<div class=" mt-4 text-center text-sm text-content w-full">
						{$i18n.t('Your account status is currently pending activation.')}<br />
						{$i18n.t(
							'To access the WebUI, please reach out to the administrator. Admins can manage user statuses from the Admin Panel.'
						)}
					</div>
				{/if}

				{#if adminDetails}
					<div class="mt-4 text-sm font-medium text-center">
						<div>{$i18n.t('Admin')}: {adminDetails.name} ({adminDetails.email})</div>
					</div>
				{/if}

				<div class=" mt-6 mx-auto relative group w-fit">
					<button
						class="relative z-20 flex px-5 py-2 rounded-full bg-white border border-gray-100 dark:border-none hover:bg-gray-100 text-content transition font-medium text-sm"
						onclick={async () => {
							location.href = '/';
						}}
					>
						{$i18n.t('Check Again')}
					</button>

					<button
						class="text-xs text-center w-full mt-2 text-content-muted underline"
						onclick={async () => {
							// llens(#70 B1): cookie セッションの破棄は backend の signout が行う
							await userSignOut().catch(() => {});
							localStorage.removeItem('token');
							location.href = '/auth';
						}}>{$i18n.t('Sign Out')}</button
					>
				</div>
			</div>
		</div>
	</div>
</div>

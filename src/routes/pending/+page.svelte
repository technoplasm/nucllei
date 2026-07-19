<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { goto } from '$app/navigation';
	import { getPendingMessage, userSignOut } from '$lib/apis/auths';
	import { user, WEBUI_NAME } from '$lib/stores';
	import Markdown from '$lib/components/chat/Messages/Markdown.svelte';

	const i18n = getContext('i18n');

	// llens(#31/#59): pending(承認待ち)と disabled(無効化)を同じページで案内する。
	// 旧 layout/Overlay/AccountPending(チャット画面へのオーバーレイ)を専用ページ化した。
	const isDisabled = $derived($user?.role === 'disabled');

	// llens(#59): 配備先固有の案内(DATA_DIR/pending.md)。無ければ既定文言(i18n)。
	let pendingMessage = $state<string | null>(null);

	onMount(async () => {
		// root layout が未ログインを /auth へ飛ばすので、ここは role の整合だけ見る
		if ($user && ['user', 'admin'].includes($user.role)) {
			await goto('/');
			return;
		}
		if (!isDisabled) {
			pendingMessage = (await getPendingMessage().catch(() => null))?.content ?? null;
		}
	});
</script>

<svelte:head>
	<title>
		{`${$WEBUI_NAME}`}
	</title>
</svelte:head>

<div class="w-full h-screen max-h-[100dvh] flex text-content bg-surface">
	<div class="m-auto pb-10 flex flex-col justify-center">
		<div class="max-w-md px-4">
			{#if isDisabled}
				<div class="text-center dark:text-white text-2xl font-medium">
					{$i18n.t('Account Disabled')}
				</div>

				<div class=" mt-4 text-center text-sm text-content w-full">
					{$i18n.t('Your account has been disabled. Please contact the administrator for details.')}
				</div>
			{:else if pendingMessage}
				<!-- 配備先 md がタイトル込みで画面を持つ(既定タイトルと二重にしない) -->
				<div class="text-sm text-content w-full markdown-prose">
					<Markdown id="pending-message" content={pendingMessage} />
				</div>
			{:else}
				<div class="text-center dark:text-white text-2xl font-medium">
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

			<div class=" mt-6 mx-auto relative group w-fit">
				<button
					class="relative z-20 flex px-5 py-2 rounded-full bg-primary hover:bg-primary-hover text-primary-content transition font-medium text-sm"
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

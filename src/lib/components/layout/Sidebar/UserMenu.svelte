<script lang="ts">
	import { DropdownMenu } from 'bits-ui';
	import { getContext, type Snippet } from 'svelte';
	import { fade } from 'svelte/transition';

	import { showSettings, mobile, showSidebar, user } from '$lib/stores';
	import { userSignOut } from '$lib/apis/auths';

	const i18n = getContext('i18n');

	let {
		show = $bindable(false),
		className = 'max-w-[240px]',
		onShow = undefined,
		children
	}: {
		show?: boolean;
		className?: string;
		onShow?: (name: string) => void;
		children?: Snippet;
	} = $props();
</script>

<DropdownMenu.Root bind:open={show}>
	<DropdownMenu.Trigger>
		{@render children?.()}
	</DropdownMenu.Trigger>

	<DropdownMenu.Content
		class="w-full {className} text-sm rounded-xl px-1 py-1.5 z-50 bg-white dark:bg-gray-850 dark:text-white shadow-lg font-primary"
		sideOffset={8}
		side="bottom"
		align="start"
		transition={(e) => fade(e, { duration: 100 })}
	>
		<button
			class="flex rounded-md py-2 px-3 w-full hover:bg-gray-50 dark:hover:bg-gray-800"
			onclick={async () => {
				await showSettings.set(true);
				show = false;

				if ($mobile) {
					showSidebar.set(false);
				}
			}}
		>
			<div class=" self-center mr-3">
				<span class="i-lucide-settings size-5 block"></span>
			</div>
			<div class=" self-center truncate">{$i18n.t('Settings')}</div>
		</button>

		<button
			class="flex rounded-md py-2 px-3 w-full hover:bg-gray-50 dark:hover:bg-gray-800"
			onclick={() => {
				onShow?.('archived-chat');
				show = false;

				if ($mobile) {
					showSidebar.set(false);
				}
			}}
		>
			<div class=" self-center mr-3">
				<span class="i-lucide-archive size-5 block"></span>
			</div>
			<div class=" self-center truncate">{$i18n.t('Archived Chats')}</div>
		</button>

		<!-- llens(#48): 管理者パネル導線は撤去(Admin を Workspace に統合)。管理面は Sidebar の Workspace リンクから到達。 -->
		<!-- llens(wire v2): Playground は機能ごと撤去(独自ストリーム消費者・薄い基盤に不要) -->

		<hr class=" border-gray-100 dark:border-gray-850 my-1 p-0" />

		<button
			class="flex rounded-md py-2 px-3 w-full hover:bg-gray-50 dark:hover:bg-gray-800"
			onclick={async () => {
				await userSignOut();
				user.set(undefined);

				localStorage.removeItem('token');
				location.href = '/auth';

				show = false;
			}}
		>
			<div class=" self-center mr-3">
				<span class="i-lucide-log-out size-5 block"></span>
			</div>
			<div class=" self-center truncate">{$i18n.t('Sign Out')}</div>
		</button>
	</DropdownMenu.Content>
</DropdownMenu.Root>

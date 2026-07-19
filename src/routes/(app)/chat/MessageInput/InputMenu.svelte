<script lang="ts">
	import { DropdownMenu } from 'bits-ui';
	import { getContext } from 'svelte';

	import { settings, user } from '$lib/stores';
	import { updateUserSettings } from '$lib/apis/users';

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DropdownContent from '$lib/components/common/DropdownContent.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n = getContext('i18n');

	let {
		uploadFilesHandler,
		// llens(#69): 保存済みファイル(/files に保存済みの file)の再添付 picker を開く
		openLibraryHandler = () => {},
		onClose,
		children
	}: {
		uploadFilesHandler: Function;
		openLibraryHandler?: Function;
		onClose: Function;
		children?: any;
	} = $props();

	let show = $state(false);

	const fileUploadEnabled = $derived(
		$user?.role === 'admin' || $user?.permissions?.chat?.file_upload
	);

	// 送信キー(Enter ⇄ Ctrl+Enter)のその場切り替え。設定 > チャットと同じ値を書く。
	// 現在の割り当ては送信ボタンのホバー(MessageInput)にも出る
	const ctrlEnterToSend = $derived($settings?.ctrlEnterToSend ?? false);

	const toggleCtrlEnterToSend = async () => {
		settings.set({ ...$settings, ctrlEnterToSend: !ctrlEnterToSend });
		await updateUserSettings({ ui: $settings });
	};
</script>

<Dropdown
	bind:show
	onChange={(state) => {
		if (state === false) {
			onClose();
		}
	}}
>
	<Tooltip content={$i18n.t('More')}>
		{@render children?.()}
	</Tooltip>

	{#snippet content()}
		<DropdownContent
			className="w-max min-w-[180px] max-w-[calc(100vw-2rem)] rounded-xl px-1 py-1 border border-line z-50 bg-surface-raised text-content shadow-sm"
			sideOffset={10}
			alignOffset={-8}
			side="top"
			align="start"
		>
			<Tooltip
				content={!fileUploadEnabled ? $i18n.t('You do not have permission to upload files') : ''}
				className="w-full"
			>
				<DropdownMenu.Item
					class="flex gap-2 items-center px-3 py-2 text-sm font-medium cursor-pointer hover:bg-surface-hover rounded-xl {!fileUploadEnabled
						? 'opacity-50'
						: ''}"
					onSelect={() => {
						if (fileUploadEnabled) {
							uploadFilesHandler();
						}
					}}
				>
					<span class="i-lucide-file-up size-4 block"></span>
					<div class="line-clamp-1">{$i18n.t('Upload Files')}</div>
				</DropdownMenu.Item>
			</Tooltip>

			<Tooltip
				content={!fileUploadEnabled ? $i18n.t('You do not have permission to upload files') : ''}
				className="w-full"
			>
				<DropdownMenu.Item
					class="flex gap-2 items-center px-3 py-2 text-sm font-medium cursor-pointer hover:bg-surface-hover rounded-xl {!fileUploadEnabled
						? 'opacity-50'
						: ''}"
					onSelect={() => {
						if (fileUploadEnabled) {
							openLibraryHandler();
						}
					}}
				>
					<span class="i-lucide-folder-open size-4 block"></span>
					<div class="line-clamp-1">{$i18n.t('Saved Files')}</div>
				</DropdownMenu.Item>
			</Tooltip>

			<DropdownMenu.Item
				class="flex gap-2 items-center px-3 py-2 text-sm font-medium cursor-pointer hover:bg-surface-hover rounded-xl"
				onSelect={() => {
					toggleCtrlEnterToSend();
				}}
			>
				<span class="i-lucide-keyboard size-4 block"></span>
				<div class="line-clamp-1 flex-1">{$i18n.t('Enter Key Behavior')}</div>
				<!-- バッジは「Enter を押したときの動作」を示す -->
				<div
					class="shrink-0 flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-surface-muted text-xs text-content-muted"
				>
					{#if ctrlEnterToSend}
						<span class="i-lucide-corner-down-left size-3 block"></span>
						{$i18n.t('New line')}
					{:else}
						<span class="i-lucide-arrow-up size-3 block"></span>
						{$i18n.t('Send')}
					{/if}
				</div>
			</DropdownMenu.Item>
		</DropdownContent>
	{/snippet}
</Dropdown>

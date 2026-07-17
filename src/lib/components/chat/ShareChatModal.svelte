<script lang="ts">
	import { getContext } from 'svelte';

	import { toast } from 'svelte-sonner';
	import { deleteSharedChatById, getChatById, shareChatById } from '$lib/apis/chats';
	import { copyToClipboard } from '$lib/utils';

	import Modal from '../common/Modal.svelte';

	const i18n = getContext('i18n');

	let {
		chatId,
		show = $bindable(false)
	}: {
		chatId: string;
		show?: boolean;
	} = $props();

	let chat = $state<any>(null);
	let shareUrl: string | null = null;

	const shareLocalChat = async () => {
		const sharedChat = await shareChatById(chatId);
		shareUrl = `${window.location.origin}/s/${sharedChat.id}`;
		chat = await getChatById(chatId);

		return shareUrl;
	};

	const isDifferentChat = (_chat: any) => {
		if (!chat) {
			return true;
		}
		if (!_chat) {
			return false;
		}
		return chat.id !== _chat.id || chat.share_id !== _chat.share_id;
	};

	$effect(() => {
		if (show) {
			(async () => {
				if (chatId) {
					const _chat = await getChatById(chatId);
					if (isDifferentChat(_chat)) {
						chat = _chat;
					}
				} else {
					chat = null;
				}
			})();
		}
	});
</script>

<Modal bind:show size="md">
	<div>
		<div class=" flex justify-between text-content px-5 pt-4 pb-0.5">
			<div class=" text-lg font-medium self-center">{$i18n.t('Share Chat')}</div>
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

		{#if chat}
			<div class="px-5 pt-4 pb-5 w-full flex flex-col justify-center">
				<div class=" text-sm text-content mb-1">
					{#if chat.share_id}
						<a href="/s/{chat.share_id}" target="_blank"
							>{$i18n.t('You have shared this chat')}
							<span class=" underline">{$i18n.t('before')}</span>.</a
						>
						{$i18n.t('Click here to')}
						<button
							class="underline"
							onclick={async () => {
								const res = await deleteSharedChatById(chatId);

								if (res) {
									chat = await getChatById(chatId);
								}
							}}
							>{$i18n.t('delete this link')}
						</button>
						{$i18n.t('and create a new shared link.')}
					{:else}
						{$i18n.t(
							"Messages you send after creating your link won't be shared. Users with the URL will be able to view the shared chat."
						)}
					{/if}
				</div>

				<div class="flex justify-end">
					<div class="flex flex-col items-end space-x-1 mt-3">
						<div class="flex gap-1">
							<button
								class="self-center flex items-center gap-1 px-3.5 py-2 text-sm font-medium bg-primary hover:bg-primary-hover text-primary-content rounded-full"
								type="button"
								id="copy-and-share-chat-button"
								onclick={async () => {
									const isSafari = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);

									if (isSafari) {
										// Safari はユーザー操作と同期した ClipboardItem でしか書き込めない
										const getUrlPromise = async () => {
											const url = await shareLocalChat();
											return new Blob([url], { type: 'text/plain' });
										};

										navigator.clipboard
											.write([
												new ClipboardItem({
													'text/plain': getUrlPromise()
												})
											])
											.catch((error) => {
												console.error('Could not copy text: ', error);
											});
									} else {
										copyToClipboard(await shareLocalChat());
									}

									toast.success($i18n.t('Copied shared chat URL to clipboard!'));
									show = false;
								}}
							>
								<span class="i-lucide-link size-4 block"></span>

								{#if chat.share_id}
									{$i18n.t('Update and Copy Link')}
								{:else}
									{$i18n.t('Copy Link')}
								{/if}
							</button>
						</div>
					</div>
				</div>
			</div>
		{/if}
	</div>
</Modal>

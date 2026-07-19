<script lang="ts">
	import { getContext, untrack } from 'svelte';

	import { toast } from 'svelte-sonner';
	import { deleteSharedChatById, getChatById, shareChatById } from '$lib/apis/chats';
	import { copyToClipboard } from '$lib/utils';

	import Modal from '../common/Modal.svelte';

	const i18n = getContext('i18n');

	// llens: 旧 upstream の「英語語順で断片キーを連結する」文面は日本語に翻訳不能だったため
	// 文単位キーで刷新(2026-07-19)。共有リンクは URL 行として明示し、
	// 作成 / 更新+コピー / 解除 をボタンに分離した。
	let {
		chatId,
		show = $bindable(false),
		onClose = undefined
	}: {
		chatId: string;
		show?: boolean;
		/** 閉じ遷移で発火(#19: admin 一覧が共有バッジを追従させるのに使う) */
		onClose?: () => void;
	} = $props();

	// 閉じ遷移でのみ onClose を発火(Switch の lastState ガード + 罠(7) の untrack)
	let lastShow = show;
	$effect(() => {
		if (show !== lastShow) {
			lastShow = show;
			if (!show) {
				untrack(() => onClose?.());
			}
		}
	});

	let chat = $state<any>(null);

	const shareUrl = $derived(chat?.share_id ? `${window.location.origin}/s/${chat.share_id}` : null);

	$effect(() => {
		if (show) {
			(async () => {
				chat = chatId ? await getChatById(chatId).catch(() => null) : null;
			})();
		}
	});

	// Safari はユーザー操作と同期した ClipboardItem でしか書き込めないため、
	// URL を返す Promise を渡す形でコピーする(それ以外は copyToClipboard)
	const copyWith = async (getUrl: () => Promise<string>) => {
		const isSafari = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
		if (isSafari) {
			navigator.clipboard
				.write([
					new ClipboardItem({
						'text/plain': getUrl().then((url) => new Blob([url], { type: 'text/plain' }))
					})
				])
				.catch((error) => {
					console.error('Could not copy text: ', error);
				});
		} else {
			copyToClipboard(await getUrl());
		}
		toast.success($i18n.t('Copied shared chat URL to clipboard!'));
	};

	// 共有(未共有なら作成 / 共有済みならスナップショット更新)して URL を返す
	const shareAndGetUrl = async () => {
		const sharedChat = await shareChatById(chatId);
		chat = await getChatById(chatId);
		return `${window.location.origin}/s/${sharedChat.id}`;
	};

	const stopSharing = async () => {
		const res = await deleteSharedChatById(chatId);
		if (res) {
			chat = await getChatById(chatId);
			toast.success($i18n.t('Sharing stopped'));
		}
	};
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
			<div class="px-5 pt-3 pb-5 w-full flex flex-col gap-3">
				{#if shareUrl}
					<!-- 共有中: URL 行(readonly + コピー)+ 更新/解除 -->
					<div class="flex items-center gap-1.5">
						<input
							class="flex-1 min-w-0 px-2 py-1.5 text-sm bg-surface-form rounded-xs outline-hidden text-content-muted"
							value={shareUrl}
							readonly
							onfocus={(e) => e.currentTarget.select()}
						/>
						<button
							class="shrink-0 p-2 rounded-lg hover:bg-surface-hover text-content-muted hover:text-content"
							type="button"
							aria-label={$i18n.t('Copy to clipboard')}
							onclick={() => copyWith(async () => shareUrl)}
						>
							<span class="i-lucide-clipboard size-4 block"></span>
						</button>
					</div>

					<div class="text-sm text-content-muted">
						{$i18n.t(
							'The link shows a snapshot of this chat from the last time it was shared or updated.'
						)}
					</div>

					<div class="flex justify-between items-center mt-1">
						<button
							class="px-3.5 py-2 text-sm font-medium rounded-full bg-secondary hover:bg-secondary-hover text-secondary-content"
							type="button"
							onclick={stopSharing}
						>
							{$i18n.t('Stop sharing')}
						</button>
						<button
							class="flex items-center gap-1.5 px-3.5 py-2 text-sm font-medium bg-primary hover:bg-primary-hover text-primary-content rounded-full"
							type="button"
							id="copy-and-share-chat-button"
							onclick={() => copyWith(shareAndGetUrl)}
						>
							<span class="i-lucide-refresh-cw size-4 block"></span>
							{$i18n.t('Update and Copy Link')}
						</button>
					</div>
				{:else}
					<!-- 未共有: 説明 + 作成 -->
					<div class="text-sm text-content-muted">
						{$i18n.t(
							'Signed-in users with the link can view a snapshot of this chat. Messages you send after creating the link stay private until you update it.'
						)}
					</div>

					<div class="flex justify-end mt-1">
						<button
							class="flex items-center gap-1.5 px-3.5 py-2 text-sm font-medium bg-primary hover:bg-primary-hover text-primary-content rounded-full"
							type="button"
							id="copy-and-share-chat-button"
							onclick={() => copyWith(shareAndGetUrl)}
						>
							<span class="i-lucide-link size-4 block"></span>
							{$i18n.t('Create and Copy Link')}
						</button>
					</div>
				{/if}
			</div>
		{/if}
	</div>
</Modal>

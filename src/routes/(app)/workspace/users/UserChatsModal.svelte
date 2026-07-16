<script lang="ts">
	// llens(#55, #69): admin のユーザーチャット一覧。見た目は ChatsListModal(files の
	// 関連チャットと共通)、このラッパは取得と削除(confirm 付き)だけを持つ。
	import { toast } from 'svelte-sonner';
	import { getContext } from 'svelte';

	import { getChatListByUserId, deleteChatById } from '$lib/apis/chats';

	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import ChatsListModal from '$lib/components/workspace/common/ChatsListModal.svelte';

	const i18n = getContext('i18n');

	let { show = $bindable(false), user }: { show?: boolean; user: any } = $props();

	let chats = $state<any[] | null>(null);
	let showDeleteConfirmDialog = $state(false);
	let chatToDelete = $state<string | null>(null);

	const deleteChatHandler = async (chatId: string) => {
		await deleteChatById(chatId).catch((error) => {
			toast.error(`${error}`);
		});

		chats = await getChatListByUserId(user.id);
	};

	$effect(() => {
		if (show) {
			if (user.id) {
				getChatListByUserId(user.id).then((res) => {
					chats = res;
				});
			}
		} else {
			chats = null;
		}
	});
</script>

<ConfirmDialog
	bind:show={showDeleteConfirmDialog}
	onConfirm={() => {
		if (chatToDelete) {
			deleteChatHandler(chatToDelete);
			chatToDelete = null;
		}
	}}
/>

<ChatsListModal
	bind:show
	title={$i18n.t("{{user}}'s Chats", { user: user?.name ?? '' })}
	{chats}
	showOwner={false}
	onDelete={(chat) => {
		chatToDelete = chat.id;
		showDeleteConfirmDialog = true;
	}}
/>

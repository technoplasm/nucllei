<script lang="ts">
	import { settings } from '$lib/stores';
	import type { ChatHistory, ChatMessage } from '$lib/types/chat';

	import ResponseMessage from './ResponseMessage.svelte';
	import UserMessage from './UserMessage.svelte';
	import SystemNoticeMessage from './SystemNoticeMessage.svelte';
	import { isSystemNoticeMessage } from '$lib/utils/system-notice';

	let {
		history = $bindable(),
		messageId,
		user,
		gotoMessage,
		showPreviousMessage,
		showNextMessage,
		editMessage,
		actionMessage,
		regenerateResponse,
		continueResponse,
		addMessages,
		readOnly = false
	}: {
		history: ChatHistory;
		messageId: string;
		user: { name: string; profile_image_url?: string } | undefined;
		gotoMessage: (message: ChatMessage, index: number) => void;
		showPreviousMessage: (message: ChatMessage) => void;
		showNextMessage: (message: ChatMessage) => void;
		editMessage: (id: string, content: string) => void;
		actionMessage: (
			actionId: string,
			message: ChatMessage,
			event?: unknown
		) => void | Promise<void>;
		regenerateResponse: (message: ChatMessage) => void | Promise<void>;
		continueResponse: () => void | Promise<void>;
		addMessages: (data: {
			modelId: string;
			parentId: string;
			messages: { role: string; content: string }[];
		}) => void | Promise<void>;
		readOnly?: boolean;
	} = $props();

	const message = $derived(history.messages[messageId] as ChatMessage | undefined);
	const parentMessage = $derived(
		message && message.parentId !== null ? history.messages[message.parentId] : undefined
	);
</script>

<div
	class="flex flex-col justify-between px-3 w-full {($settings?.widescreenMode ?? null)
		? 'max-w-full'
		: 'max-w-5xl'} mx-auto rounded-lg group"
>
	{#if message}
		{#if isSystemNoticeMessage(message)}
			<!-- llens(#110): バックグラウンドタスクの配達通知(BE 挿入)は wire 上
			     user ロールだが、ユーザー発言には見せない(システム挿入として描く)。
			     判定は meta.system_notice のみ — 文面プレフィクス判定はしない -->
			<SystemNoticeMessage {history} {messageId} />
		{:else if message.role === 'user'}
			<UserMessage
				{user}
				{history}
				{messageId}
				siblings={message.parentId !== null
					? (parentMessage?.childrenIds ?? [])
					: Object.values(history.messages)
							.filter((m) => m.parentId === null)
							.map((m) => m.id)}
				{gotoMessage}
				{showPreviousMessage}
				{showNextMessage}
				{editMessage}
				{readOnly}
			/>
		{:else}
			<!-- llens(#108): マルチモデル送信は撤去 — 応答は常に単一 lane。
			     旧データ(models.length > 1 のチャット)の応答も sibling ナビ(1/N)で
			     そのまま閲覧できる(side-by-side renderer は削除済み) -->
			<ResponseMessage
				{history}
				{messageId}
				isLastMessage={messageId === history.currentId}
				siblings={parentMessage?.childrenIds ?? []}
				{gotoMessage}
				{showPreviousMessage}
				{showNextMessage}
				{editMessage}
				{actionMessage}
				{continueResponse}
				{regenerateResponse}
				{addMessages}
				{readOnly}
			/>
		{/if}
	{/if}
</div>

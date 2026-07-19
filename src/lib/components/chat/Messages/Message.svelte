<script lang="ts">
	import { settings } from '$lib/stores';
	import type { ChatHistory, ChatMessage } from '$lib/types/chat';

	import MultiResponseMessages from './MultiResponseMessages.svelte';
	import ResponseMessage from './ResponseMessage.svelte';
	import UserMessage from './UserMessage.svelte';

	let {
		idx = 0,
		history = $bindable(),
		messageId,
		user,
		gotoMessage,
		showPreviousMessage,
		showNextMessage,
		updateChat,
		editMessage,
		deleteMessage,
		actionMessage,
		regenerateResponse,
		continueResponse,
		addMessages,
		triggerScroll,
		readOnly = false
	}: {
		idx?: number;
		history: ChatHistory;
		messageId: string;
		user: { name: string; profile_image_url?: string } | undefined;
		gotoMessage: (message: ChatMessage, index: number) => void;
		showPreviousMessage: (message: ChatMessage) => void;
		showNextMessage: (message: ChatMessage) => void;
		updateChat: () => void | Promise<void>;
		editMessage: (id: string, content: string, submit?: boolean) => void;
		deleteMessage: (id: string) => void;
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
		triggerScroll: () => void;
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
		{#if message.role === 'user'}
			<UserMessage
				{user}
				{history}
				{messageId}
				isFirstMessage={idx === 0}
				siblings={message.parentId !== null
					? (parentMessage?.childrenIds ?? [])
					: Object.values(history.messages)
							.filter((m) => m.parentId === null)
							.map((m) => m.id)}
				{gotoMessage}
				{showPreviousMessage}
				{showNextMessage}
				{editMessage}
				{deleteMessage}
				{readOnly}
			/>
		{:else if (parentMessage?.models?.length ?? 1) === 1}
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
				{deleteMessage}
				{continueResponse}
				{regenerateResponse}
				{addMessages}
				{readOnly}
			/>
		{:else}
			<MultiResponseMessages
				bind:history
				{messageId}
				{updateChat}
				{editMessage}
				{actionMessage}
				{deleteMessage}
				{continueResponse}
				{regenerateResponse}
				{triggerScroll}
				{addMessages}
				{readOnly}
			/>
		{/if}
	{/if}
</div>

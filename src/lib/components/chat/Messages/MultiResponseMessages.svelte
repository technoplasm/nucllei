<script lang="ts">
	import { onMount, tick } from 'svelte';

	import { mobile } from '$lib/stores';
	import type { ChatHistory, ChatMessage } from '$lib/types/chat';

	import ResponseMessage from './ResponseMessage.svelte';

	let {
		history = $bindable(),
		messageId,
		readOnly = false,
		updateChat,
		editMessage,
		actionMessage,
		deleteMessage,
		continueResponse,
		regenerateResponse,
		addMessages,
		triggerScroll
	}: {
		history: ChatHistory;
		messageId: string;
		readOnly?: boolean;
		updateChat: () => void | Promise<void>;
		editMessage: (id: string, content: string, submit?: boolean) => void;
		actionMessage: (
			actionId: string,
			message: ChatMessage,
			event?: unknown
		) => void | Promise<void>;
		deleteMessage: (id: string) => void;
		continueResponse: () => void | Promise<void>;
		regenerateResponse: (message: ChatMessage) => void | Promise<void>;
		addMessages: (data: {
			modelId: string;
			parentId: string;
			messages: { role: string; content: string }[];
		}) => void | Promise<void>;
		triggerScroll: () => void;
	} = $props();

	let parentMessage: ChatMessage | null = $state(null);
	let groupedMessageIds: Record<string, { messageIds: string[] }> = $state({});
	let groupedMessageIdsIdx: Record<string, number> = $state({});

	const message = $derived(history.messages[messageId] as ChatMessage | undefined);

	// 指定 messageId の枝の最深子まで降りる
	const deepestChildId = (id: string): string => {
		let currentId = id;
		let childrenIds = history.messages[currentId].childrenIds;
		while (childrenIds.length !== 0) {
			currentId = childrenIds.at(-1) as string;
			childrenIds = history.messages[currentId].childrenIds;
		}
		return currentId;
	};

	const gotoMessage = async (modelIdx: string, messageIdx: number) => {
		// Clamp messageIdx to ensure it's within valid range
		groupedMessageIdsIdx[modelIdx] = Math.max(
			0,
			Math.min(messageIdx, groupedMessageIds[modelIdx].messageIds.length - 1)
		);

		// Traverse the branch to find the deepest child message
		history.currentId = deepestChildId(
			groupedMessageIds[modelIdx].messageIds[groupedMessageIdsIdx[modelIdx]]
		);

		// Await UI updates
		await tick();
		await updateChat();

		// Trigger scrolling after navigation
		triggerScroll();
	};

	const selectResponse = async (_messageId: string) => {
		if (messageId != _messageId) {
			history.currentId = deepestChildId(_messageId);

			await tick();
			await updateChat();
			triggerScroll();
		}
	};

	const showPreviousMessage = async (modelIdx: string) => {
		groupedMessageIdsIdx[modelIdx] = Math.max(0, groupedMessageIdsIdx[modelIdx] - 1);

		history.currentId = deepestChildId(
			groupedMessageIds[modelIdx].messageIds[groupedMessageIdsIdx[modelIdx]]
		);

		await tick();
		await updateChat();
		triggerScroll();
	};

	const showNextMessage = async (modelIdx: string) => {
		groupedMessageIdsIdx[modelIdx] = Math.min(
			groupedMessageIds[modelIdx].messageIds.length - 1,
			groupedMessageIdsIdx[modelIdx] + 1
		);

		history.currentId = deepestChildId(
			groupedMessageIds[modelIdx].messageIds[groupedMessageIdsIdx[modelIdx]]
		);

		await tick();
		await updateChat();
		triggerScroll();
	};

	const initHandler = async () => {
		await tick();

		const parentId = history.messages[messageId].parentId;
		parentMessage = parentId ? history.messages[parentId] : null;

		const models = parentMessage?.models ?? [];
		const childMessages = (parentMessage?.childrenIds ?? []).map((id) => history.messages[id]);

		groupedMessageIds = models.reduce<Record<string, { messageIds: string[] }>>(
			(a, _model, modelIdx) => {
				// Find all messages that are children of the parent message and have the same model
				const modelMessageIds = childMessages
					.filter((m) => m?.modelIdx === modelIdx)
					.map((m) => m.id);

				return {
					...a,
					[modelIdx]: { messageIds: modelMessageIds }
				};
			},
			{}
		);

		groupedMessageIdsIdx = models.reduce<Record<string, number>>((a, _model, modelIdx) => {
			const idx = groupedMessageIds[modelIdx].messageIds.findIndex((id) => id === messageId);
			return {
				...a,
				[modelIdx]: idx !== -1 ? idx : groupedMessageIds[modelIdx].messageIds.length - 1
			};
		}, {});

		await tick();
	};

	onMount(async () => {
		await initHandler();
		await tick();

		const messageElement = document.getElementById(`message-${messageId}`);
		if (messageElement) {
			messageElement.scrollIntoView({ block: 'start' });
		}
	});
</script>

{#if parentMessage}
	<div>
		<div class="flex snap-x snap-mandatory overflow-x-auto scrollbar-hidden">
			{#each Object.keys(groupedMessageIds) as modelIdx}
				{#if groupedMessageIdsIdx[modelIdx] !== undefined && groupedMessageIds[modelIdx].messageIds.length > 0}
					{@const _messageId =
						groupedMessageIds[modelIdx].messageIds[groupedMessageIdsIdx[modelIdx]]}

					<div
						class=" snap-center w-full max-w-full m-1 border {message?.modelIdx === Number(modelIdx)
							? `border-gray-100 dark:border-gray-850 border-[1.5px] ${
									$mobile ? 'min-w-full' : 'min-w-80'
								}`
							: `border-gray-100 dark:border-gray-850 border-dashed ${
									$mobile ? 'min-w-full' : 'min-w-80'
								}`} transition-all p-5 rounded-2xl"
						role="button"
						tabindex="0"
						onclick={() => {
							selectResponse(_messageId);
						}}
						onkeydown={(e) => {
							if (e.key === 'Enter' || e.key === ' ') {
								e.preventDefault();
								selectResponse(_messageId);
							}
						}}
					>
						{#key history.currentId}
							{#if message}
								<ResponseMessage
									{history}
									messageId={_messageId}
									isLastMessage={true}
									siblings={groupedMessageIds[modelIdx].messageIds}
									gotoMessage={(_message: ChatMessage, messageIdx: number) =>
										gotoMessage(modelIdx, messageIdx)}
									showPreviousMessage={() => showPreviousMessage(modelIdx)}
									showNextMessage={() => showNextMessage(modelIdx)}
									{editMessage}
									{deleteMessage}
									{actionMessage}
									{continueResponse}
									regenerateResponse={async (message: ChatMessage) => {
										regenerateResponse(message);
										await tick();
										groupedMessageIdsIdx[modelIdx] =
											groupedMessageIds[modelIdx].messageIds.length - 1;
									}}
									{addMessages}
									{readOnly}
								/>
							{/if}
						{/key}
					</div>
				{/if}
			{/each}
		</div>
	</div>
{/if}

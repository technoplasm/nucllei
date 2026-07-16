<script lang="ts">
	import { onMount, tick } from 'svelte';

	import { mobile } from '$lib/stores';

	import ResponseMessage from './ResponseMessage.svelte';

	export let chatId;
	export let history;
	export let messageId;

	export let readOnly = false;

	export let updateChat: Function;
	export let editMessage: Function;
	export let saveMessage: Function;
	export let actionMessage: Function;

	export let submitMessage: Function;
	export let deleteMessage: Function;

	export let continueResponse: Function;
	export let regenerateResponse: Function;

	export let addMessages: Function;

	export let triggerScroll: Function;

	let currentMessageId;
	let parentMessage;
	let groupedMessageIds = {};
	let groupedMessageIdsIdx = {};

	let message = JSON.parse(JSON.stringify(history.messages[messageId]));
	$: if (history.messages) {
		if (JSON.stringify(message) !== JSON.stringify(history.messages[messageId])) {
			message = JSON.parse(JSON.stringify(history.messages[messageId]));
		}
	}

	const gotoMessage = async (modelIdx, messageIdx) => {
		// Clamp messageIdx to ensure it's within valid range
		groupedMessageIdsIdx[modelIdx] = Math.max(
			0,
			Math.min(messageIdx, groupedMessageIds[modelIdx].messageIds.length - 1)
		);

		// Get the messageId at the specified index
		let messageId = groupedMessageIds[modelIdx].messageIds[groupedMessageIdsIdx[modelIdx]];
		console.log(messageId);

		// Traverse the branch to find the deepest child message
		let messageChildrenIds = history.messages[messageId].childrenIds;
		while (messageChildrenIds.length !== 0) {
			messageId = messageChildrenIds.at(-1);
			messageChildrenIds = history.messages[messageId].childrenIds;
		}

		// Update the current message ID in history
		history.currentId = messageId;

		// Await UI updates
		await tick();
		await updateChat();

		// Trigger scrolling after navigation
		triggerScroll();
	};

	const selectResponse = async (_messageId: string) => {
		if (messageId != _messageId) {
			let currentMessageId = _messageId;
			let messageChildrenIds = history.messages[currentMessageId].childrenIds;
			while (messageChildrenIds.length !== 0) {
				currentMessageId = messageChildrenIds.at(-1);
				messageChildrenIds = history.messages[currentMessageId].childrenIds;
			}
			history.currentId = currentMessageId;

			await tick();
			await updateChat();
			triggerScroll();
		}
	};

	const showPreviousMessage = async (modelIdx) => {
		groupedMessageIdsIdx[modelIdx] = Math.max(0, groupedMessageIdsIdx[modelIdx] - 1);

		let messageId = groupedMessageIds[modelIdx].messageIds[groupedMessageIdsIdx[modelIdx]];
		console.log(messageId);

		let messageChildrenIds = history.messages[messageId].childrenIds;

		while (messageChildrenIds.length !== 0) {
			messageId = messageChildrenIds.at(-1);
			messageChildrenIds = history.messages[messageId].childrenIds;
		}

		history.currentId = messageId;

		await tick();
		await updateChat();
		triggerScroll();
	};

	const showNextMessage = async (modelIdx) => {
		groupedMessageIdsIdx[modelIdx] = Math.min(
			groupedMessageIds[modelIdx].messageIds.length - 1,
			groupedMessageIdsIdx[modelIdx] + 1
		);

		let messageId = groupedMessageIds[modelIdx].messageIds[groupedMessageIdsIdx[modelIdx]];
		console.log(messageId);

		let messageChildrenIds = history.messages[messageId].childrenIds;

		while (messageChildrenIds.length !== 0) {
			messageId = messageChildrenIds.at(-1);
			messageChildrenIds = history.messages[messageId].childrenIds;
		}

		history.currentId = messageId;

		await tick();
		await updateChat();
		triggerScroll();
	};

	const initHandler = async () => {
		console.log('multiresponse:initHandler');
		await tick();

		currentMessageId = messageId;
		parentMessage = history.messages[messageId].parentId
			? history.messages[history.messages[messageId].parentId]
			: null;

		groupedMessageIds = parentMessage?.models.reduce((a, model, modelIdx) => {
			// Find all messages that are children of the parent message and have the same model
			let modelMessageIds = parentMessage?.childrenIds
				.map((id) => history.messages[id])
				.filter((m) => m?.modelIdx === modelIdx)
				.map((m) => m.id);

			// Legacy support for messages that don't have a modelIdx
			// Find all messages that are children of the parent message and have the same model
			if (modelMessageIds.length === 0) {
				let modelMessages = parentMessage?.childrenIds
					.map((id) => history.messages[id])
					.filter((m) => m?.model === model);

				modelMessages.forEach((m) => {
					m.modelIdx = modelIdx;
				});

				modelMessageIds = modelMessages.map((m) => m.id);
			}

			return {
				...a,
				[modelIdx]: { messageIds: modelMessageIds }
			};
		}, {});

		groupedMessageIdsIdx = parentMessage?.models.reduce((a, model, modelIdx) => {
			const idx = groupedMessageIds[modelIdx].messageIds.findIndex((id) => id === messageId);
			if (idx !== -1) {
				return {
					...a,
					[modelIdx]: idx
				};
			} else {
				return {
					...a,
					[modelIdx]: groupedMessageIds[modelIdx].messageIds.length - 1
				};
			}
		}, {});

		console.log(groupedMessageIds, groupedMessageIdsIdx);

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
		<div
			class="flex snap-x snap-mandatory overflow-x-auto scrollbar-hidden"
			id="responses-container-{chatId}-{parentMessage.id}"
		>
			{#each Object.keys(groupedMessageIds) as modelIdx}
				{#if groupedMessageIdsIdx[modelIdx] !== undefined && groupedMessageIds[modelIdx].messageIds.length > 0}
					{@const _messageId =
						groupedMessageIds[modelIdx].messageIds[groupedMessageIdsIdx[modelIdx]]}

					<div
						class=" snap-center w-full max-w-full m-1 border {history.messages[messageId]
							?.modelIdx == modelIdx
							? `border-gray-100 dark:border-gray-850 border-[1.5px] ${
									$mobile ? 'min-w-full' : 'min-w-80'
								}`
							: `border-gray-100 dark:border-gray-850 border-dashed ${
									$mobile ? 'min-w-full' : 'min-w-80'
								}`} transition-all p-5 rounded-2xl"
						role="button"
						tabindex="0"
						on:click={() => {
							selectResponse(_messageId);
						}}
						on:keydown={(e) => {
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
									gotoMessage={(message, messageIdx) => gotoMessage(modelIdx, messageIdx)}
									showPreviousMessage={() => showPreviousMessage(modelIdx)}
									showNextMessage={() => showNextMessage(modelIdx)}
									{updateChat}
									{editMessage}
									{saveMessage}
									{deleteMessage}
									{actionMessage}
									{submitMessage}
									{continueResponse}
									regenerateResponse={async (message) => {
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

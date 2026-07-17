<script lang="ts">
	// ResponseMessage のアクションボタン列 + 兄弟ナビゲーション。
	// 読み上げ(TTS)状態は tts.svelte.ts の MessageSpeech に委譲する。
	import { getContext, onMount, tick } from 'svelte';
	import { toast } from 'svelte-sonner';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import { config, settings, user } from '$lib/stores';
	import type { Model } from '$lib/stores';
	import { copyToClipboard as _copyToClipboard, sanitizeResponseContent } from '$lib/utils';
	import type { ChatMessage } from '$lib/types/chat';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import DeleteConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import { MessageSpeech } from './tts.svelte';

	const i18n = getContext<Writable<i18nType>>('i18n');

	type ModelAction = { id: string; name: string; icon_url?: string };

	let {
		message,
		model = undefined,
		siblings,
		isLastMessage,
		readOnly,
		onEdit,
		gotoMessage,
		showPreviousMessage,
		showNextMessage,
		continueResponse,
		regenerateResponse,
		actionMessage,
		deleteMessage
	}: {
		message: ChatMessage;
		// model.actions は backend(nucllei/utils/models.py)が action function から同梱する
		model?: (Model & { actions?: ModelAction[] }) | undefined;
		siblings: string[];
		isLastMessage: boolean;
		readOnly: boolean;
		onEdit: () => void;
		gotoMessage: (message: ChatMessage, index: number) => void;
		showPreviousMessage: (message: ChatMessage) => void;
		showNextMessage: (message: ChatMessage) => void;
		continueResponse: () => void;
		regenerateResponse: (message: ChatMessage) => void;
		actionMessage: (actionId: string, message: ChatMessage) => void;
		deleteMessage: (id: string) => void;
	} = $props();

	let buttonsContainerElement: HTMLDivElement | null = $state(null);
	let messageIndexEdit = $state(false);
	let showDeleteConfirm = $state(false);

	const speech = new MessageSpeech((key) => $i18n.t(key));

	const usageTooltip = $derived(
		message.usage
			? `<pre>${sanitizeResponseContent(
					JSON.stringify(message.usage, null, 2)
						.replace(/"([^(")"]+)":/g, '$1:')
						.slice(1, -1)
						.split('\n')
						.map((line) => line.slice(2))
						.map((line) => (line.endsWith(',') ? line.slice(0, -1) : line))
						.join('\n')
				)}</pre>`
			: ''
	);

	const copyToClipboard = async (text: string) => {
		const res = await _copyToClipboard(text, $settings?.copyFormatted ?? false);
		if (res) {
			toast.success($i18n.t('Copying to clipboard was successful!'));
		}
	};

	onMount(() => {
		// 縦ホイールをボタン列の横スクロールに変換する(preventDefault が要るので
		// non-passive な素の addEventListener で登録する)
		buttonsContainerElement?.addEventListener('wheel', (event) => {
			event.preventDefault();
			if (event.deltaY !== 0 && buttonsContainerElement) {
				buttonsContainerElement.scrollLeft += event.deltaY;
			}
		});
	});
</script>

<DeleteConfirmDialog
	bind:show={showDeleteConfirm}
	title={$i18n.t('Delete message?')}
	onConfirm={() => {
		deleteMessage(message.id);
	}}
/>

<div
	bind:this={buttonsContainerElement}
	class="flex justify-start overflow-x-auto buttons text-content-muted mt-0.5"
>
	{#if message.done || siblings.length > 1}
		{#if siblings.length > 1}
			<div class="flex self-center min-w-fit" dir="ltr">
				<button
					class="self-center p-1 hover:bg-black/5 dark:hover:bg-white/5 dark:hover:text-white hover:text-black rounded-md"
					aria-label={$i18n.t('Previous message')}
					onclick={() => {
						showPreviousMessage(message);
					}}
				>
					<span class="i-lucide-chevron-left size-3.5 block"></span>
				</button>

				{#if messageIndexEdit}
					<div class="text-sm flex justify-center font-semibold self-center text-content min-w-fit">
						<input
							id="message-index-input-{message.id}"
							type="number"
							value={siblings.indexOf(message.id) + 1}
							min="1"
							max={siblings.length}
							onfocus={(e) => {
								e.currentTarget.select();
							}}
							onblur={(e) => {
								gotoMessage(message, Number(e.currentTarget.value) - 1);
								messageIndexEdit = false;
							}}
							onkeydown={(e) => {
								if (e.key === 'Enter') {
									gotoMessage(message, Number(e.currentTarget.value) - 1);
									messageIndexEdit = false;
								}
							}}
							class="bg-transparent font-semibold self-center text-content min-w-fit outline-hidden"
						/>/{siblings.length}
					</div>
				{:else}
					<!-- svelte-ignore a11y_no_static_element_interactions -->
					<div
						class="text-sm tracking-widest font-semibold self-center text-content min-w-fit"
						ondblclick={async () => {
							messageIndexEdit = true;

							await tick();
							const input = document.getElementById(
								`message-index-input-${message.id}`
							) as HTMLInputElement | null;
							if (input) {
								input.focus();
								input.select();
							}
						}}
					>
						{siblings.indexOf(message.id) + 1}/{siblings.length}
					</div>
				{/if}

				<button
					class="self-center p-1 hover:bg-black/5 dark:hover:bg-white/5 dark:hover:text-white hover:text-black rounded-md"
					aria-label={$i18n.t('Next message')}
					onclick={() => {
						showNextMessage(message);
					}}
				>
					<span class="i-lucide-chevron-right size-3.5 block"></span>
				</button>
			</div>
		{/if}

		{#if message.done}
			{#if !readOnly}
				{#if $user?.role === 'user' ? ($user?.permissions?.chat?.edit ?? true) : true}
					<Tooltip content={$i18n.t('Edit')} placement="bottom">
						<button
							class="{isLastMessage
								? 'visible'
								: 'invisible group-hover:visible'} p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-white hover:text-black"
							aria-label={$i18n.t('Edit')}
							onclick={() => {
								onEdit();
							}}
						>
							<span class="i-lucide-pencil size-4 block"></span>
						</button>
					</Tooltip>
				{/if}
			{/if}

			<Tooltip content={$i18n.t('Copy')} placement="bottom">
				<button
					class="{isLastMessage
						? 'visible'
						: 'invisible group-hover:visible'} p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-white hover:text-black copy-response-button"
					aria-label={$i18n.t('Copy')}
					onclick={() => {
						copyToClipboard(message.content);
					}}
				>
					<span class="i-lucide-copy size-4 block"></span>
				</button>
			</Tooltip>

			<!-- llens(#66/#53): per-user フラグは廃止(全員使用可)。
			     配備レベルの enable(audio.tts.enable)でアイコンごと制御 -->
			{#if $config?.audio?.tts?.enable ?? true}
				<Tooltip content={$i18n.t('Read Aloud')} placement="bottom">
					<button
						id="speak-button-{message.id}"
						class="{isLastMessage
							? 'visible'
							: 'invisible group-hover:visible'} p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-white hover:text-black"
						aria-label={$i18n.t('Read Aloud')}
						onclick={() => {
							if (!speech.loadingSpeech) {
								speech.toggle(message.content);
							}
						}}
					>
						{#if speech.loadingSpeech}
							<span class="i-lucide-loader-circle size-4 block animate-spin"></span>
						{:else if speech.speaking}
							<span class="i-lucide-volume-x size-4 block"></span>
						{:else}
							<span class="i-lucide-volume-2 size-4 block"></span>
						{/if}
					</button>
				</Tooltip>
			{/if}

			{#if message.usage}
				<Tooltip content={usageTooltip} placement="bottom">
					<button
						class=" {isLastMessage
							? 'visible'
							: 'invisible group-hover:visible'} p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-white hover:text-black whitespace-pre-wrap"
						aria-label={$i18n.t('Generation Info')}
						id="info-{message.id}"
					>
						<span class="i-lucide-info size-4 block"></span>
					</button>
				</Tooltip>
			{/if}

			{#if !readOnly}
				{#if isLastMessage}
					<Tooltip content={$i18n.t('Continue Response')} placement="bottom">
						<button
							type="button"
							id="continue-response-button"
							class="visible p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-white hover:text-black regenerate-response-button"
							aria-label={$i18n.t('Continue Response')}
							onclick={() => {
								continueResponse();
							}}
						>
							<span class="i-lucide-circle-play size-4 block"></span>
						</button>
					</Tooltip>
				{/if}

				<Tooltip content={$i18n.t('Regenerate')} placement="bottom">
					<button
						type="button"
						class="{isLastMessage
							? 'visible'
							: 'invisible group-hover:visible'} p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-white hover:text-black regenerate-response-button"
						aria-label={$i18n.t('Regenerate')}
						onclick={() => {
							regenerateResponse(message);
						}}
					>
						<span class="i-lucide-refresh-ccw size-4 block"></span>
					</button>
				</Tooltip>

				{#if siblings.length > 1}
					<Tooltip content={$i18n.t('Delete')} placement="bottom">
						<button
							type="button"
							id="delete-response-button"
							class="{isLastMessage
								? 'visible'
								: 'invisible group-hover:visible'} p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-white hover:text-black regenerate-response-button"
							aria-label={$i18n.t('Delete')}
							onclick={() => {
								showDeleteConfirm = true;
							}}
						>
							<span class="i-lucide-trash-2 size-4 block"></span>
						</button>
					</Tooltip>
				{/if}

				{#if isLastMessage}
					{#each model?.actions ?? [] as action (action.id)}
						<Tooltip content={action.name} placement="bottom">
							<button
								type="button"
								class="visible p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-white hover:text-black"
								aria-label={action.name}
								onclick={() => {
									actionMessage(action.id, message);
								}}
							>
								{#if action.icon_url}
									<div class="size-4">
										<img
											src={action.icon_url}
											class="w-4 h-4 {action.icon_url.includes('svg') ? 'dark:invert-[80%]' : ''}"
											style="fill: currentColor;"
											alt={action.name}
										/>
									</div>
								{:else}
									<span class="i-lucide-sparkles size-4 block"></span>
								{/if}
							</button>
						</Tooltip>
					{/each}
				{/if}
			{/if}
		{/if}
	{/if}
</div>

<style>
	.buttons::-webkit-scrollbar {
		display: none; /* for Chrome, Safari and Opera */
	}

	.buttons {
		-ms-overflow-style: none; /* IE and Edge */
		scrollbar-width: none; /* Firefox */
	}
</style>

<script lang="ts">
	// assistant 応答 1 件のオーケストレータ。
	// 本文描画は ContentBlocks / ContentRenderer、編集は ResponseMessage/MessageEditor、
	// アクションボタン列(TTS 含む)は ResponseMessage/MessageActions に委譲する。
	import dayjs from 'dayjs';
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import { models, settings } from '$lib/stores';
	import type { Model } from '$lib/stores';
	import { formatDate } from '$lib/utils';
	import { DEFAULT_MODEL_IMAGE_URL } from '$lib/constants';
	import type { ChatHistory, ChatMessage } from '$lib/types/chat';

	import Name from './Name.svelte';
	import ProfileImage from './ProfileImage.svelte';
	import Skeleton from './Skeleton.svelte';
	import Image from '$lib/components/common/Image.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import FileItem from '$lib/components/common/FileItem.svelte';

	import Error from './Error.svelte';
	import Citations from './Citations.svelte';
	import CodeExecutions from './CodeExecutions.svelte';
	import ContentRenderer from './ContentRenderer.svelte';
	import ContentBlocks from './ContentBlocks.svelte';
	import MessageEditor from './ResponseMessage/MessageEditor.svelte';
	import MessageActions from './ResponseMessage/MessageActions.svelte';

	const i18n = getContext<Writable<i18nType>>('i18n');

	type ModelAction = { id: string; name: string; icon_url?: string };

	let {
		history,
		messageId,
		siblings,
		gotoMessage = () => {},
		showPreviousMessage,
		showNextMessage,
		updateChat,
		editMessage,
		saveMessage = undefined,
		actionMessage,
		deleteMessage,
		submitMessage = undefined,
		continueResponse,
		regenerateResponse,
		addMessages,
		isLastMessage = true,
		readOnly = false
	}: {
		history: ChatHistory;
		messageId: string;
		siblings: string[];
		gotoMessage?: (message: ChatMessage, index: number) => void;
		showPreviousMessage: (message: ChatMessage) => void;
		showNextMessage: (message: ChatMessage) => void;
		updateChat: () => void;
		editMessage: (id: string, content: string, submit?: boolean) => void;
		/** 未使用(親の呼び出し互換のため受け取りだけ維持)。 */
		saveMessage?: (id: string, message: ChatMessage) => void;
		actionMessage: (actionId: string, message: ChatMessage) => void;
		deleteMessage: (id: string) => void;
		/** 未使用(旧 on:select 死に購読の撤去で不要化。呼び出し互換のため維持)。 */
		submitMessage?: (parentId: string, prompt: string) => void;
		continueResponse: () => void;
		regenerateResponse: (message: ChatMessage) => void;
		addMessages: (payload: {
			modelId: string;
			parentId: string;
			messages: { role: string; content: string }[];
		}) => void;
		isLastMessage?: boolean;
		readOnly?: boolean;
	} = $props();

	const message = $derived(history.messages[messageId]);
	const model = $derived(
		$models.find((m) => m.id === message.model) as (Model & { actions?: ModelAction[] }) | undefined
	);

	// HTML の dir 属性は小文字へ正規化(store は 'LTR' | 'RTL' | 'auto')
	const chatDirection = $derived(
		$settings?.chatDirection
			? ($settings.chatDirection.toLowerCase() as 'ltr' | 'rtl' | 'auto')
			: undefined
	);

	const status = $derived(
		(message.statusHistory ?? (message.status ? [message.status] : [])).at(-1)
	);

	const citationsEnabled = $derived(
		(model?.info?.meta?.capabilities as { citations?: boolean } | undefined)?.citations ?? true
	);

	let edit = $state(false);

	const handleSourceClick = async (_id: string, idx: number | string | undefined) => {
		let sourceButton = document.getElementById(`source-${message.id}-${idx}`);
		const sourcesCollapsible = document.getElementById(`collapsible-${message.id}`);

		if (sourceButton) {
			sourceButton.click();
		} else if (sourcesCollapsible) {
			// Sources の Collapsible を開いてから source ボタンをクリックする
			sourcesCollapsible
				.querySelector('div:first-child')
				?.dispatchEvent(new PointerEvent('pointerup', {}));

			// DOM 反映を待つ
			await new Promise((resolve) => {
				requestAnimationFrame(() => {
					requestAnimationFrame(resolve);
				});
			});

			sourceButton = document.getElementById(`source-${message.id}-${idx}`);
			sourceButton?.click();
		}
	};
</script>

{#key message.id}
	<div class=" flex w-full message-{message.id}" id="message-{message.id}" dir={chatDirection}>
		<div class={`shrink-0 ltr:mr-3 rtl:ml-3`}>
			<ProfileImage
				src={model?.info?.meta?.profile_image_url ??
					($i18n.language === 'dg-DG' ? `/doge.png` : DEFAULT_MODEL_IMAGE_URL)}
				className={'size-8'}
			/>
		</div>

		<div class="flex-auto w-0 pl-1">
			<Name>
				<Tooltip content={model?.name ?? message.model} placement="top-start">
					<span class="line-clamp-1 text-black dark:text-white">
						{model?.name ?? message.model}
					</span>
				</Tooltip>

				{#if message.timestamp}
					<div
						class=" self-center text-xs invisible group-hover:visible text-content-subtle font-medium first-letter:capitalize ml-0.5 translate-y-[1px]"
					>
						<Tooltip content={dayjs(message.timestamp * 1000).format('LLLL')}>
							<span class="line-clamp-1">{formatDate(message.timestamp * 1000)}</span>
						</Tooltip>
					</div>
				{/if}
			</Name>

			<div>
				<div class="chat-{message.role} w-full min-w-full markdown-prose">
					<div>
						{#if status && !status.hidden}
							<div class="status-description flex items-center gap-2 py-0.5">
								{#if status.done === false}
									<div class="">
										<Spinner className="size-4" />
									</div>
								{/if}

								{#if status.action === 'knowledge_search'}
									<div class="flex flex-col justify-center -space-y-0.5">
										<div
											class="{status.done === false
												? 'shimmer'
												: ''} text-content-muted text-base line-clamp-1 text-wrap"
										>
											{$i18n.t(`Searching Knowledge for "{{searchQuery}}"`, {
												searchQuery: status.query
											})}
										</div>
									</div>
								{:else}
									<div class="flex flex-col justify-center -space-y-0.5">
										<div
											class="{status.done === false
												? 'shimmer'
												: ''} text-content-muted text-base line-clamp-1 text-wrap"
										>
											{#if status.description?.includes('{{searchQuery}}')}
												{$i18n.t(status.description, {
													searchQuery: status.query
												})}
											{:else if status.description === 'No search query generated'}
												{$i18n.t('No search query generated')}
											{:else if status.description === 'Generating search query'}
												{$i18n.t('Generating search query')}
											{:else}
												{status.description}
											{/if}
										</div>
									</div>
								{/if}
							</div>
						{/if}

						{#if message.files && message.files.filter((f) => f.type === 'image').length > 0}
							<div class="my-1 w-full flex overflow-x-auto gap-2 flex-wrap">
								{#each message.files as file}
									<div>
										{#if file.type === 'image'}
											<Image src={file.url ?? ''} alt={message.content} />
										{:else}
											<FileItem
												item={file}
												url={file.url}
												name={file.name ?? ''}
												type={file.type}
												size={file?.size}
												colorClassName="bg-white dark:bg-gray-850 "
											/>
										{/if}
									</div>
								{/each}
							</div>
						{/if}

						{#if edit}
							<MessageEditor
								messageId={message.id}
								content={message.content}
								onSave={(content) => {
									editMessage(message.id, content, false);
									edit = false;
								}}
								onSaveAsCopy={(content) => {
									editMessage(message.id, content);
									edit = false;
								}}
								onCancel={() => {
									edit = false;
								}}
							/>
						{:else}
							<div class="w-full flex flex-col relative py-1.5" id="response-content-container">
								{#if (message.content_blocks ?? []).length > 0}
									<!-- #17 L0: content_blocks があればブロックから直接描画(details 擬似 HTML を経由しない) -->
									<ContentBlocks
										id={message.id}
										blocks={message.content_blocks ?? []}
										done={message.done ?? false}
										{history}
										sources={message.sources}
										floatingButtons={(message.done ?? false) && !readOnly}
										{model}
										onSourceClick={handleSourceClick}
										onAddMessages={addMessages}
									/>
								{:else if message.content === '' && !message.error}
									{#if message.done}
										<!-- llens: 本文ゼロのまま確定した応答(中断・接続断)。
										     無言のスピナーで固まらせず、途切れたことを明示する -->
										<div class="mb-1.5 text-sm text-content-subtle italic" dir="auto">
											{$i18n.t('The response was interrupted and no content was received.')}
										</div>
									{:else}
										<Skeleton />
									{/if}
								{:else if message.content}
									<!-- エラーがあっても本文は常に表示する(error は {content} オブジェクト形のみ —
									     旧 error === true 形式の互換は撤去済み) -->
									<ContentRenderer
										id={message.id}
										{history}
										content={message.content}
										sources={message.sources}
										floatingButtons={(message.done ?? false) && !readOnly}
										{model}
										onSourceClick={handleSourceClick}
										onAddMessages={addMessages}
									/>
								{/if}

								{#if message.error}
									<Error
										content={typeof message.error === 'object'
											? message.error.content
											: message.content}
									/>
								{/if}

								{#if message.sources && citationsEnabled}
									<Citations id={message.id} sources={message.sources} />
								{/if}

								{#if message.code_executions}
									<CodeExecutions codeExecutions={message.code_executions} />
								{/if}
							</div>
						{/if}
					</div>
				</div>

				{#if !edit}
					<MessageActions
						{message}
						{model}
						{siblings}
						{isLastMessage}
						{readOnly}
						onEdit={() => {
							edit = true;
						}}
						{gotoMessage}
						{showPreviousMessage}
						{showNextMessage}
						{continueResponse}
						{regenerateResponse}
						{actionMessage}
						{deleteMessage}
					/>
				{/if}
			</div>
		</div>
	</div>
{/key}

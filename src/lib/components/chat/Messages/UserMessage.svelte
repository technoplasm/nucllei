<script lang="ts">
	import dayjs from 'dayjs';
	import { toast } from 'svelte-sonner';
	import { tick, getContext } from 'svelte';

	import { models, settings, skills, user as _user } from '$lib/stores';
	import { copyToClipboard as _copyToClipboard, formatDate } from '$lib/utils';
	import { findSkillByCommand } from '$lib/utils/skills';
	import type { ChatHistory, ChatMessage } from '$lib/types/chat';

	import Name from './Name.svelte';
	import ProfileImage from './ProfileImage.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import FileItem from '$lib/components/common/FileItem.svelte';
	import Markdown from './Markdown.svelte';
	import Image from '$lib/components/common/Image.svelte';

	import localizedFormat from 'dayjs/plugin/localizedFormat';

	const i18n = getContext('i18n');
	dayjs.extend(localizedFormat);

	let {
		user,
		history,
		messageId,
		siblings,
		gotoMessage,
		showPreviousMessage,
		showNextMessage,
		editMessage,
		readOnly
	}: {
		user: { name: string; profile_image_url?: string } | undefined;
		history: ChatHistory;
		messageId: string;
		siblings: string[];
		gotoMessage: (message: ChatMessage, index: number) => void;
		showPreviousMessage: (message: ChatMessage) => void;
		showNextMessage: (message: ChatMessage) => void;
		editMessage: (id: string, content: string) => void;
		readOnly: boolean;
	} = $props();

	let messageIndexEdit = $state(false);

	let edit = $state(false);
	let editedContent = $state('');
	let messageEditTextAreaElement: HTMLTextAreaElement | null = $state(null);

	const message = $derived(history.messages[messageId]);

	// HTML の dir 属性は小文字(store は 'LTR' | 'RTL' | 'auto'。属性自体は
	// 大文字も通るが、型どおり小文字へ正規化する)。未設定なら属性を出さない。
	const chatDirection = $derived(
		$settings?.chatDirection
			? ($settings.chatDirection.toLowerCase() as 'ltr' | 'rtl' | 'auto')
			: undefined
	);

	// llens(issue #35): 有効な /command で始まる送信済みメッセージだけ、先頭トークンを
	// chip 風に描画する。ヒットしなければ完全に従来どおり(挙動を変えない)。
	const skillMatch = $derived(findSkillByCommand($skills, message.content));
	const skillRest = $derived(skillMatch ? message.content.slice(1 + skillMatch.id.length) : '');

	const copyToClipboard = async (text: string) => {
		const res = await _copyToClipboard(text);
		if (res) {
			toast.success($i18n.t('Copying to clipboard was successful!'));
		}
	};

	const editMessageHandler = async () => {
		edit = true;
		editedContent = message.content;

		await tick();

		if (messageEditTextAreaElement) {
			messageEditTextAreaElement.style.height = '';
			messageEditTextAreaElement.style.height = `${messageEditTextAreaElement.scrollHeight}px`;
			messageEditTextAreaElement.focus();
		}
	};

	const editMessageConfirmHandler = async () => {
		editMessage(message.id, editedContent);

		edit = false;
		editedContent = '';
	};

	const cancelEditMessage = () => {
		edit = false;
		editedContent = '';
	};
</script>

<div class=" flex w-full user-message" dir={chatDirection} id="message-{message.id}">
	{#if !($settings?.chatBubble ?? true)}
		<div class={`shrink-0 ltr:mr-3 rtl:ml-3 max-sm:hidden`}>
			<ProfileImage
				src={message.user
					? ($models.find((m) => m.id === message.user)?.info?.meta?.profile_image_url ??
						'/user.png')
					: (user?.profile_image_url ?? '/user.png')}
				className={'size-8'}
			/>
		</div>
	{/if}
	<div class="flex-auto w-0 max-w-full pl-1">
		{#if !($settings?.chatBubble ?? true)}
			<div>
				<Name>
					{#if message.user}
						{$i18n.t('You')}
						<span class=" text-content-muted text-sm font-medium">{message?.user ?? ''}</span>
					{:else if $settings?.showUsername || $_user?.name !== user?.name}
						{user?.name}
					{:else}
						{$i18n.t('You')}
					{/if}

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
			</div>
		{/if}

		<div class="chat-{message.role} w-full min-w-full markdown-prose">
			{#if message.files}
				<div class="mt-2.5 mb-1 w-full flex flex-col justify-end overflow-x-auto gap-1 flex-wrap">
					{#each message.files as file}
						<div class={($settings?.chatBubble ?? true) ? 'self-end' : ''}>
							{#if file.type === 'image'}
								<Image src={file.url ?? ''} imageClassName=" max-h-96 rounded-lg" />
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

			{#if message.content !== ''}
				{#if edit === true}
					<div class=" w-full bg-gray-50 dark:bg-gray-800 rounded-3xl px-5 py-3 mb-2">
						<div class="max-h-96 overflow-auto">
							<textarea
								id="message-edit-{message.id}"
								bind:this={messageEditTextAreaElement}
								class=" bg-transparent outline-hidden w-full resize-none"
								bind:value={editedContent}
								oninput={(e) => {
									const target = e.currentTarget;
									target.style.height = '';
									target.style.height = `${target.scrollHeight}px`;
								}}
								onkeydown={(e) => {
									if (e.key === 'Escape') {
										document.getElementById('close-edit-message-button')?.click();
									}

									const isCmdOrCtrlPressed = e.metaKey || e.ctrlKey;
									const isEnterPressed = e.key === 'Enter';

									if (isCmdOrCtrlPressed && isEnterPressed) {
										document.getElementById('confirm-edit-message-button')?.click();
									}
								}}
							></textarea>
						</div>

						<!-- 「保存」(その場で書き換え)は置かない: 履歴は append-only(#31)でサーバが必ず拒否する。
						     書き換えるなら「送信」= 兄弟ブランチとして送り直す(#119) -->
						<div class=" mt-2 mb-1 flex justify-end text-sm font-medium">
							<div class="flex space-x-1.5">
								<button
									id="close-edit-message-button"
									class="px-4 py-2 bg-white dark:bg-gray-900 hover:bg-gray-100 dark:hover:bg-gray-850 text-content rounded-3xl"
									onclick={() => {
										cancelEditMessage();
									}}
								>
									{$i18n.t('Cancel')}
								</button>

								<button
									id="confirm-edit-message-button"
									class=" px-4 py-2 bg-gray-900 text-white hover:bg-gray-850 dark:bg-white dark:text-black dark:hover:bg-gray-100 rounded-3xl"
									onclick={() => {
										editMessageConfirmHandler();
									}}
								>
									{$i18n.t('Send')}
								</button>
							</div>
						</div>
					</div>
				{:else}
					<div class="w-full">
						<div class="flex {($settings?.chatBubble ?? true) ? 'justify-end pb-1' : 'w-full'}">
							<div
								class="rounded-3xl {($settings?.chatBubble ?? true)
									? `max-w-[90%] px-5 py-2 bg-secondary text-secondary-content ${
											message.files ? 'rounded-tr-lg' : ''
										}`
									: ' w-full'}"
							>
								{#if message.content}
									{#if skillMatch}
										<span class="skill-command-token">/{skillMatch.id}</span>
										<Markdown id={message.id} content={skillRest} />
									{:else}
										<Markdown id={message.id} content={message.content} />
									{/if}
								{/if}
							</div>
						</div>

						<div
							class=" flex {($settings?.chatBubble ?? true)
								? 'justify-end'
								: ''}  text-content-muted"
						>
							{#if !($settings?.chatBubble ?? true)}
								{#if siblings.length > 1}
									<div class="flex self-center" dir="ltr">
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
											<div
												class="text-sm flex justify-center font-semibold self-center text-content min-w-fit"
											>
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
							{/if}
							<!-- #120: 応答側(MessageActions)と同じ chat.edit ゲート。UX 用の導線隠しで、境界はサーバ側 -->
							{#if !readOnly && ($_user?.role === 'user' ? ($_user?.permissions?.chat?.edit ?? true) : true)}
								<Tooltip content={$i18n.t('Edit')} placement="bottom">
									<button
										class="invisible group-hover:visible p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-white hover:text-black edit-user-message-button"
										aria-label={$i18n.t('Edit')}
										onclick={() => {
											editMessageHandler();
										}}
									>
										<span class="i-lucide-pencil size-4 block"></span>
									</button>
								</Tooltip>
							{/if}

							<Tooltip content={$i18n.t('Copy')} placement="bottom">
								<button
									class="invisible group-hover:visible p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-white hover:text-black"
									aria-label={$i18n.t('Copy')}
									onclick={() => {
										copyToClipboard(message.content);
									}}
								>
									<span class="i-lucide-copy size-4 block"></span>
								</button>
							</Tooltip>

							{#if $settings?.chatBubble ?? true}
								{#if siblings.length > 1}
									<div class="flex self-center" dir="ltr">
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
											<div
												class="text-sm flex justify-center font-semibold self-center text-content min-w-fit"
											>
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
							{/if}
						</div>
					</div>
				{/if}
			{/if}
		</div>
	</div>
</div>

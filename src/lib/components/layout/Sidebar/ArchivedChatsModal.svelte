<script lang="ts">
	import fileSaver from 'file-saver';
	const { saveAs } = fileSaver;
	import { toast } from 'svelte-sonner';
	import dayjs from 'dayjs';
	import { getContext } from 'svelte';
	import localizedFormat from 'dayjs/plugin/localizedFormat';

	dayjs.extend(localizedFormat);

	import {
		archiveChatById,
		deleteChatById,
		getAllArchivedChats,
		getArchivedChatList
	} from '$lib/apis/chats';

	import Modal from '$lib/components/common/Modal.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import UnarchiveAllConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	const i18n = getContext('i18n');

	type ArchivedChat = {
		id: string;
		title: string;
		created_at: number;
	};

	let {
		show = $bindable(false),
		onChange = undefined
	}: {
		show?: boolean;
		onChange?: () => void;
	} = $props();

	let chats: ArchivedChat[] = $state([]);

	let searchValue = $state('');
	let showUnarchiveAllConfirmDialog = $state(false);

	const unarchiveChatHandler = async (chatId: string) => {
		await archiveChatById(chatId).catch((error) => {
			toast.error(`${error}`);
		});

		chats = await getArchivedChatList();
		onChange?.();
	};

	const deleteChatHandler = async (chatId: string) => {
		await deleteChatById(chatId).catch((error) => {
			toast.error(`${error}`);
		});

		chats = await getArchivedChatList();
	};

	const exportChatsHandler = async () => {
		const chats = await getAllArchivedChats();
		let blob = new Blob([JSON.stringify(chats)], {
			type: 'application/json'
		});
		saveAs(blob, `${$i18n.t('archived-chat-export')}-${Date.now()}.json`);
	};

	const unarchiveAllHandler = async () => {
		for (const chat of chats) {
			await archiveChatById(chat.id);
		}
		chats = await getArchivedChatList();
	};

	$effect(() => {
		if (show) {
			(async () => {
				chats = await getArchivedChatList();
			})();
		}
	});
</script>

<UnarchiveAllConfirmDialog
	bind:show={showUnarchiveAllConfirmDialog}
	message={$i18n.t('Are you sure you want to unarchive all archived chats?')}
	confirmLabel={$i18n.t('Unarchive All')}
	onConfirm={() => {
		unarchiveAllHandler();
	}}
/>

<Modal size="lg" bind:show>
	<div>
		<div class=" flex justify-between text-content px-5 pt-4 pb-1">
			<div class=" text-lg font-medium self-center">{$i18n.t('Archived Chats')}</div>
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

		<div class="flex flex-col w-full px-5 pb-4 text-content">
			<div class=" flex w-full mt-2 space-x-2">
				<div class="flex flex-1">
					<div class=" self-center ml-1 mr-3">
						<span class="i-lucide-search size-4 block"></span>
					</div>
					<input
						class=" w-full text-sm pr-4 py-1 rounded-r-xl outline-hidden bg-transparent"
						bind:value={searchValue}
						placeholder={$i18n.t('Search Chats')}
					/>
				</div>
			</div>
			<hr class="border-gray-100 dark:border-gray-850 my-2" />
			<div class=" flex flex-col w-full sm:flex-row sm:justify-center sm:space-x-6">
				{#if chats.length > 0}
					<div class="w-full">
						<div class="text-left text-sm w-full mb-3 max-h-[22rem] overflow-y-scroll">
							<div class="relative overflow-x-auto">
								<table class="w-full text-sm text-left text-content-muted table-auto">
									<thead
										class="text-xs text-content uppercase bg-transparent border-b-2 border-gray-50 dark:border-gray-850"
									>
										<tr>
											<th scope="col" class="px-3 py-2"> {$i18n.t('Name')} </th>
											<th scope="col" class="px-3 py-2 hidden md:flex">
												{$i18n.t('Created At')}
											</th>
											<th scope="col" class="px-3 py-2 text-right"></th>
										</tr>
									</thead>
									<tbody>
										{#each chats.filter((c) => searchValue === '' || c.title
													.toLowerCase()
													.includes(searchValue.toLowerCase())) as chat, idx}
											<tr
												class="bg-transparent {idx !== chats.length - 1 &&
													'border-b'} dark:bg-gray-900 border-gray-50 dark:border-gray-850 text-xs"
											>
												<td class="px-3 py-1 w-2/3">
													<a href="/c/{chat.id}" target="_blank">
														<div class=" underline line-clamp-1">
															{chat.title}
														</div>
													</a>
												</td>

												<td class=" px-3 py-1 hidden md:flex h-[2.5rem]">
													<div class="my-auto">
														{dayjs(chat.created_at * 1000).format('LLL')}
													</div>
												</td>

												<td class="px-3 py-1 text-right">
													<div class="flex justify-end w-full">
														<Tooltip content={$i18n.t('Unarchive Chat')}>
															<button
																class="self-center w-fit text-sm px-2 py-2 hover:bg-black/5 dark:hover:bg-white/5 rounded-xl"
																aria-label={$i18n.t('Unarchive Chat')}
																onclick={() => {
																	unarchiveChatHandler(chat.id);
																}}
															>
																<span class="i-lucide-archive-restore size-4 block"></span>
															</button>
														</Tooltip>

														<Tooltip content={$i18n.t('Delete Chat')}>
															<button
																class="self-center w-fit text-sm px-2 py-2 hover:bg-black/5 dark:hover:bg-white/5 rounded-xl"
																aria-label={$i18n.t('Delete Chat')}
																onclick={() => {
																	deleteChatHandler(chat.id);
																}}
															>
																<span class="i-lucide-trash-2 size-4 block"></span>
															</button>
														</Tooltip>
													</div>
												</td>
											</tr>
										{/each}
									</tbody>
								</table>
							</div>
						</div>

						<div class="flex flex-wrap text-sm font-medium gap-1.5 mt-2 m-1 justify-end w-full">
							<button
								class=" px-3.5 py-1.5 font-medium hover:bg-black/5 dark:hover:bg-white/5 outline outline-1 outline-gray-300 dark:outline-gray-800 rounded-3xl"
								onclick={() => {
									showUnarchiveAllConfirmDialog = true;
								}}
							>
								{$i18n.t('Unarchive All Archived Chats')}
							</button>

							<button
								class="px-3.5 py-1.5 font-medium hover:bg-black/5 dark:hover:bg-white/5 outline outline-1 outline-gray-300 dark:outline-gray-800 rounded-3xl"
								onclick={() => {
									exportChatsHandler();
								}}
							>
								{$i18n.t('Export All Archived Chats')}
							</button>
						</div>
					</div>
				{:else}
					<div class="text-left text-sm w-full mb-8">
						{$i18n.t('You have no archived conversations.')}
					</div>
				{/if}
			</div>
		</div>
	</div>
</Modal>

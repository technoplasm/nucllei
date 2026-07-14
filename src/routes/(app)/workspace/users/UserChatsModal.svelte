<script lang="ts">
	import { toast } from 'svelte-sonner';
	import dayjs from 'dayjs';
	import { getContext } from 'svelte';
	import localizedFormat from 'dayjs/plugin/localizedFormat';
	dayjs.extend(localizedFormat);

	import { getChatListByUserId, deleteChatById } from '$lib/apis/chats';

	import Modal from '$lib/components/common/Modal.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';

	const i18n = getContext('i18n');

	let { show = $bindable(false), user }: { show?: boolean; user: any } = $props();

	let chats = $state<any[] | null>(null);
	let showDeleteConfirmDialog = $state(false);
	let chatToDelete = $state<string | null>(null);

	let sortKey = $state('updated_at');
	let sortOrder = $state('desc');

	const sortedChats = $derived(
		[...(chats ?? [])].sort((a, b) => {
			if (a[sortKey] < b[sortKey]) return sortOrder === 'asc' ? -1 : 1;
			if (a[sortKey] > b[sortKey]) return sortOrder === 'asc' ? 1 : -1;
			return 0;
		})
	);

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

	function setSortKey(key: string) {
		if (sortKey === key) {
			sortOrder = sortOrder === 'asc' ? 'desc' : 'asc';
		} else {
			sortKey = key;
			sortOrder = 'asc';
		}
	}
</script>

<ConfirmDialog
	bind:show={showDeleteConfirmDialog}
	on:confirm={() => {
		if (chatToDelete) {
			deleteChatHandler(chatToDelete);
			chatToDelete = null;
		}
	}}
/>

<Modal size="lg" bind:show>
	<div class=" flex justify-between text-content px-5 pt-4">
		<div class=" text-lg font-medium self-center capitalize">
			{$i18n.t("{{user}}'s Chats", { user: user.name })}
		</div>
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

	<div class="flex flex-col md:flex-row w-full px-5 pt-2 pb-4 md:space-x-4 text-content">
		<div class=" flex flex-col w-full sm:flex-row sm:justify-center sm:space-x-6">
			{#if chats}
				{#if chats.length > 0}
					<div class="text-left text-sm w-full mb-4 max-h-[22rem] overflow-y-scroll">
						<div class="relative overflow-x-auto">
							<table class="w-full text-sm text-left text-content-muted table-auto">
								<thead class="text-xs text-content uppercase bg-transparent border-b-2 border-line">
									<tr>
										<th scope="col" class="px-3 py-2 select-none">
											<button
												class="flex gap-1 items-center cursor-pointer uppercase"
												onclick={() => setSortKey('title')}
											>
												{$i18n.t('Title')}
												{#if sortKey === 'title'}
													{sortOrder === 'asc' ? '▲' : '▼'}
												{:else}
													<span class="invisible">▲</span>
												{/if}
											</button>
										</th>
										<th scope="col" class="px-3 py-2 hidden md:table-cell select-none">
											<button
												class="flex gap-1 items-center cursor-pointer uppercase justify-end w-full"
												onclick={() => setSortKey('updated_at')}
											>
												{$i18n.t('Updated at')}
												{#if sortKey === 'updated_at'}
													{sortOrder === 'asc' ? '▲' : '▼'}
												{:else}
													<span class="invisible">▲</span>
												{/if}
											</button>
										</th>
										<th scope="col" class="px-3 py-2 text-right"></th>
									</tr>
								</thead>
								<tbody>
									{#each sortedChats as chat, idx (chat.id)}
										<tr
											class="bg-transparent {idx !== sortedChats.length - 1 &&
												'border-b'} border-line dark:bg-gray-900 text-xs"
										>
											<td class="px-3 py-1">
												<a href="/s/{chat.id}" target="_blank">
													<div class=" underline line-clamp-1 max-w-96">
														{chat.title}
													</div>
												</a>
											</td>

											<td class=" px-3 py-1 hidden md:flex h-[2.5rem] justify-end">
												<div class="my-auto shrink-0">
													{dayjs(chat.updated_at * 1000).format('LLL')}
												</div>
											</td>

											<td class="px-3 py-1 text-right">
												<div class="flex justify-end w-full">
													<Tooltip content={$i18n.t('Delete Chat')}>
														<button
															class="self-center w-fit text-sm px-2 py-2 hover:bg-black/5 dark:hover:bg-white/5 rounded-xl"
															aria-label={$i18n.t('Delete Chat')}
															onclick={() => {
																chatToDelete = chat.id;
																showDeleteConfirmDialog = true;
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
				{:else}
					<div class="text-left text-sm w-full mb-8">
						{user.name}
						{$i18n.t('has no conversations.')}
					</div>
				{/if}
			{:else}
				<Spinner />
			{/if}
		</div>
	</div>
</Modal>

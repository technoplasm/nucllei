<script lang="ts">
	import { DropdownMenu } from 'bits-ui';
	import { getContext, type Snippet } from 'svelte';

	import fileSaver from 'file-saver';
	const { saveAs } = fileSaver;

	import jsPDF from 'jspdf';
	import html2canvas from 'html2canvas-pro';

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DropdownContent from '$lib/components/common/DropdownContent.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import {
		getChatById,
		getChatPinnedStatusById,
		toggleChatPinnedStatusById
	} from '$lib/apis/chats';
	import { theme, user } from '$lib/stores';
	import { createMessagesList } from '$lib/utils';

	const i18n = getContext('i18n');

	let {
		chatId = '',
		shareHandler,
		cloneChatHandler,
		archiveChatHandler,
		renameHandler,
		deleteHandler,
		onClose,
		onChange = undefined,
		children
	}: {
		chatId?: string;
		shareHandler: () => void;
		cloneChatHandler: () => void;
		archiveChatHandler: () => void;
		renameHandler: () => void;
		deleteHandler: () => void;
		onClose: () => void;
		onChange?: () => void;
		children?: Snippet;
	} = $props();

	let show = $state(false);
	let pinned = $state(false);

	const pinHandler = async () => {
		await toggleChatPinnedStatusById(chatId);
		onChange?.();
	};

	const checkPinned = async () => {
		pinned = await getChatPinnedStatusById(chatId);
	};

	const getChatAsText = async (chat: Record<string, any>) => {
		const history = chat.chat.history;
		const messages = createMessagesList(history, history.currentId);
		const chatText = messages.reduce((a: string, message: Record<string, any>) => {
			return `${a}### ${message.role.toUpperCase()}\n${message.content}\n\n`;
		}, '');

		return chatText.trim();
	};

	const downloadTxt = async () => {
		const chat = await getChatById(chatId);
		if (!chat) {
			return;
		}

		const chatText = await getChatAsText(chat);
		let blob = new Blob([chatText], {
			type: 'text/plain'
		});

		saveAs(blob, `chat-${chat.chat.title}.txt`);
	};

	const downloadPdf = async () => {
		const chat = await getChatById(chatId);

		const containerElement = document.getElementById('messages-container');

		if (containerElement) {
			try {
				const isDarkMode = $theme.includes('dark'); // Check theme mode

				// Define a fixed virtual screen size
				const virtualWidth = 1024; // Fixed width (adjust as needed)
				const virtualHeight = 1400; // Fixed height (adjust as needed)

				// Clone the container to avoid layout shifts
				const clonedElement = containerElement.cloneNode(true) as HTMLElement;
				clonedElement.style.width = `${virtualWidth}px`; // Apply fixed width
				clonedElement.style.height = 'auto'; // Allow content to expand

				document.body.appendChild(clonedElement); // Temporarily add to DOM

				// Render to canvas with predefined width
				const canvas = await html2canvas(clonedElement, {
					backgroundColor: isDarkMode ? '#000' : '#fff',
					useCORS: true,
					scale: 2, // Keep at 1x to avoid unexpected enlargements
					width: virtualWidth, // Set fixed virtual screen width
					windowWidth: virtualWidth, // Ensure consistent rendering
					windowHeight: virtualHeight
				});

				document.body.removeChild(clonedElement); // Clean up temp element

				const imgData = canvas.toDataURL('image/png');

				// A4 page settings
				const pdf = new jsPDF('p', 'mm', 'a4');
				const imgWidth = 210; // A4 width in mm
				const pageHeight = 297; // A4 height in mm

				// Maintain aspect ratio
				const imgHeight = (canvas.height * imgWidth) / canvas.width;
				let heightLeft = imgHeight;
				let position = 0;

				// Set page background for dark mode
				if (isDarkMode) {
					pdf.setFillColor(0, 0, 0);
					pdf.rect(0, 0, imgWidth, pageHeight, 'F'); // Apply black bg
				}

				pdf.addImage(imgData, 'PNG', 0, position, imgWidth, imgHeight);
				heightLeft -= pageHeight;

				// Handle additional pages
				while (heightLeft > 0) {
					position -= pageHeight;
					pdf.addPage();

					if (isDarkMode) {
						pdf.setFillColor(0, 0, 0);
						pdf.rect(0, 0, imgWidth, pageHeight, 'F');
					}

					pdf.addImage(imgData, 'PNG', 0, position, imgWidth, imgHeight);
					heightLeft -= pageHeight;
				}

				pdf.save(`chat-${chat.chat.title}.pdf`);
			} catch (error) {
				console.error('Error generating PDF', error);
			}
		}
	};

	const downloadJSONExport = async () => {
		const chat = await getChatById(chatId);

		if (chat) {
			let blob = new Blob([JSON.stringify([chat])], {
				type: 'application/json'
			});
			saveAs(blob, `chat-export-${Date.now()}.json`);
		}
	};

	$effect(() => {
		if (show) {
			checkPinned();
		}
	});
</script>

<Dropdown
	bind:show
	onChange={(state) => {
		if (state === false) {
			onClose();
		}
	}}
>
	<Tooltip content={$i18n.t('More')}>
		{@render children?.()}
	</Tooltip>

	{#snippet content()}
		<DropdownContent
			className="w-full max-w-[200px] rounded-xl px-1 py-1.5 z-50 bg-white dark:bg-gray-850 dark:text-white shadow-lg"
			sideOffset={-2}
			side="bottom"
			align="start"
		>
			<DropdownMenu.Item
				class="flex gap-2 items-center px-3 py-1.5 text-sm  cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md"
				onSelect={() => {
					pinHandler();
				}}
			>
				{#if pinned}
					<span class="i-lucide-bookmark-x size-4 block"></span>
					<div class="flex items-center">{$i18n.t('Unpin')}</div>
				{:else}
					<span class="i-lucide-bookmark size-4 block"></span>
					<div class="flex items-center">{$i18n.t('Pin')}</div>
				{/if}
			</DropdownMenu.Item>

			<DropdownMenu.Item
				class="flex gap-2 items-center px-3 py-1.5 text-sm  cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md"
				onSelect={() => {
					renameHandler();
				}}
			>
				<span class="i-lucide-pencil size-4 block"></span>
				<div class="flex items-center">{$i18n.t('Rename')}</div>
			</DropdownMenu.Item>

			<DropdownMenu.Item
				class="flex gap-2 items-center px-3 py-1.5 text-sm  cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md"
				onSelect={() => {
					cloneChatHandler();
				}}
			>
				<span class="i-lucide-copy size-4 block"></span>
				<div class="flex items-center">{$i18n.t('Clone')}</div>
			</DropdownMenu.Item>

			<DropdownMenu.Item
				class="flex gap-2 items-center px-3 py-1.5 text-sm  cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md"
				onSelect={() => {
					archiveChatHandler();
				}}
			>
				<span class="i-lucide-archive size-4 block"></span>
				<div class="flex items-center">{$i18n.t('Archive')}</div>
			</DropdownMenu.Item>

			{#if $user?.role === 'admin' || ($user?.permissions?.chat?.share ?? true)}
				<DropdownMenu.Item
					class="flex gap-2 items-center px-3 py-1.5 text-sm  cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800  rounded-md"
					onSelect={() => {
						shareHandler();
					}}
				>
					<span class="i-lucide-share-2 size-4 block"></span>
					<div class="flex items-center">{$i18n.t('Share')}</div>
				</DropdownMenu.Item>
			{/if}

			<DropdownMenu.Sub>
				<DropdownMenu.SubTrigger
					class="flex gap-2 items-center px-3 py-2 text-sm  cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md"
				>
					<span class="i-lucide-download size-4 block"></span>

					<div class="flex items-center">{$i18n.t('Download')}</div>
				</DropdownMenu.SubTrigger>
				<DropdownContent
					sub
					className="w-full rounded-xl px-1 py-1.5 z-50 bg-white dark:bg-gray-850 dark:text-white shadow-lg"
					sideOffset={8}
				>
					<DropdownMenu.Item
						class="flex gap-2 items-center px-3 py-2 text-sm  cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md"
						onSelect={() => {
							downloadJSONExport();
						}}
					>
						<div class="flex items-center line-clamp-1">{$i18n.t('Export chat (.json)')}</div>
					</DropdownMenu.Item>
					<DropdownMenu.Item
						class="flex gap-2 items-center px-3 py-2 text-sm  cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md"
						onSelect={() => {
							downloadTxt();
						}}
					>
						<div class="flex items-center line-clamp-1">{$i18n.t('Plain text (.txt)')}</div>
					</DropdownMenu.Item>

					<DropdownMenu.Item
						class="flex gap-2 items-center px-3 py-2 text-sm  cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md"
						onSelect={() => {
							downloadPdf();
						}}
					>
						<div class="flex items-center line-clamp-1">{$i18n.t('PDF document (.pdf)')}</div>
					</DropdownMenu.Item>
				</DropdownContent>
			</DropdownMenu.Sub>
			<DropdownMenu.Item
				class="flex  gap-2  items-center px-3 py-1.5 text-sm  cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md"
				onSelect={() => {
					deleteHandler();
				}}
			>
				<span class="i-lucide-trash-2 size-4 block"></span>
				<div class="flex items-center">{$i18n.t('Delete')}</div>
			</DropdownMenu.Item>
		</DropdownContent>
	{/snippet}
</Dropdown>

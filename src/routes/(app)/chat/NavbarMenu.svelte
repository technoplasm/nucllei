<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { DropdownMenu } from 'bits-ui';
	import { getContext, type Snippet } from 'svelte';

	import fileSaver from 'file-saver';
	const { saveAs } = fileSaver;

	import jsPDF from 'jspdf';
	import html2canvas from 'html2canvas-pro';

	import { copyToClipboard, createMessagesList } from '$lib/utils';

	import { chatId, showControls, user } from '$lib/stores';
	import { flyAndScale } from '$lib/utils/transitions';

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import { getChatById } from '$lib/apis/chats';

	const i18n = getContext('i18n');

	let {
		shareHandler,
		chat,
		onClose = () => {},
		children
	}: {
		shareHandler: () => void;
		chat: any;
		onClose?: () => void;
		children?: Snippet;
	} = $props();

	const getChatAsText = async () => {
		const history = chat.chat.history;
		const messages = createMessagesList(history, history.currentId);
		const chatText = messages.reduce((a, message) => {
			return `${a}### ${message.role.toUpperCase()}\n${message.content}\n\n`;
		}, '');

		return chatText.trim();
	};

	const downloadTxt = async () => {
		const chatText = await getChatAsText();

		let blob = new Blob([chatText], {
			type: 'text/plain'
		});

		saveAs(blob, `chat-${chat.chat.title}.txt`);
	};

	const downloadPdf = async () => {
		const containerElement = document.getElementById('messages-container');

		if (containerElement) {
			try {
				const isDarkMode = document.documentElement.classList.contains('dark');

				// Define a fixed virtual screen size
				const virtualWidth = 800; // Fixed width (adjust as needed)
				// Clone the container to avoid layout shifts
				const clonedElement = containerElement.cloneNode(true);
				clonedElement.classList.add('text-black');
				clonedElement.classList.add('dark:text-white');
				clonedElement.style.width = `${virtualWidth}px`; // Apply fixed width
				clonedElement.style.height = 'auto'; // Allow content to expand

				document.body.appendChild(clonedElement); // Temporarily add to DOM

				// Render to canvas with predefined width
				const canvas = await html2canvas(clonedElement, {
					backgroundColor: isDarkMode ? '#000' : '#fff',
					useCORS: true,
					scale: 2, // Keep at 1x to avoid unexpected enlargements
					width: virtualWidth, // Set fixed virtual screen width
					windowWidth: virtualWidth // Ensure consistent rendering
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
		if (chat.id) {
			let chatObj = null;

			if (chat.id === 'local') {
				chatObj = chat;
			} else {
				chatObj = await getChatById(chat.id);
			}

			let blob = new Blob([JSON.stringify([chatObj])], {
				type: 'application/json'
			});
			saveAs(blob, `chat-export-${Date.now()}.json`);
		}
	};
</script>

<Dropdown
	onChange={(state) => {
		if (state === false) {
			onClose();
		}
	}}
>
	{@render children?.()}

	{#snippet content()}
		<DropdownMenu.Content
			class="w-full max-w-[200px] rounded-xl px-1 py-1.5 border border-line z-50 bg-surface-raised text-content shadow-sm"
			sideOffset={8}
			side="bottom"
			align="end"
			transition={flyAndScale}
		>
			<!-- llens(#19): 右ペイン(このチャットのファイル)のトグル。Navbar 直置きから移設。
			     ChatControls は !chatId で強制クローズするため、chat 未確定時は出さない -->
			{#if $chatId}
				<DropdownMenu.Item
					class="flex gap-2 items-center px-3 py-2 text-sm  cursor-pointer hover:bg-surface-hover rounded-md"
					id="chat-files-button"
					on:click={() => {
						showControls.set(!$showControls);
					}}
				>
					<span class="i-lucide-folder-open size-4 block"></span>
					<div class="flex items-center">{$i18n.t('Files in this chat')}</div>
				</DropdownMenu.Item>
			{/if}

			{#if $user?.role === 'admin' || ($user?.permissions?.chat?.share ?? true)}
				<DropdownMenu.Item
					class="flex gap-2 items-center px-3 py-2 text-sm  cursor-pointer hover:bg-surface-hover rounded-md"
					id="chat-share-button"
					on:click={() => {
						shareHandler();
					}}
				>
					<span class="i-lucide-share-2 size-4 block"></span>
					<div class="flex items-center">{$i18n.t('Share')}</div>
				</DropdownMenu.Item>
			{/if}

			<DropdownMenu.Sub>
				<DropdownMenu.SubTrigger
					class="flex gap-2 items-center px-3 py-2 text-sm  cursor-pointer hover:bg-surface-hover rounded-md"
				>
					<span class="i-lucide-download size-4 block"></span>
					<div class="flex items-center">{$i18n.t('Download')}</div>
				</DropdownMenu.SubTrigger>
				<DropdownMenu.SubContent
					class="w-full rounded-xl px-1 py-1.5 border border-line z-50 bg-surface-raised text-content shadow-sm"
					transition={flyAndScale}
					sideOffset={8}
				>
					<DropdownMenu.Item
						class="flex gap-2 items-center px-3 py-2 text-sm  cursor-pointer hover:bg-surface-hover rounded-md"
						on:click={() => {
							downloadJSONExport();
						}}
					>
						<div class="flex items-center line-clamp-1">{$i18n.t('Export chat (.json)')}</div>
					</DropdownMenu.Item>
					<DropdownMenu.Item
						class="flex gap-2 items-center px-3 py-2 text-sm  cursor-pointer hover:bg-surface-hover rounded-md"
						on:click={() => {
							downloadTxt();
						}}
					>
						<div class="flex items-center line-clamp-1">{$i18n.t('Plain text (.txt)')}</div>
					</DropdownMenu.Item>

					<DropdownMenu.Item
						class="flex gap-2 items-center px-3 py-2 text-sm  cursor-pointer hover:bg-surface-hover rounded-md"
						on:click={() => {
							downloadPdf();
						}}
					>
						<div class="flex items-center line-clamp-1">{$i18n.t('PDF document (.pdf)')}</div>
					</DropdownMenu.Item>
				</DropdownMenu.SubContent>
			</DropdownMenu.Sub>

			<DropdownMenu.Item
				class="flex gap-2 items-center px-3 py-2 text-sm  cursor-pointer hover:bg-surface-hover rounded-md"
				id="chat-copy-button"
				on:click={async () => {
					const res = await copyToClipboard(await getChatAsText()).catch((e) => {
						console.error(e);
					});

					if (res) {
						toast.success($i18n.t('Copied to clipboard'));
					}
				}}
			>
				<span class="i-lucide-clipboard size-4 block"></span>
				<div class="flex items-center">{$i18n.t('Copy')}</div>
			</DropdownMenu.Item>
		</DropdownMenu.Content>
	{/snippet}
</Dropdown>

<script lang="ts">
	import { toast } from 'svelte-sonner';

	import { onMount, getContext, createEventDispatcher } from 'svelte';

	const dispatch = createEventDispatcher();

	import { resetVectorDB, getRAGConfig, updateRAGConfig } from '$lib/apis/retrieval';

	import { reindexKnowledgeFiles } from '$lib/apis/knowledge';
	import { deleteAllFiles } from '$lib/apis/files';

	import ResetUploadDirConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import ResetVectorDBConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import ReindexKnowledgeFilesConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import Textarea from '$lib/components/common/Textarea.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n = getContext('i18n');

	let showResetConfirm = false;
	let showResetUploadDirConfirm = false;
	let showReindexConfirm = false;

	let RAGConfig = null;

	// llens(#52): 受け入れ拡張子 whitelist。UI はカンマ区切り文字列、config は配列。
	let allowedExtensions = '';

	const submitHandler = async () => {
		RAGConfig.ATTACHMENT_ALLOWED_EXTENSIONS = allowedExtensions
			.split(',')
			.map((s) => s.trim().toLowerCase().replace(/^\.+/, ''))
			.filter(Boolean);

		const res = await updateRAGConfig(localStorage.token, RAGConfig);
		dispatch('save');
	};

	onMount(async () => {
		RAGConfig = await getRAGConfig(localStorage.token);
		allowedExtensions = (RAGConfig?.ATTACHMENT_ALLOWED_EXTENSIONS ?? []).join(', ');
	});
</script>

<ResetUploadDirConfirmDialog
	bind:show={showResetUploadDirConfirm}
	on:confirm={async () => {
		const res = await deleteAllFiles(localStorage.token).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			toast.success($i18n.t('Success'));
		}
	}}
/>

<ResetVectorDBConfirmDialog
	bind:show={showResetConfirm}
	on:confirm={() => {
		const res = resetVectorDB(localStorage.token).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			toast.success($i18n.t('Success'));
		}
	}}
/>

<ReindexKnowledgeFilesConfirmDialog
	bind:show={showReindexConfirm}
	on:confirm={async () => {
		const res = await reindexKnowledgeFiles(localStorage.token).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			toast.success($i18n.t('Success'));
		}
	}}
/>

<form
	class="flex flex-col h-full justify-between space-y-3 text-sm"
	on:submit|preventDefault={() => {
		submitHandler();
	}}
>
	{#if RAGConfig}
		<div class=" space-y-2.5 overflow-y-scroll scrollbar-hidden h-full pr-1.5">
			<div class="">
				<div class="mb-3">
					<div class=" mb-2.5 text-base font-medium">{$i18n.t('Retrieval')}</div>

					<hr class=" border-gray-100 dark:border-gray-850 my-2" />

					<div class="  mb-2.5 flex flex-col w-full justify-between">
						<div class=" mb-1 text-xs font-medium">{$i18n.t('RAG Template')}</div>
						<div class="flex w-full items-center relative">
							<Tooltip
								content={$i18n.t('Leave empty to use the default prompt, or enter a custom prompt')}
								placement="top-start"
								className="w-full"
							>
								<Textarea
									bind:value={RAGConfig.RAG_TEMPLATE}
									placeholder={$i18n.t(
										'Leave empty to use the default prompt, or enter a custom prompt'
									)}
								/>
							</Tooltip>
						</div>
					</div>
				</div>

				<div class="mb-3">
					<div class=" mb-2.5 text-base font-medium">{$i18n.t('Files')}</div>

					<hr class=" border-gray-100 dark:border-gray-850 my-2" />

					<div class="  mb-2.5 flex w-full justify-between">
						<div class=" self-center text-xs font-medium">{$i18n.t('Max Upload Size')}</div>
						<div class="flex items-center relative">
							<Tooltip
								content={$i18n.t(
									'The maximum file size in MB. If the file size exceeds this limit, the file will not be uploaded.'
								)}
								placement="top-start"
							>
								<input
									class="flex-1 w-full rounded-lg text-sm bg-transparent outline-hidden"
									type="number"
									placeholder={$i18n.t('Leave empty for unlimited')}
									bind:value={RAGConfig.ATTACHMENT_MAX_SIZE}
									autocomplete="off"
									min="0"
								/>
							</Tooltip>
						</div>
					</div>

					<div class="  mb-2.5 flex w-full justify-between">
						<div class=" self-center text-xs font-medium">{$i18n.t('Max Upload Count')}</div>
						<div class="flex items-center relative">
							<Tooltip
								content={$i18n.t(
									'The maximum number of files that can be used at once in chat. If the number of files exceeds this limit, the files will not be uploaded.'
								)}
								placement="top-start"
							>
								<input
									class="flex-1 w-full rounded-lg text-sm bg-transparent outline-hidden"
									type="number"
									placeholder={$i18n.t('Leave empty for unlimited')}
									bind:value={RAGConfig.ATTACHMENT_MAX_COUNT}
									autocomplete="off"
									min="0"
								/>
							</Tooltip>
						</div>
					</div>

					<div class="  mb-2.5 flex w-full justify-between">
						<div class=" self-center text-xs font-medium">{$i18n.t('Allowed File Extensions')}</div>
						<div class="flex items-center relative">
							<Tooltip
								content={$i18n.t(
									'Comma-separated list of allowed extensions (e.g. pdf, docx, xlsx). Leave empty to allow all.'
								)}
								placement="top-start"
							>
								<input
									class="flex-1 w-full rounded-lg text-sm bg-transparent outline-hidden"
									type="text"
									placeholder={$i18n.t('Leave empty to allow all')}
									bind:value={allowedExtensions}
									autocomplete="off"
								/>
							</Tooltip>
						</div>
					</div>
				</div>

				<div class="mb-3">
					<div class=" mb-2.5 text-base font-medium">{$i18n.t('Integration')}</div>

					<hr class=" border-gray-100 dark:border-gray-850 my-2" />

					<div class="  mb-2.5 flex w-full justify-between">
						<div class=" self-center text-xs font-medium">{$i18n.t('Google Drive')}</div>
						<div class="flex items-center relative">
							<Switch bind:state={RAGConfig.ENABLE_GOOGLE_DRIVE_INTEGRATION} />
						</div>
					</div>

					<div class="  mb-2.5 flex w-full justify-between">
						<div class=" self-center text-xs font-medium">{$i18n.t('OneDrive')}</div>
						<div class="flex items-center relative">
							<Switch bind:state={RAGConfig.ENABLE_ONEDRIVE_INTEGRATION} />
						</div>
					</div>
				</div>

				<div class="mb-3">
					<div class=" mb-2.5 text-base font-medium">{$i18n.t('Danger Zone')}</div>

					<hr class=" border-gray-100 dark:border-gray-850 my-2" />

					<div class="  mb-2.5 flex w-full justify-between">
						<div class=" self-center text-xs font-medium">{$i18n.t('Reset Upload Directory')}</div>
						<div class="flex items-center relative">
							<button
								class="text-xs"
								on:click={() => {
									showResetUploadDirConfirm = true;
								}}
							>
								{$i18n.t('Reset')}
							</button>
						</div>
					</div>

					<div class="  mb-2.5 flex w-full justify-between">
						<div class=" self-center text-xs font-medium">
							{$i18n.t('Reset Vector Storage/Knowledge')}
						</div>
						<div class="flex items-center relative">
							<button
								class="text-xs"
								on:click={() => {
									showResetConfirm = true;
								}}
							>
								{$i18n.t('Reset')}
							</button>
						</div>
					</div>
					<div class="  mb-2.5 flex w-full justify-between">
						<div class=" self-center text-xs font-medium">
							{$i18n.t('Reindex Knowledge Base Vectors')}
						</div>
						<div class="flex items-center relative">
							<button
								class="text-xs"
								on:click={() => {
									showReindexConfirm = true;
								}}
							>
								{$i18n.t('Reindex')}
							</button>
						</div>
					</div>
				</div>
			</div>
		</div>
		<div class="flex justify-end pt-3 text-sm font-medium">
			<button
				class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
				type="submit"
			>
				{$i18n.t('Save')}
			</button>
		</div>
	{:else}
		<div class="flex items-center justify-center h-full">
			<Spinner />
		</div>
	{/if}
</form>

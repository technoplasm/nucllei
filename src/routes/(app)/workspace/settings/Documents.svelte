<script lang="ts">
	import { toast } from 'svelte-sonner';

	import { onMount, getContext } from 'svelte';

	import { getAttachmentConfig, setAttachmentConfig } from '$lib/apis/configs';
	import { getFunctions } from '$lib/apis/functions';

	import { deleteAllFiles } from '$lib/apis/files';

	import ResetUploadDirConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n = getContext('i18n');

	let { onSave = () => {} }: { onSave?: () => void } = $props();

	let showResetUploadDirConfirm = $state(false);

	let RAGConfig = $state<any>(null);

	// llens(#52): 受け入れ拡張子 whitelist。UI はカンマ区切り文字列、config は配列。
	let allowedExtensions = $state('');

	// llens(#69): グローバル Attachment Function(type=attachment の Function から単一選択、'' = None)。
	let attachmentFunctions = $state<any[]>([]);

	const submitHandler = async () => {
		RAGConfig.ATTACHMENT_ALLOWED_EXTENSIONS = allowedExtensions
			.split(',')
			.map((s) => s.trim().toLowerCase().replace(/^\.+/, ''))
			.filter(Boolean);

		// llens(#69): 不正な ATTACHMENT_FUNCTION_ID は backend が 400 で弾く — toast で通知。
		const res = await setAttachmentConfig(RAGConfig).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			onSave();
		}
	};

	onMount(async () => {
		attachmentFunctions = ((await getFunctions().catch(() => [])) ?? []).filter(
			(func: any) => func.type === 'attachment'
		);

		RAGConfig = await getAttachmentConfig();
		allowedExtensions = (RAGConfig?.ATTACHMENT_ALLOWED_EXTENSIONS ?? []).join(', ');
	});
</script>

<ResetUploadDirConfirmDialog
	bind:show={showResetUploadDirConfirm}
	on:confirm={async () => {
		const res = await deleteAllFiles().catch((error) => {
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
	onsubmit={(e) => {
		e.preventDefault();
		submitHandler();
	}}
>
	{#if RAGConfig}
		<div class=" space-y-2.5 overflow-y-scroll scrollbar-hidden h-full pr-1.5">
			<div class="">
				<div class="mb-3">
					<div class=" mb-2.5 text-base font-medium">{$i18n.t('Files')}</div>

					<hr class=" border-line my-2" />

					<div class="  mb-2.5 flex w-full justify-between">
						<div class=" self-center text-xs font-medium">{$i18n.t('Attachment Function')}</div>
						<div class="flex items-center relative">
							<Tooltip
								content={$i18n.t(
									'The attachment function to run on every file upload. Select None to disable.'
								)}
								placement="top-start"
							>
								<select
									class="bg-surface-raised hover:bg-surface-hover cursor-pointer w-fit px-2 py-0.5 pr-6 rounded-xs text-sm outline-hidden"
									bind:value={RAGConfig.ATTACHMENT_FUNCTION_ID}
								>
									<option value="">{$i18n.t('None')}</option>
									{#each attachmentFunctions as func}
										<option value={func.id}>{func.name}</option>
									{/each}
									{#if RAGConfig.ATTACHMENT_FUNCTION_ID && !attachmentFunctions.some((func) => func.id === RAGConfig.ATTACHMENT_FUNCTION_ID)}
										<!-- llens(#69): 設定済み id の function が消えている場合も現在値を可視化する(保存時は 400 で弾かれる) -->
										<option value={RAGConfig.ATTACHMENT_FUNCTION_ID}
											>{RAGConfig.ATTACHMENT_FUNCTION_ID}</option
										>
									{/if}
								</select>
							</Tooltip>
						</div>
					</div>

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
									class="flex-1 w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
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
									class="flex-1 w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
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
									class="flex-1 w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
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
					<div class=" mb-2.5 text-base font-medium">{$i18n.t('Danger Zone')}</div>

					<hr class=" border-line my-2" />

					<div class="  mb-2.5 flex w-full justify-between">
						<div class=" self-center text-xs font-medium">{$i18n.t('Reset Upload Directory')}</div>
						<div class="flex items-center relative">
							<button
								class="text-xs"
								type="button"
								onclick={() => {
									showResetUploadDirConfirm = true;
								}}
							>
								{$i18n.t('Reset')}
							</button>
						</div>
					</div>
				</div>
			</div>
		</div>
		<div class="flex justify-end pt-3 text-sm font-medium">
			<button
				class="px-3.5 py-1.5 text-sm font-medium bg-primary hover:bg-primary-hover text-primary-content rounded-full"
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

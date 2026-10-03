<script lang="ts">
	import fileSaver from 'file-saver';
	const { saveAs } = fileSaver;

	import { downloadDatabase } from '$lib/apis/utils';
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { getAllUserChats } from '$lib/apis/chats';
	import { exportConfig, importConfig } from '$lib/apis/configs';

	const i18n = getContext('i18n');

	const exportAllUserChats = async () => {
		let blob = new Blob([JSON.stringify(await getAllUserChats())], {
			type: 'application/json'
		});
		saveAs(blob, `all-chats-export-${Date.now()}.json`);
	};
</script>

<div class="flex flex-col h-full justify-between space-y-3 text-sm">
	<div class=" space-y-3 overflow-y-scroll h-full">
		<div>
			<div class=" mb-2 text-sm font-medium">{$i18n.t('Database')}</div>

			<input
				id="config-json-input"
				hidden
				type="file"
				accept=".json"
				onchange={(e) => {
					const inputElement = e.currentTarget;
					const file = inputElement.files?.[0];
					if (!file) {
						return;
					}
					const reader = new FileReader();

					reader.onload = async (event) => {
						const res = await importConfig(JSON.parse(event.target?.result as string)).catch(
							(error) => {
								toast.error(`${error}`);
							}
						);

						if (res) {
							toast.success('Config imported successfully');
						}
						// 旧コードは FileReader に value を設定していて input がリセットされなかった
						inputElement.value = '';
					};

					reader.readAsText(file);
				}}
			/>

			<button
				type="button"
				class=" flex rounded-md py-2 px-3 w-full hover:bg-surface-hover"
				onclick={() => {
					(document.getElementById('config-json-input') as HTMLInputElement | null)?.click();
				}}
			>
				<div class=" self-center mr-3">
					<span class="i-lucide-file-up size-4 block"></span>
				</div>
				<div class=" self-center text-sm font-medium">
					{$i18n.t('Import Config from JSON File')}
				</div>
			</button>

			<button
				type="button"
				class=" flex rounded-md py-2 px-3 w-full hover:bg-surface-hover"
				onclick={async () => {
					const config = await exportConfig();
					const blob = new Blob([JSON.stringify(config)], {
						type: 'application/json'
					});
					saveAs(blob, `config-${Date.now()}.json`);
				}}
			>
				<div class=" self-center mr-3">
					<span class="i-lucide-file-down size-4 block"></span>
				</div>
				<div class=" self-center text-sm font-medium">
					{$i18n.t('Export Config to JSON File')}
				</div>
			</button>

			<hr class="border-line my-1" />

			<div class="  flex w-full justify-between">
				<button
					class=" flex rounded-md py-1.5 px-3 w-full hover:bg-surface-hover"
					type="button"
					onclick={() => {
						downloadDatabase().catch((error) => {
							toast.error(`${error}`);
						});
					}}
				>
					<div class=" self-center mr-3">
						<span class="i-lucide-database size-4 block"></span>
					</div>
					<div class=" self-center text-sm font-medium">{$i18n.t('Download Database')}</div>
				</button>
			</div>

			<button
				class=" flex rounded-md py-2 px-3 w-full hover:bg-surface-hover"
				type="button"
				onclick={() => {
					exportAllUserChats();
				}}
			>
				<div class=" self-center mr-3">
					<span class="i-lucide-file-down size-4 block"></span>
				</div>
				<div class=" self-center text-sm font-medium">
					{$i18n.t('Export All Chats (All Users)')}
				</div>
			</button>
		</div>
	</div>
</div>

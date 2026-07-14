<script>
	import { getContext, tick } from 'svelte';
	import { goto } from '$app/navigation';

	const i18n = getContext('i18n');

	import { toast } from 'svelte-sonner';
	import CodeEditor from '$lib/components/common/CodeEditor.svelte';
	import Textarea from '$lib/components/common/Textarea.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Badge from '$lib/components/common/Badge.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import ChevronLeft from '$lib/components/icons/ChevronLeft.svelte';

	let formElement = null;
	let loading = false;
	// llens(#43 Phase2b): id リネームは「旧 id 削除 → 新 id 作成」で差し替える(PK 不変)。
	let showRenameConfirm = false;

	export let onSave = () => {};

	export let edit = false;

	export let id = '';
	export let name = '';
	// llens(#43): description は top-level フィールド(旧 meta.description)。
	export let description = '';
	export let content = '';
	// llens(#45): 編集時に既存の type をバッジ表示するため受け取る(作成時は未指定)。
	export let type = '';
	let _content = '';
	// 編集開始時の id を控え、変更があれば確認ダイアログを出す。
	let originalId = id;

	$: if (content) {
		updateContent();
	}

	const updateContent = () => {
		_content = content;
	};

	$: if (name && !edit) {
		id = name.replace(/\s+/g, '_').toLowerCase();
	}

	let codeEditor;

	// llens(#45): frontmatter(title/author/version)は退役。type はコード中のクラス名
	// (Filter/Action/Pipe)で確定するため、作成時のドロップダウンは「どの雛形を差し込むか」を
	// 選ぶだけの補助。真実源は content(クラス)側で二重定義しない。
	const filterBoilerplate = `from pydantic import BaseModel, Field
from typing import Optional


class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=0, description="Priority level for the filter operations."
        )

    def __init__(self):
        self.valves = self.Valves()

    def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        # Pre-process the request body before it reaches the model.
        return body

    def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        # Post-process the response body after the model responds.
        return body
`;

	const actionBoilerplate = `from pydantic import BaseModel, Field
from typing import Optional


class Action:
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()

    async def action(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__=None,
        __event_call__=None,
    ) -> Optional[dict]:
        # Triggered by a button under a message. Emit events or return a body.
        return body
`;

	const pipeBoilerplate = `from pydantic import BaseModel, Field
from typing import Union, Generator, Iterator


class Pipe:
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()

    def pipe(self, body: dict) -> Union[str, Generator, Iterator]:
        # Implement a custom model / pipeline. Return a string or a stream.
        return "Hello from your pipe."
`;

	// llens(#52): Attachment はアップロード時に発火し、ファイルをテキスト化するルータ。
	// このモデルに割り当てられた時だけ走り、file.data.content を埋める(単一選択)。
	const attachmentBoilerplate = `from pydantic import BaseModel, Field
from typing import Optional


class Attachment:
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()

    def process(self, file, __request__, __user__: Optional[dict] = None) -> str:
        # Fired on upload when this Attachment is assigned to the model.
        # Route by file.meta["content_type"], extract text (markdown) and
        # RETURN it. The system stores it as the file content (status=success).
        # To fail, raise with a message — the system records it as the content
        # and marks the file extraction=failed (shown to the model as-is).
        from open_webui.retrieval.extract import extract_file_text

        return extract_file_text(__request__, file)
`;

	// 作成時に選択中の type(雛形の切り替えに使う)。編集時は既存 content をそのまま出す。
	let selectedType = 'filter';
	$: boilerplate =
		selectedType === 'action'
			? actionBoilerplate
			: selectedType === 'pipe'
				? pipeBoilerplate
				: selectedType === 'attachment'
					? attachmentBoilerplate
					: filterBoilerplate;

	const saveHandler = async () => {
		loading = true;
		onSave({
			id,
			name,
			description,
			content
		});
	};

	const submitHandler = async () => {
		if (!codeEditor) return;

		content = _content;
		await tick();

		const res = await codeEditor.formatPythonCodeHandler();
		await tick();

		content = _content;
		await tick();

		if (!res) return;

		// 編集で id が変わったら、割り当て・valves がちぎれる旨を確認してから差し替える。
		if (edit && id !== originalId) {
			// id 差し替えは create 経由 = description 必須(#43 P1)。空だと create が 400 で
			// 無言失敗するため、confirm を出す前に明示的に弾く。
			if ((description ?? '').trim() === '') {
				toast.error($i18n.t('A description is required to change the ID.'));
				return;
			}
			showRenameConfirm = true;
		} else {
			saveHandler();
		}
	};
</script>

<div class=" flex flex-col justify-between w-full overflow-y-auto h-full">
	<div class="mx-auto w-full md:px-0 h-full">
		<form
			bind:this={formElement}
			class=" flex flex-col max-h-[100dvh] h-full"
			on:submit|preventDefault={() => {
				submitHandler();
			}}
		>
			<div class="flex flex-col flex-1 overflow-auto h-0 rounded-lg">
				<!-- llens(#45): 作成/編集画面を skill / tool 型のセクション構成に統一 -->
				<div class="w-full mb-2 flex flex-col gap-1">
					<div class="flex w-full items-center">
						<div class=" shrink-0 mr-2">
							<Tooltip content={$i18n.t('Back')}>
								<button
									class="w-full text-left text-sm py-1.5 px-1 rounded-lg text-content dark:hover:text-white hover:bg-black/5 dark:hover:bg-gray-850"
									on:click={() => {
										goto('/workspace/functions');
									}}
									type="button"
								>
									<ChevronLeft strokeWidth="2.5" />
								</button>
							</Tooltip>
						</div>

						<div class="flex-1">
							<input
								class="w-full text-2xl font-semibold bg-transparent outline-hidden font-primary"
								type="text"
								placeholder={$i18n.t('Function Name')}
								bind:value={name}
								required
							/>
						</div>

						<div class="self-center shrink-0">
							{#if edit}
								<Badge type="muted" content={type || $i18n.t('Function')} />
							{:else}
								<!-- llens(#45): 作成時のみ type を選ぶ(雛形の差し替え)。filter が既定。 -->
								<select
									class="capitalize rounded-lg text-sm bg-gray-50 dark:bg-gray-850 dark:text-white outline-hidden px-3 py-1.5"
									bind:value={selectedType}
									aria-label={$i18n.t('Function Type')}
								>
									<option value="filter">Filter</option>
									<option value="attachment">Attachment</option>
									<option value="action">Action</option>
									<option value="pipe">Pipe</option>
								</select>
							{/if}
						</div>
					</div>

					<div class="flex gap-0.5 items-center text-xs text-content-muted px-1">
						<input
							class="w-full bg-transparent outline-hidden"
							type="text"
							placeholder={$i18n.t('Function ID')}
							bind:value={id}
							required
						/>
					</div>
				</div>

				<div class="my-2">
					<div class=" self-center text-sm font-semibold mb-1">{$i18n.t('Description')}</div>
					<Textarea
						className="text-sm w-full bg-transparent outline-hidden overflow-y-hidden resize-none"
						placeholder={$i18n.t('e.g. A filter to remove profanity from text')}
						bind:value={description}
						rows={2}
					/>
				</div>

				<div class=" self-start text-sm font-semibold px-1 mb-1">{$i18n.t('Function Content')}</div>
				<div class="mb-2 flex-1 overflow-auto h-0 rounded-lg">
					{#key edit ? 'edit' : selectedType}
						<CodeEditor
							bind:this={codeEditor}
							value={content}
							lang="python"
							{boilerplate}
							onChange={(e) => {
								_content = e;
							}}
							onSave={async () => {
								if (formElement) {
									formElement.requestSubmit();
								}
							}}
						/>
					{/key}
				</div>

				<div class="pb-3 flex justify-start">
					<button
						class="px-4 py-2 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-lg"
						type="submit"
					>
						{$i18n.t('Save')}
					</button>
				</div>
			</div>
		</form>
	</div>
</div>

<ConfirmDialog
	bind:show={showRenameConfirm}
	title={$i18n.t('Change ID?')}
	on:confirm={() => {
		saveHandler();
	}}
>
	<div class="text-sm text-content-muted">
		<div class=" bg-yellow-500/20 text-yellow-700 dark:text-yellow-200 rounded-lg px-4 py-3">
			<div>
				{$i18n.t('The ID will be changed by deleting the old one and creating a new one.')}
			</div>
			<ul class=" mt-1 list-disc pl-4 text-xs">
				<li>{$i18n.t('Assignments and references from other items will be detached.')}</li>
				<li>{$i18n.t('Per-item settings such as valves will be reset.')}</li>
			</ul>
		</div>
	</div>
</ConfirmDialog>

<script lang="ts">
	import { getContext, tick } from 'svelte';

	import CodeEditor from '$lib/components/common/CodeEditor.svelte';
	import Textarea from '$lib/components/common/Textarea.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Badge from '$lib/components/common/Badge.svelte';

	const i18n = getContext('i18n');

	let {
		edit = false,
		onSave = () => {},
		id = $bindable(''),
		name = $bindable(''),
		// llens(#43): description は top-level フィールド(旧 meta.description)。空でも可(UI メタデータ)。
		description = $bindable(''),
		content = $bindable(''),
		// llens(#45): 編集時に既存の type をバッジ表示するため受け取る(作成時は未指定)。
		type = ''
	}: {
		edit?: boolean;
		onSave?: (data: any) => void;
		id?: string;
		name?: string;
		description?: string;
		content?: string;
		type?: string;
	} = $props();

	let formElement = $state<HTMLFormElement | null>(null);
	let codeEditor = $state<any>(null);

	// llens(#43 Phase2b): id リネームは「旧 id 削除 → 新 id 作成」で差し替える(PK 不変)。
	// 編集開始時の id を控え、変更があれば確認ダイアログを出す。
	let originalId = id;
	let showRenameConfirm = $state(false);

	let _content = $state('');

	$effect(() => {
		if (content) {
			_content = content;
		}
	});

	// 作成時は表示名から id を自動生成する
	$effect(() => {
		if (name && !edit) {
			id = name.replace(/\s+/g, '_').toLowerCase();
		}
	});

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
	let selectedType = $state('filter');
	const boilerplate = $derived(
		selectedType === 'action'
			? actionBoilerplate
			: selectedType === 'pipe'
				? pipeBoilerplate
				: selectedType === 'attachment'
					? attachmentBoilerplate
					: filterBoilerplate
	);

	const saveHandler = async () => {
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
			showRenameConfirm = true;
		} else {
			saveHandler();
		}
	};
</script>

<!-- 高さは workspace layout(.max-w-5xl の h-full)から一段で貼る。
     col 連打(h-0 / max-h-[100dvh] の入れ子)はやめ、flex-col は form の一段だけ。
     エディタ枠が flex-1 + min-h-0 で残り全高を吸収する。 -->
<form
	bind:this={formElement}
	class="flex flex-col h-full"
	onsubmit={(e) => {
		e.preventDefault();
		submitHandler();
	}}
>
	<div>
		<!-- llens(#45): 作成/編集画面を skill / tool 型のセクション構成に統一。
		     ラベルは固定幅(w-24)でコントロールと同一行 — エディタの縦を稼ぐ。 -->
		<a
			class="inline-flex items-center gap-1 text-sm py-0.5 pl-2 pr-4 bg-secondary hover:bg-secondary-hover text-secondary-content rounded-lg transition"
			href="/workspace/functions"
		>
			<span class="i-lucide-chevron-left size-4 block"></span>
			{$i18n.t('Back to list')}
		</a>

		<div class="flex items-center gap-2 my-2">
			<label class="w-24 shrink-0 text-sm font-semibold" for="function-name">
				{$i18n.t('Name')}
			</label>
			<input
				id="function-name"
				class="flex-1 min-w-0 text-sm py-1 px-2 bg-surface-raised outline-hidden font-primary"
				type="text"
				placeholder={$i18n.t('Function Name')}
				bind:value={name}
				required
			/>

			{#if edit}
				<Badge type="muted" content={type || $i18n.t('Function')} />
			{:else}
				<!-- llens(#45): 作成時のみ type を選ぶ(雛形の差し替え)。filter が既定。 -->
				<select
					class="shrink-0 capitalize rounded-lg text-sm bg-surface-raised text-content outline-hidden px-3 py-1.5"
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

		<div class="flex items-center gap-2 my-2">
			<label class="w-24 shrink-0 text-sm font-semibold" for="function-id">
				{$i18n.t('ID')}
			</label>
			<input
				id="function-id"
				class="flex-1 min-w-0 px-2 py-1 text-sm bg-surface-raised outline-hidden"
				type="text"
				placeholder={$i18n.t('Function ID')}
				bind:value={id}
				required
			/>
		</div>

		<div class="flex items-start gap-2 my-2">
			<label class="w-24 shrink-0 text-sm font-semibold pt-1" for="function-description">
				{$i18n.t('Description')}
			</label>
			<Textarea
				id="function-description"
				className="flex-1 min-w-0 px-2 py-1 text-sm bg-surface-raised outline-hidden overflow-y-hidden resize-none"
				placeholder={$i18n.t('e.g. A filter to remove profanity from text')}
				bind:value={description}
				rows={2}
			/>
		</div>

		<div class="text-sm font-semibold px-1 mb-1">{$i18n.t('Function Content')}</div>
	</div>

	<div class="flex-1 min-h-0 mb-2 rounded-lg overflow-auto bg-white">
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
			class="px-4 py-2 text-sm font-medium bg-primary hover:bg-primary-hover text-primary-content transition rounded-lg"
			type="submit"
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>

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

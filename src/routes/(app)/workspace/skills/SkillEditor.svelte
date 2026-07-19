<script lang="ts">
	import { onMount, tick, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { user } from '$lib/stores';
	import { getTools } from '$lib/apis/tools';

	import Textarea from '$lib/components/common/Textarea.svelte';
	import CodeEditor from '$lib/components/common/CodeEditor.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import AccessControl from '$lib/components/workspace/common/AccessControl.svelte';

	const i18n = getContext('i18n');

	let {
		onSubmit,
		edit = false,
		skill = null
	}: {
		onSubmit: (skill: any) => void | Promise<void>;
		edit?: boolean;
		skill?: any;
	} = $props();

	let loading = $state(false);

	// llens(#43): 可読キーは id 一本(旧 command)、表示名は name(旧 title)。
	let name = $state('');
	let id = $state('');
	let description = $state('');
	// 本文は CodeEditor で編集。value は _content にミラーし、submit 時に content へ確定。
	let content = $state('');
	let _content = $state('');

	// llens(#43 Phase2b): id リネームは「旧 id 削除 → 新 id 作成」で差し替える(PK 不変)。
	// 編集開始時の id を控え、変更があれば確認ダイアログを出す。
	let originalId = '';
	let showRenameConfirm = $state(false);

	// tool_ids は admin のみ編集可(バックエンドが 401 を返すゲートと整合)。
	let tools = $state<any[]>([]);
	let _tools = $state<Record<string, any>>({});
	let toolIds = $state<string[]>([]);

	let accessControl = $state<any>({});

	// llens(#66): 編集できるのは自分の skill だけ(admin は全部)。他人の skill は閲覧のみ。
	// ACL(公開範囲)を触れるのは admin のみ — editor の保存では server 側でも温存される。
	const canEdit = $derived(!edit || $user?.role === 'admin' || skill?.user_id === $user?.id);

	// 作成時は表示名から id を自動生成する
	$effect(() => {
		if (!edit) {
			id = name !== '' ? `${name.replace(/\s+/g, '-').toLowerCase()}` : '';
		}
	});

	const submitHandler = async () => {
		content = _content;

		if (!validateCommandString(id)) {
			toast.error(
				$i18n.t('Only alphanumeric characters and hyphens are allowed in the command string.')
			);
			return;
		}

		if (content.trim() === '') {
			toast.error($i18n.t('Skill Content is required'));
			return;
		}

		// 編集で id が変わったら、割り当て・valves がちぎれる旨を確認してから差し替える。
		if (edit && id !== originalId) {
			showRenameConfirm = true;
			return;
		}

		await doSubmit();
	};

	const doSubmit = async () => {
		loading = true;
		await onSubmit({
			name,
			id,
			// description が空ならモデルには公開されない(バックエンド仕様)。
			description: description,
			content,
			tool_ids: toolIds,
			access_control: accessControl
		});
		loading = false;
	};

	const validateCommandString = (inputString: string) => {
		// 英数字とハイフンのみ許可(PromptEditor 踏襲)
		return /^[a-zA-Z0-9-]+$/.test(inputString);
	};

	onMount(async () => {
		if (skill) {
			name = skill.name;
			await tick();

			id = skill.id.at(0) === '/' ? skill.id.slice(1) : skill.id;
			originalId = id;
			description = skill.description ?? '';
			content = skill.content;
			_content = content;
			toolIds = skill?.tool_ids ?? [];

			accessControl = skill?.access_control ?? null;
		}

		// tool 選択肢は admin のときのみ取得・描画(非 admin には出さない)
		if ($user?.role === 'admin') {
			tools = (await getTools().catch(() => [])) ?? [];
			_tools = tools.reduce((acc: Record<string, any>, tool) => {
				acc[tool.id] = {
					...tool,
					selected: toolIds.includes(tool.id)
				};
				return acc;
			}, {});
		}
	});
</script>

<!-- 高さは workspace layout(.max-w-5xl の h-full)から一段で貼る(FunctionEditor と同型)。 -->
<form
	class="flex flex-col h-full"
	onsubmit={(e) => {
		e.preventDefault();
		submitHandler();
	}}
>
	<div>
		<!-- ラベルは固定幅(w-24)でコントロールと同一行 — エディタの縦を稼ぐ。 -->
		<a
			class="inline-flex items-center text-sm py-0.5 text-content-subtle hover:text-content font-medium"
			href="/workspace/skills"
		>
			<span class="i-lucide-arrow-left size-4 mr-2"></span>
			{$i18n.t('Back to list')}
		</a>

		<div class="flex items-center gap-2 my-2">
			<label class="w-24 shrink-0 text-sm font-semibold" for="skill-name">
				{$i18n.t('Name')}
			</label>
			<input
				id="skill-name"
				class="flex-1 min-w-0 text-sm py-1 px-2 bg-surface-form rounded-xs outline-hidden font-primary"
				placeholder={$i18n.t('Title')}
				bind:value={name}
				required
			/>
		</div>

		<div class="flex items-center gap-2 my-2">
			<label class="w-24 shrink-0 text-sm font-semibold" for="skill-id">
				{$i18n.t('ID')}
			</label>
			<Tooltip
				className="flex-1 min-w-0"
				content={`${$i18n.t('Only alphanumeric characters and hyphens are allowed')} - ${$i18n.t(
					'Activate this command by typing "/{{COMMAND}}" to chat input.',
					{
						COMMAND: id
					}
				)}`}
				placement="bottom-start"
			>
				<div class="flex items-center px-2 bg-surface-form rounded-xs">
					<span class="text-content-muted">/</span>
					<input
						id="skill-id"
						class="w-full min-w-0 px-1 py-1 text-sm bg-transparent outline-hidden"
						placeholder={$i18n.t('Command')}
						bind:value={id}
						required
					/>
				</div>
			</Tooltip>
		</div>

		<div class="flex items-start gap-2 my-2">
			<label class="w-24 shrink-0 text-sm font-semibold pt-1" for="skill-description">
				{$i18n.t('Description')}
			</label>
			<Textarea
				id="skill-description"
				className="flex-1 min-w-0 px-2 py-1 text-sm bg-surface-form rounded-xs outline-hidden overflow-y-hidden resize-none"
				placeholder={$i18n.t(
					'Description the model uses to pick this skill. If empty, it is not exposed to the model'
				)}
				bind:value={description}
				rows={2}
			/>
		</div>

		{#if $user?.role === 'admin'}
			<div class="flex items-start gap-2 my-2">
				<div class="w-24 shrink-0 text-sm font-semibold pt-0.5">{$i18n.t('Tools')}</div>
				<div class="flex-1 min-w-0">
					{#if tools.length > 0}
						<div class="flex items-center flex-wrap gap-x-3 gap-y-1">
							{#each Object.keys(_tools) as tool (tool)}
								<!-- label 包みで名前クリックでも toggle(#49 と同じ流儀。Checkbox の
								 button は labelable なので for/id 不要でクリックが転送される) -->
								<label class="flex items-center gap-2 cursor-pointer">
									<Checkbox
										state={_tools[tool].selected ? 'checked' : 'unchecked'}
										onChange={(state) => {
											_tools[tool].selected = state === 'checked';
											toolIds = Object.keys(_tools).filter((t) => _tools[t].selected);
										}}
									/>
									<span class="py-0.5 text-sm capitalize font-medium select-none">
										{_tools[tool].name}
									</span>
								</label>
							{/each}
						</div>
					{/if}
					<div class="text-xs text-content-muted mt-0.5">
						{$i18n.t('Only admins can create or update skills with tools attached.')}
					</div>
				</div>
			</div>

			<!-- llens(#85): ACL はモーダルから行インラインへ(公開状態が開かずに見える)。触れるのは admin のみ -->
			<div class="flex items-start gap-2 my-2">
				<div class="w-24 shrink-0 text-sm font-semibold pt-1">{$i18n.t('Visibility')}</div>
				<div class="flex-1 min-w-0">
					<AccessControl bind:accessControl allowPublic={true} showLabel={false} />
				</div>
			</div>
		{/if}

		<div class="text-sm font-semibold px-1 mb-1">{$i18n.t('Skill Content')}</div>
	</div>

	<div class="flex-1 min-h-0 rounded-lg border border-line overflow-auto">
		<CodeEditor
			id="skill"
			lang="markdown"
			value={content}
			onChange={(e) => {
				_content = e;
			}}
			onSave={() => {
				submitHandler();
			}}
		/>
	</div>

	<div class="text-xs text-content-subtle mt-1 mb-1">
		ⓘ {$i18n.t('Format your variables using brackets like this:')}&nbsp;<span
			class=" text-content font-medium">{'{{'}{$i18n.t('variable')}{'}}'}</span
		>.
		{$i18n.t('Make sure to enclose them with')}
		<span class=" text-content font-medium">{'{{'}</span>
		{$i18n.t('and')}
		<span class=" text-content font-medium">{'}}'}</span>.
	</div>

	<div class="pb-3 flex justify-start items-center gap-3">
		<button
			class="px-4 py-2 text-sm font-medium bg-primary hover:bg-primary-hover text-primary-content rounded-lg {loading ||
			!canEdit
				? ' cursor-not-allowed opacity-50'
				: ''} flex items-center"
			type="submit"
			disabled={loading || !canEdit}
		>
			{$i18n.t('Save')}

			{#if loading}
				<span class="i-lucide-loader-circle size-4 block animate-spin ml-1.5"></span>
			{/if}
		</button>

		{#if !canEdit}
			<div class="text-sm text-content-muted">
				{$i18n.t('Read-only (you can only edit your own skills)')}
			</div>
		{/if}
	</div>
</form>

<ConfirmDialog
	bind:show={showRenameConfirm}
	title={$i18n.t('Change ID?')}
	onConfirm={() => {
		doSubmit();
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

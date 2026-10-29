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

	// 自動ロード条件: content_type の前方一致をカンマ区切りで編集する(例 "audio/, image/png")。
	// 正規化(小文字化・空要素と重複の除去)はバックエンドが行う。
	let autoLoadContentTypes = $state('');

	// llens#22: on_load(読み込み時に書いた順に処理する手順)は JSON の配列で編集する。
	// load_skill の要素(別の skill を一緒に読み込む)だけなら誰でも書けるが、それ以外の tool
	// (サーバが実行する)を含むものは admin のみ(バックエンドの admin ゲートと整合)。
	// 非 admin が開いた skill に tool の要素が入っていれば欄は読み取り専用で、保存では
	// 送らない(= 既存値を保つ。その skill はバックエンドが非 admin の更新を断る)。
	let onLoadText = $state('');
	let onLoadLocked = $state(false);

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

		if (!onLoadLocked) {
			const parsed = parseOnLoad(onLoadText);
			if (parsed === null) {
				toast.error($i18n.t('On load must be a JSON array.'));
				return;
			}
			if ($user?.role !== 'admin' && hasToolEntries(parsed)) {
				toast.error(
					$i18n.t('Only admins can set tools other than load_skill and load_recipe in On load.')
				);
				return;
			}
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
			auto_load_content_types: autoLoadContentTypes.split(',').map((type) => type.trim()),
			...(onLoadLocked ? {} : { on_load: parseOnLoad(onLoadText) }),
			access_control: accessControl
		});
		loading = false;
	};

	// load_skill・load_recipe 以外の tool(サーバが実行する)の要素があるか — あれば admin のみ。
	// バックエンド(models/skills.py の on_load_tool_entries)と同じ規則で、短い記法の
	// `{"skill": …}` `{"recipe": …}` も読み込みとして数える(`{"read": …}` は read_file = tool)
	const LOADING_TOOLS = ['load_skill', 'load_recipe'];
	const LOADING_SHORT_KEYS = ['skill', 'recipe'];
	const isLoadingEntry = (entry: unknown) => {
		if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return false;
		const keys = Object.keys(entry);
		if (keys.length === 1 && LOADING_SHORT_KEYS.includes(keys[0])) return true;
		return LOADING_TOOLS.includes((entry as { tool?: unknown }).tool as string);
	};
	const hasToolEntries = (entries: unknown[]) => entries.some((entry) => !isLoadingEntry(entry));

	// 空欄は [](on_load 無し)。配列でない・JSON でないものは null(保存させない)。
	// 要素の形(tool・args)の検証はバックエンドが行い、400 の detail を toast に出す
	const parseOnLoad = (text: string): unknown[] | null => {
		if (text.trim() === '') return [];
		try {
			const value = JSON.parse(text);
			return Array.isArray(value) ? value : null;
		} catch {
			return null;
		}
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
			autoLoadContentTypes = (skill?.auto_load_content_types ?? []).join(', ');
			onLoadText = (skill?.on_load ?? []).length ? JSON.stringify(skill.on_load, null, 2) : '';
			onLoadLocked = $user?.role !== 'admin' && hasToolEntries(skill?.on_load ?? []);

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

		<div class="flex items-start gap-2 my-2">
			<label class="w-24 shrink-0 text-sm font-semibold pt-1" for="skill-auto-load">
				{$i18n.t('Auto-load')}
			</label>
			<div class="flex-1 min-w-0">
				<input
					id="skill-auto-load"
					class="w-full text-sm py-1 px-2 bg-surface-form rounded-xs outline-hidden"
					placeholder="audio/, image/png"
					bind:value={autoLoadContentTypes}
				/>
				<div class="text-xs text-content-muted mt-0.5">
					{$i18n.t(
						'Loads this skill automatically when a chat has an attachment whose content type starts with one of these (comma-separated). Leave empty to disable.'
					)}
				</div>
			</div>
		</div>

		<div class="flex items-start gap-2 my-2">
			<label class="w-24 shrink-0 text-sm font-semibold pt-1" for="skill-on-load">
				{$i18n.t('On load')}
			</label>
			<div class="flex-1 min-w-0">
				{#if onLoadLocked}
					<pre
						id="skill-on-load"
						class="w-full px-2 py-1 text-sm font-mono bg-surface-form rounded-xs whitespace-pre-wrap break-all text-content-muted">{onLoadText}</pre>
					<div class="text-xs text-content-muted mt-0.5">
						{$i18n.t(
							'Only admins can change this, because it runs tools other than load_skill and load_recipe.'
						)}
					</div>
				{:else}
					<Textarea
						id="skill-on-load"
						className="w-full px-2 py-1 text-sm font-mono bg-surface-form rounded-xs outline-hidden overflow-y-hidden resize-none"
						placeholder={'[{"tool": "load_skill", "args": {"skill_id": "recipes"}}]'}
						bind:value={onLoadText}
						rows={2}
					/>
					<div class="text-xs text-content-muted mt-0.5">
						{$i18n.t(
							'Steps run in order when this skill is loaded (JSON array). load_skill loads another skill and load_recipe loads a recipe together with this one; any other tool is run by the server once per chat with these fixed arguments, and its result is added to the chat. Only admins can set tools other than load_skill and load_recipe.'
						)}
					</div>
				{/if}
			</div>
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

	<!-- 縦が狭い画面ではエディタを潰さず(min-h-96)ページスクロールに逃がす(親 #workspace-container が overflow-y-auto) -->
	<div class="flex-1 min-h-96 rounded-lg border border-line overflow-auto">
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
				<li>{$i18n.t('Per-item settings such as values will be reset.')}</li>
			</ul>
		</div>
	</div>
</ConfirmDialog>

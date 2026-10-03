<script lang="ts">
	// tool call 1 件の表示(content_blocks の tool_calls ブロック配下、#17/#20)。
	// 旧・汎用 Collapsible の attributes 経由レンダリングから独立させた専用コンポーネント。
	//
	// レイアウト: ヘッダ(Tool の使用 NAME)は開くと枠の上辺 tab に合体し、
	// Arguments と Result は同じ 1 枚の枠の中の区画(Result は内部罫線 + tab)。
	// 引数の無い tool は Arguments 区画にグレーの「引数なし」を出す。
	// 閉じるハンドルは枠の下辺・左側、すべて閉じる(チャット内一括)は右側。
	// ヘッダ・Result tab・閉じるハンドルのどれをクリックしても閉じられる。
	// tab は上側だけ rounded・下辺なしで、-mb-px/-mt-px で枠線に重ねて一体化する。
	import { getContext, untrack } from 'svelte';
	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';

	import { settings } from '$lib/stores';
	import {
		classifyToolResult,
		formatJSONString,
		parseToolArgumentsStreaming,
		type ToolCallRender
	} from '$lib/utils/tool-render';

	import { formatDurationSeconds } from '$lib/utils/duration';
	import { subscribeTick, tickNow } from '$lib/utils/tick.svelte';
	import type {
		SubagentPhase,
		SubagentProgress,
		ToolProgress
	} from '$lib/stores/tool-progress.svelte';
	import { getExecutionEntry, refreshExecutionsUntilTerminal } from '$lib/stores/executions.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import HljsCode from '$lib/components/common/HljsCode.svelte';
	import Markdown from './Markdown.svelte';
	import SubagentSessionBlock from './SubagentSessionBlock.svelte';
	import ExecutionBlock from './ExecutionBlock.svelte';

	const i18n = getContext('i18n');

	type ToolCall = {
		id: string;
		function?: { name?: string; arguments?: string };
		render?: ToolCallRender;
	};
	type DisplayItem = { kind: string; content: string };
	type ToolResult = {
		tool_call_id: string;
		content?: string;
		render?: { kind: string };
		// display チャネル(#88): tool が __display__ で明示的にユーザーへ見せる表示。
		// 戻り値(モデル向け)とは別面で collapse 外に常時出す。
		display?: DisplayItem[];
		// 所要時間(#100、秒)。成否に関わらず載る
		duration?: number;
	};

	let {
		id = '',
		call,
		result = null,
		// メッセージ確定後(中断含む)は result が無くてもスピナーを止める保険
		done = false,
		// 実行中の進捗(#100、揮発)。tool が __progress__ で押した「今どこか」。
		// 完了の権威は result の到着なので、result があればこれは無視する。
		progress = null,
		// チャット内一括開閉(collapse-all.svelte.ts が採番)。seq が進んだら open に追従する
		expandAll = null,
		onExpandAll = undefined
	}: {
		id?: string;
		call: ToolCall;
		result?: ToolResult | null;
		done?: boolean;
		progress?: ToolProgress | null;
		expandAll?: { seq: number; open: boolean } | null;
		onExpandAll?: (open: boolean) => void;
	} = $props();

	// tool call の自動展開(実行中だけ開く #20 ステップ5)は OFF —
	// reasoning と同じく expandDetails 設定を初期値に、手動の開閉を維持する。
	let open = $state($settings?.expandDetails ?? false);
	let rootElement: HTMLElement | null = $state(null);
	// 開閉コントロール(バッジ/tab)hover 中は開閉単位のボーダー全部を強調する
	let toggleHover = $state(false);

	// 初期値キャプチャは意図(seq 比較の lastState ガード — 変化時のみ $effect で追従)
	// svelte-ignore state_referenced_locally
	let lastExpandSeq = expandAll?.seq ?? 0;
	$effect(() => {
		if (expandAll && expandAll.seq !== lastExpandSeq) {
			lastExpandSeq = expandAll.seq;
			open = expandAll.open;
		}
	});

	const name = $derived(call?.function?.name ?? '');
	const args = $derived(call?.function?.arguments ?? '');
	const isDone = $derived(Boolean(result) || done);

	// 進捗(#100)は実行中だけ。tool が done を撃たずに return しても、timeout /
	// 例外で tool のコードを通らなくても、結果の到着でここが止まる。
	const activeProgress = $derived(isDone ? null : progress);
	// 決定的(current/total)なら分数を添える。backend が正規化済みなので
	// ここで「total があるか」以上の判断はしない。
	const progressLabel = $derived.by(() => {
		if (!activeProgress) return '';
		const { description, current, total } = activeProgress;
		const fraction = total != null && current != null ? `${current}/${total}` : '';
		return [description, fraction].filter(Boolean).join(' ');
	});

	const durationLabel = $derived(
		result?.duration != null
			? $i18n.t('{{DURATION}} seconds', { DURATION: formatDurationSeconds(result.duration) })
			: ''
	);

	// 実行中の経過時間。起点は backend が call 開始時に打つ progress の受信時刻
	// (ブロックは実行前の 1 回の emit で一斉に描かれるので、マウント時刻を起点に
	// すると 2 本目以降が前の tool の待ち時間まで数えてしまう)。
	// 終点は終端 progress(done)の受信時刻 — results はラウンド単位で届くので、
	// これが無いと先に終わった tool が後続を待つ間ずっと回り続けて巻き戻る。
	// 依存は **真偽値だけ**にする(progress オブジェクトを依存に置くと更新のたびに
	// 購読を張り直し、タイマが毎回作り直される偶然依存になる)。
	const ticking = $derived(!isDone && progress != null && progress.doneAt == null);
	$effect(() => {
		if (!ticking) return;
		return subscribeTick();
	});
	const elapsedLabel = $derived.by(() => {
		if (isDone || !progress) return '';
		const until = progress.doneAt ?? tickNow();
		const seconds = Math.max(0, Math.round((until - progress.startedAt) / 1000));
		return $i18n.t('{{DURATION}} seconds', { DURATION: seconds });
	});

	// 引数はディスクリプタ(call.render.args、#20)どおりに引数単位で描画。
	// ストリーミング中の未閉鎖 JSON は寛容パーサで部分値を逐次描画し、
	// object 以外の壊れ方のみ一枚岩 JSON にフォールバック(argDisplays === null)。
	const argDisplays = $derived(parseToolArgumentsStreaming(args, call?.render?.args));
	const hasArgs = $derived(argDisplays === null || argDisplays.length > 0);

	// 結果の表示種別: 戻り型宣言(call.render.result)→ backend が実行時の実型から
	// 同梱した result.render(dict/list → json)→ text(中身からの動的解釈はしない)
	const resultDisplay = $derived(
		result ? classifyToolResult(result.content ?? '', call?.render?.result ?? result.render) : null
	);

	// #110: background 昇格された呼び出し。spawn は result(running 形)が即存在するため
	// isDone が緑チェックを出してしまう — exec 行が running の間はスピナーに上書きする。
	// store に行が無い間(未 fetch)も running 扱い(result の running 形が最後の真実)
	const executionEntry = $derived(
		resultDisplay?.kind === 'execution' ? getExecutionEntry(resultDisplay.execId) : null
	);
	const execRunning = $derived(
		resultDisplay?.kind === 'execution' && (executionEntry?.status ?? 'running') === 'running'
	);

	// #140 UI 追従: run_subagent の子 agent session の所在(progress の `subagent`)。
	// 実行中(結果がまだ無い sync / spawn 即返しの background)でも子 transcript を
	// カード内に live 追従で描く。progress は揮発(メッセージ確定で store から消える)
	// ので、一度観測した所在はこのブロックが持ち続ける。
	let liveSubagent = $state<SubagentProgress | null>(null);
	$effect(() => {
		const observed = progress?.subagent;
		if (!observed?.session_id) return;
		untrack(() => {
			if (
				liveSubagent?.session_id !== observed.session_id ||
				liveSubagent?.task_id !== observed.task_id
			) {
				liveSubagent = { ...observed };
			}
		});
	});
	// 子ブロックが返す進み具合(ヘッダのラベル・自動展開の畳み戻し)
	let childPhase = $state<SubagentPhase | null>(null);

	// tool として「まだ終わっていない」: 結果未着(sync)か、exec 行が running(background)
	const toolRunning = $derived(!isDone || execRunning);

	// 子 session を描くか(結果の subagent 形 / live の所在)。同じ位置に 1 個だけ置く —
	// live → 結果到着で同じインスタンスが続き、二重に描かない
	// background の所在は progress(揮発)のほか exec 行にも永続化されている — リロード後
	// (live の所在が無い)はこちらで子 session へ再リンクする
	const subagentSessionId = $derived(
		resultDisplay?.kind === 'subagent'
			? resultDisplay.sessionId
			: (liveSubagent?.session_id ?? executionEntry?.session_id ?? null)
	);
	const subagentDisplay = $derived(resultDisplay?.kind === 'subagent' ? resultDisplay : null);

	// 実行中ヘッダのラベル: 素の `agent session <uuid>` ではなく、子のイベントから
	// 「子を起動しました → 考え中 → 回答中」
	// 子ブロックは {#if open} の内側 — 畳むと unmount されて phase が届かなくなる
	// (畳んだ時点のラベルで固まる)。畳んでいる間は段階を言わず「実行中」に倒す
	const subagentLabel = $derived.by(() => {
		if (!liveSubagent || !toolRunning) return '';
		if (!open) return $i18n.t('Running...');
		const phase = childPhase ?? 'started';
		if (phase === 'answering') return $i18n.t('Subagent answering');
		if (phase === 'thinking') return $i18n.t('Subagent thinking');
		if (phase === 'started') return $i18n.t('Subagent started');
		return '';
	});

	// 子が走っている間はカードを自動で開き、終わったらユーザーの既定(expandDetails)へ
	// 戻す。途中でユーザーが手で開閉したらそれを尊重する(以後触らない)
	let autoOpened = false;
	let autoExpandConsumed = false;
	$effect(() => {
		if (!liveSubagent || !toolRunning || childPhase === 'done') return;
		untrack(() => {
			if (autoExpandConsumed) return;
			autoExpandConsumed = true;
			if (!open) {
				open = true;
				autoOpened = true;
			}
		});
	});
	$effect(() => {
		const finished = !toolRunning || childPhase === 'done';
		if (!finished) return;
		untrack(() => {
			if (!autoOpened) return;
			autoOpened = false;
			open = $settings?.expandDetails ?? false;
		});
	});

	const onChildPhase = (phase: SubagentPhase) => {
		childPhase = phase;
		// background: 子の完走を live 追従で観測したら exec 行を即読み直す
		// (`execution:done` の push が届かない — 親の stream が閉じている — 場合の fallback)
		if (phase === 'done' && resultDisplay?.kind === 'execution' && execRunning) {
			void refreshExecutionsUntilTerminal(resultDisplay.execId);
		}
	};

	// display チャネル(#88): tool が __display__ で押した人間向け表示。collapse の
	// 開閉と無関係に常時見せる — open 時は枠の底に、閉時は badge から下がる専用枠に
	// 描き、collapsible ブロックと 1 つの塊として「くっついて」見えるようにする。
	const displayItems = $derived(Array.isArray(result?.display) ? result.display : []);
	const hasDisplay = $derived(displayItems.length > 0);

	// llens(#17): 長い展開を閉じたあとの視点合わせ。slide の outro 完了
	// (= ジオメトリ確定)後に scrollIntoView する — rAF 1 回では 300ms の収縮中に
	// 測ってしまい、ビューポートより背の高い展開では 'nearest' が no-op になって、
	// 収縮後の視界が無関係な位置に取り残される。
	// フラグ方式なのは「すべて閉じる」の巻き添えで閉じる他ブロックまで
	// スクロールを取り合わないため(合図を出した 1 ブロックだけが復帰する)。
	let scrollOnClose = false;
	const collapseFromBottom = () => {
		scrollOnClose = true;
		open = false;
		autoOpened = false;
	};
	const restoreViewAfterCollapse = () => {
		if (!scrollOnClose) return;
		scrollOnClose = false;
		rootElement?.scrollIntoView({ block: 'nearest' });
	};
</script>

{#snippet displayBody(separated: boolean)}
	<!--
		display チャネル(#88): tool が __display__(content) で押した人間向け表示。
		戻り値=モデル向けとは別のユーザー面。open 枠の底(separated=上罫で区切る)にも
		閉時の専用枠にも同じ本体を出し、collapsible ブロックと 1 つの塊に見せる。
		XSS 不変条件: 外部入力(tool 出力)なので Markdown(MarkdownTokens 内で
		DOMPurify.sanitize)経由で描く — 本文・skill 結果と同じサニタイズ経路。
		kind は将来の描画分岐用ヒントで、現状は markdown 一本ゆえ全種を Markdown で描く。
	-->
	<div class="{separated ? 'border-t border-line ' : ''}px-3 py-2 space-y-1.5">
		{#each displayItems as item, i (i)}
			<div class="text-sm px-0.5" dir="auto">
				<Markdown id={`${id || call.id}-display-${i}`} content={item.content ?? ''} />
			</div>
		{/each}
	</div>
{/snippet}

<!-- 注意: ルートに space-y を置かない — ヘッダ tab は -mb-px で枠上辺に密着させる -->
<div {id} class="w-full text-sm -ml-2" bind:this={rootElement}>
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="w-fit cursor-pointer text-content-muted hover:text-content transition ms-2 px-2 py-0.5 border {toggleHover
			? 'border-line-hover'
			: 'border-line'} bg-gray-100 dark:bg-gray-850 {open || hasDisplay
			? 'relative z-[1] -mb-px rounded-t-md border-b-0'
			: 'rounded-md'}"
		onpointerenter={() => (toggleHover = true)}
		onpointerleave={() => (toggleHover = false)}
		onpointerup={() => {
			if (open) scrollOnClose = true;
			open = !open;
			autoOpened = false;
		}}
	>
		<div class="font-medium flex items-center gap-2 {isDone && !execRunning ? '' : 'shimmer'}">
			{#if !isDone || execRunning}
				<div>
					<Spinner className="size-4" />
				</div>
			{:else}
				<div class="text-green-500 dark:text-green-400 shrink-0">
					<span class="i-lucide-circle-check size-4 block"></span>
				</div>
			{/if}

			<div>
				{#if isDone && !execRunning}
					<Markdown
						id={`${id}-header`}
						content={$i18n.t('Tool used **{{NAME}}**', { NAME: name })}
					/>
				{:else}
					<Markdown
						id={`${id}-header-executing`}
						content={$i18n.t('Executing **{{NAME}}**...', { NAME: name })}
					/>
				{/if}
			</div>

			<!--
				実行中は進捗(#100)、完了後は所要時間。どちらも **素のテキスト**で描く —
				ヘッダ本体は Markdown 経由だが、ここは tool が中身を決める文字列なので
				md として通すと任意の markdown をヘッダへ注入できてしまう。
				ヘッダは w-fit バッジで open 時は枠の上辺タブに合体するため、長い
				description で幅が暴れないよう max-w + truncate で頭を押さえる。
			-->
			{#if subagentLabel}
				<div class="max-w-52 truncate font-normal">{subagentLabel}</div>
			{:else if progressLabel}
				<div class="max-w-52 truncate font-normal" title={progressLabel}>
					{progressLabel}
				</div>
			{/if}
			{#if elapsedLabel}
				<div class="font-normal tabular-nums">{elapsedLabel}</div>
			{:else if isDone && durationLabel}
				<div class="font-normal text-content-subtle tabular-nums">{durationLabel}</div>
			{/if}

			<div class="flex self-center translate-y-[1px]">
				{#if open}
					<span class="i-lucide-chevron-up size-3.5 block"></span>
				{:else}
					<span class="i-lucide-chevron-down size-3.5 block"></span>
				{/if}
			</div>
		</div>
	</div>

	{#if open}
		<div
			transition:slide={{ duration: 300, easing: quintOut, axis: 'y' }}
			onoutroend={restoreViewAfterCollapse}
		>
			<div class="border {toggleHover ? 'border-line-hover' : 'border-line'} rounded-lg transition">
				<div class="px-3 py-2">
					{#if !hasArgs}
						<div class="text-xs text-content-muted italic my-0.5 px-0.5">
							{$i18n.t('No arguments')}
						</div>
					{:else if argDisplays === null}
						<HljsCode lang="json" code={formatJSONString(args)} />
					{:else}
						{#each argDisplays as arg (arg.name)}
							{#if arg.kind === 'scalar'}
								<div class="text-sm font-mono my-0.5 px-0.5" dir="ltr">
									<span class="text-content-muted">{arg.name}:</span>
									<span class="whitespace-pre-wrap">{arg.value}</span>
								</div>
							{:else}
								<div class="text-content-muted text-sm mt-1 mb-0.5 font-mono">
									{arg.name}:
								</div>
								<HljsCode
									lang={arg.kind === 'code' ? (arg.language ?? 'plaintext') : 'json'}
									code={arg.value}
								/>
							{/if}
						{/each}
					{/if}
				</div>

				{#if resultDisplay || subagentSessionId}
					<div class="flex ms-2 mt-1">
						<!-- Result tab: クリックでも閉じられる -->
						<!-- svelte-ignore a11y_no_static_element_interactions -->
						<div
							class="relative z-[1] -mb-px cursor-pointer inline-flex items-center gap-1 text-xs font-medium text-content-muted hover:text-content transition bg-gray-100 dark:bg-gray-850 border border-b-0 {toggleHover
								? 'border-line-hover'
								: 'border-line'} rounded-t-md px-1.5 py-0.5"
							onpointerenter={() => (toggleHover = true)}
							onpointerleave={() => (toggleHover = false)}
							onpointerup={(e) => {
								e.stopPropagation();
								collapseFromBottom();
							}}
						>
							<span class="i-lucide-corner-down-left size-3 block text-emerald-500"></span>
							Result
						</div>
					</div>
					<div
						class="border-t {toggleHover
							? 'border-line-hover'
							: 'border-line'} transition px-3 py-2 space-y-2"
					>
						{#if !resultDisplay || resultDisplay.kind === 'subagent'}
							<!-- 子 session は下の固定位置で描く(live → 結果到着で同じインスタンス) -->
						{:else if resultDisplay.kind === 'skill'}
							<!--
								load_skill の構造化結果(#35): 指示本文(md レンダリング)+ 導入 tool 一覧。
								XSS 不変条件: tool 結果は外部入力なのでサニタイズ必須 — Markdown は
								MarkdownTokens/InlineTokens 内で DOMPurify.sanitize を通す(ReasoningBlock と同じ)。
							-->
							<div class="text-sm px-0.5" dir="auto">
								<Markdown id={`${id || call.id}-skill`} content={resultDisplay.value} />
							</div>
							{#if resultDisplay.tools.length > 0}
								<div class="mt-1.5 mb-1">
									<span
										class="inline-block text-xs font-medium text-content-muted bg-gray-100 dark:bg-gray-850 rounded-md px-1.5 py-0.5"
									>
										{$i18n.t('Tools loaded')}
									</span>
								</div>
								<div class="flex flex-wrap gap-1 my-0.5 px-0.5">
									{#each resultDisplay.tools as toolName}
										<span
											class="text-xs font-mono rounded-md bg-gray-100 dark:bg-gray-850 px-1.5 py-0.5"
											>{toolName}</span
										>
									{/each}
								</div>
							{/if}
						{:else if resultDisplay.kind === 'execution'}
							<!--
								background 昇格された呼び出し(バックグラウンドタスク)の spawn 形(#110):
								exec 行の現在状態を executions store から引く 1 行ステータス。
								結果は BE の配達通知(SystemNoticeMessage)または await_task が運ぶ。
							-->
							<ExecutionBlock id={`${id || call.id}-execution`} display={resultDisplay} />
						{:else if resultDisplay.kind === 'text'}
							<div class="text-sm font-mono whitespace-pre-wrap my-0.5 px-0.5" dir="ltr">
								{resultDisplay.value}
							</div>
						{:else if resultDisplay.kind === 'markdown'}
							<!-- Markdown 戻り型: md としてレンダリング(サニタイズは skill と同じ経路) -->
							<div class="text-sm px-0.5" dir="auto">
								<Markdown id={`${id || call.id}-result`} content={resultDisplay.value} />
							</div>
						{:else if resultDisplay.kind === 'code'}
							<HljsCode lang={resultDisplay.language} code={resultDisplay.value} />
						{:else}
							<HljsCode lang="json" code={resultDisplay.value} />
						{/if}
						{#if subagentSessionId}
							<!--
								run_subagent の子 agent session のインライン表示(#107)。結果の subagent 形
								(display)と、実行中の所在(live — #140 UI 追従)の両方をここ 1 箇所で描く。
								{#if open} の内側なので、開くまで子 chat の fetch は走らない。
							-->
							{#key subagentSessionId}
								<SubagentSessionBlock
									id={`${id || call.id}-subagent`}
									sessionId={subagentSessionId}
									display={subagentDisplay}
									live={liveSubagent}
									onPhase={onChildPhase}
								/>
							{/key}
						{/if}
					</div>
				{/if}

				{#if hasDisplay}
					{@render displayBody(true)}
				{/if}
			</div>

			<div class="flex justify-between mx-2">
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div
					class="relative cursor-pointer -mt-px flex items-center gap-1 text-xs font-medium text-content-muted hover:text-content transition bg-gray-100 dark:bg-gray-850 border border-t-0 {toggleHover
						? 'border-line-hover'
						: 'border-line'} rounded-b-md px-1.5 py-0.5"
					onpointerenter={() => (toggleHover = true)}
					onpointerleave={() => (toggleHover = false)}
					onpointerup={(e) => {
						e.stopPropagation();
						collapseFromBottom();
					}}
				>
					<span class="i-lucide-chevron-up size-3 block"></span>
					{$i18n.t('Close')}
				</div>

				{#if onExpandAll}
					<!-- svelte-ignore a11y_no_static_element_interactions -->
					<div
						class="relative cursor-pointer -mt-px flex items-center gap-1 text-xs font-medium text-content-muted hover:text-content transition bg-gray-100 dark:bg-gray-850 border border-t-0 {toggleHover
							? 'border-line-hover'
							: 'border-line'} rounded-b-md px-1.5 py-0.5"
						onpointerenter={() => (toggleHover = true)}
						onpointerleave={() => (toggleHover = false)}
						onpointerup={(e) => {
							e.stopPropagation();
							// 一括で畳んだあとも、押したこのブロックは視界に戻す
							scrollOnClose = true;
							onExpandAll?.(false);
						}}
					>
						<span class="i-lucide-chevrons-down-up size-3 block"></span>
						{$i18n.t('Collapse all')}
					</div>
				{/if}
			</div>
		</div>
	{/if}

	{#if !open && hasDisplay}
		<!-- 閉時: badge から下がる専用枠に display を出す(open 時は枠の底に出るので
		     ここは描かない)。badge は open||hasDisplay で下辺を開いたタブになり、この枠と
		     -mb-px で密着して 1 つの塊に見える。 -->
		<div class="border {toggleHover ? 'border-line-hover' : 'border-line'} rounded-lg transition">
			{@render displayBody(false)}
		</div>
	{/if}
</div>

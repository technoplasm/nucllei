<script lang="ts">
	// llens(#121): サイドバーのチャット検索ボックス。
	// 入力をデバウンスして onSearch(text) に渡す(空文字 = 検索解除)。
	// 「チャットで調べる」は検索語をそのまま onAsk(text) へ渡す(新規チャットの入力欄にプリフィルする側は Sidebar)。
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	let {
		value = $bindable(''),
		onSearch,
		onAsk
	}: {
		value?: string;
		onSearch: (text: string) => void;
		onAsk: (text: string) => void;
	} = $props();

	const DEBOUNCE_MS = 300;
	let timer: ReturnType<typeof setTimeout> | null = null;

	const emit = (text: string) => {
		if (timer) {
			clearTimeout(timer);
			timer = null;
		}
		onSearch(text.trim());
	};

	const onInput = () => {
		if (timer) {
			clearTimeout(timer);
		}
		timer = setTimeout(() => emit(value), DEBOUNCE_MS);
	};

	const clear = () => {
		value = '';
		emit('');
	};

	const onKeyDown = (e: KeyboardEvent) => {
		if (e.key === 'Escape') {
			e.preventDefault();
			clear();
		} else if (e.key === 'Enter') {
			e.preventDefault();
			emit(value);
		}
	};

	const ask = () => {
		const text = value.trim();
		if (!text) {
			return;
		}
		onAsk(text);
	};
</script>

<div class="px-1.5 mt-1 flex items-center gap-1 min-w-0">
	<div
		class="flex-1 min-w-0 flex items-center gap-1.5 px-2 rounded-lg bg-surface-form border border-line focus-within:border-line-hover"
	>
		<span class="i-lucide-search size-4 block shrink-0 text-content-muted"></span>
		<input
			id="sidebar-chat-search"
			class="flex-1 min-w-0 text-sm py-1 bg-transparent outline-hidden placeholder:text-content-subtle"
			type="text"
			autocomplete="off"
			bind:value
			oninput={onInput}
			onkeydown={onKeyDown}
			placeholder={$i18n.t('Search conversations')}
		/>
		{#if value}
			<button
				class="shrink-0 p-0.5 rounded text-content-muted hover:text-content"
				onclick={clear}
				aria-label={$i18n.t('Clear')}
			>
				<span class="i-lucide-x size-3.5 block"></span>
			</button>
		{/if}
	</div>

	<button
		id="sidebar-chat-search-ask"
		class="shrink-0 flex items-center gap-1 px-2 py-1.5 rounded-lg text-xs font-medium text-content hover:bg-sidebar-hover disabled:opacity-50 disabled:hover:bg-transparent"
		disabled={!value.trim()}
		onclick={ask}
		title={$i18n.t('Ask in chat')}
	>
		<span class="i-lucide-message-circle-question-mark size-4 block"></span>
		<span class="whitespace-nowrap">{$i18n.t('Ask in chat')}</span>
	</button>
</div>

<script lang="ts">
	// 本文の番号チップ(#146 簡易版)。押してもページは開かず、そばに吹き出しを出す
	// (ken 2026-10-06「番号押した瞬間開くのは論外」)。吹き出しには ページ名 / その番号で本文が使った節
	// (› 見出し / › 「引用文」)/ ページ全体へのリンク。添付(file)はファイル名のリンク。リンクはすべて新しいタブ。
	// 開閉は bits-ui Popover に任せる: クリック・タップ・Enter / Space で開閉、PC はマウスを乗せても開く
	// (離れたら closeDelay 後に閉じる。吹き出しの中へ移る間は閉じない)、外側を押す・Esc で閉じる、
	// 画面の端では反対側・内側へ寄せる。hover で開いた吹き出しはチップを押すと開いたまま固定され、
	// もう一度押すと閉じる(bits-ui の挙動)。
	// 角括弧は範囲選択のコピーで「[1]」になるよう見えない文字で残す(button 要素は選択から外れる
	// ブラウザがあるので、トリガーは span role=button)。
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { Popover } from 'bits-ui';

	import { flyAndScale } from '$lib/utils/transitions';
	import { citeLabel, type CiteRef } from '$lib/utils/citations';
	import type { CitationScope } from './citations.svelte';

	const i18n = getContext<Writable<i18nType>>('i18n');

	let {
		n,
		cite,
		citations
	}: {
		n: number;
		/** このチップの箇所の引用(吹き出しで該当の節を強調する) */
		cite: CiteRef;
		citations: CitationScope;
	} = $props();

	let open = $state(false);

	const entry = $derived(citations.current.entries[n - 1]);

	const setActive = (on: boolean) => {
		if (on) citations.active = n;
		else if (citations.active === n) citations.active = null;
	};
</script>

<sup class="cite-ref"
	><Popover.Root
		bind:open
		onOpenChange={(state) => {
			setActive(state);
		}}
		><Popover.Trigger openOnHover openDelay={200} closeDelay={300}
			>{#snippet child({ props })}
				{@const { type: _type, disabled: _disabled, ...triggerProps } = props}
				<span
					{...triggerProps}
					role="button"
					tabindex="0"
					aria-label={`[${n}] ${citeLabel(cite)}`}
					class="inline-flex items-center justify-center min-w-[1.45em] h-[1.45em] px-[0.35em] mx-[0.15em] rounded-full text-[0.95em] leading-none font-medium tabular-nums cursor-pointer select-text outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 bg-black/5 dark:bg-white/10 text-content-muted hover:bg-black/10 dark:hover:bg-white/20 hover:text-content data-[state=open]:bg-black/10 dark:data-[state=open]:bg-white/20 data-[state=open]:text-content transition-colors"
					onmouseenter={() => setActive(true)}
					onmouseleave={() => setActive(open)}
					onfocus={() => setActive(true)}
					onblur={() => setActive(open)}
					><span class="sr-only">[</span>{n}<span class="sr-only">]</span></span
				>
			{/snippet}</Popover.Trigger
		><Popover.Portal
			><Popover.Content
				side="top"
				align="center"
				sideOffset={6}
				collisionPadding={12}
				forceMount
				aria-label={$i18n.t('Sources')}
			>
				{#snippet child({ wrapperProps, props, open: shown })}
					{#if shown && entry}
						<div {...wrapperProps}>
							<div
								{...props}
								transition:flyAndScale
								class="z-50 w-max max-w-[min(22rem,calc(100vw-1.5rem))] rounded-xl border border-black/5 dark:border-white/10 bg-white dark:bg-gray-850 px-3 py-2 text-xs leading-relaxed text-content shadow-lg font-primary outline-none"
							>
								<div class="flex items-start gap-1.5 font-medium">
									<span class="shrink-0 tabular-nums text-content-muted">{entry.n}.</span>
									<span
										class="{entry.kind === 'file'
											? 'i-lucide-paperclip'
											: 'i-lucide-file-text'} size-3.5 shrink-0 mt-0.5 text-content-muted"
									></span>
									{#if entry.kind === 'file'}
										<a
											href={entry.pageHref}
											target="_blank"
											rel="nofollow"
											class="min-w-0 break-all hover:underline">{entry.title}</a
										>
									{:else}
										<span class="min-w-0 break-all">{entry.title}</span>
									{/if}
								</div>
								{#if entry.kind === 'ext'}
									{#each entry.sections as section (section.href)}
										<div class="pl-[1.125rem]">
											<a
												href={section.href}
												target="_blank"
												rel="nofollow"
												class="inline-block max-w-full break-all hover:underline {section.href ===
												cite.href
													? 'text-content font-medium'
													: 'text-content-muted hover:text-content'}">› {section.label}</a
											>
										</div>
									{/each}
									<div class="mt-1.5 pt-1.5 border-t border-black/5 dark:border-white/10">
										<a
											href={entry.pageHref}
											target="_blank"
											rel="nofollow"
											class="inline-flex items-center gap-1 text-content-muted hover:text-content hover:underline"
										>
											<span class="i-lucide-external-link size-3.5 shrink-0"></span>
											{$i18n.t('Open the whole page')}
										</a>
									</div>
								{/if}
							</div>
						</div>
					{/if}
				{/snippet}
			</Popover.Content></Popover.Portal
		></Popover.Root
	></sup
>

<script lang="ts">
	import { getContext } from 'svelte';
	import { WEBUI_BASE_URL } from '$lib/constants';
	import ImagePreview from './ImagePreview.svelte';

	const i18n = getContext('i18n');

	let {
		src = '',
		alt = '',
		className = ' w-full outline-hidden focus:outline-hidden',
		imageClassName = 'rounded-lg'
	}: {
		src?: string;
		alt?: string;
		className?: string;
		imageClassName?: string;
	} = $props();

	const _src = $derived(src.startsWith('/') ? `${WEBUI_BASE_URL}${src}` : src);

	// llens(#94): 読み込み失敗(削除済み file の 404 等)は壊れ画像でなくプレースホルダを出す。
	// src が変わったら再試行できるようフラグを戻す
	let failed = $state(false);
	$effect(() => {
		void _src;
		failed = false;
	});

	let showImagePreview = $state(false);
</script>

{#if failed}
	<div class={className}>
		<div
			class="w-fit flex items-center gap-2 px-3 py-2 rounded-lg bg-surface-muted text-content-muted text-sm"
		>
			<span class="i-lucide-image-off size-4 block shrink-0"></span>
			{$i18n.t('Image unavailable')}
		</div>
	</div>
{:else}
	<button
		class={className}
		onclick={() => {
			showImagePreview = true;
		}}
		type="button"
	>
		<img
			src={_src}
			{alt}
			class={imageClassName}
			draggable="false"
			data-cy="image"
			onerror={() => {
				failed = true;
			}}
		/>
	</button>

	<ImagePreview bind:show={showImagePreview} src={_src} {alt} />
{/if}

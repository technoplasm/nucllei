<script lang="ts">
	import { WEBUI_BASE_URL } from '$lib/constants';
	import ImagePreview from './ImagePreview.svelte';

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

	let showImagePreview = $state(false);
</script>

<button
	class={className}
	onclick={() => {
		showImagePreview = true;
	}}
	type="button"
>
	<img src={_src} {alt} class={imageClassName} draggable="false" data-cy="image" />
</button>

<ImagePreview bind:show={showImagePreview} src={_src} {alt} />

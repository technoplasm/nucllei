<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import panzoom, { type PanZoom } from 'panzoom';

	export let show = false;
	export let src = '';
	export let alt = '';

	let mounted = false;

	let previewElement = null;

	let instance: PanZoom;

	let sceneParentElement: HTMLElement;
	let sceneElement: HTMLElement;

	$: if (sceneElement) {
		instance = panzoom(sceneElement, {
			bounds: true,
			boundsPadding: 0.1,

			zoomSpeed: 0.065
		});
	}
	const resetPanZoomViewport = () => {
		instance.moveTo(0, 0);
		instance.zoomAbs(0, 0, 1);
		console.log(instance.getTransform());
	};

	const downloadImage = (url, filename, prefixName = '') => {
		fetch(url)
			.then((response) => response.blob())
			.then((blob) => {
				const objectUrl = window.URL.createObjectURL(blob);
				const link = document.createElement('a');
				link.href = objectUrl;
				link.download = `${prefixName}${filename}`;
				document.body.appendChild(link);
				link.click();
				document.body.removeChild(link);
				window.URL.revokeObjectURL(objectUrl);
			})
			.catch((error) => console.error('Error downloading image:', error));
	};

	const handleKeyDown = (event: KeyboardEvent) => {
		if (event.key === 'Escape') {
			console.log('Escape');
			show = false;
		}
	};

	onMount(() => {
		mounted = true;
	});

	$: if (show && previewElement) {
		document.body.appendChild(previewElement);
		window.addEventListener('keydown', handleKeyDown);
		document.body.style.overflow = 'hidden';
	} else if (previewElement) {
		window.removeEventListener('keydown', handleKeyDown);
		document.body.removeChild(previewElement);
		document.body.style.overflow = 'unset';
	}

	onDestroy(() => {
		show = false;

		if (previewElement) {
			document.body.removeChild(previewElement);
		}
	});
</script>

{#if show}
	<!-- svelte-ignore a11y-click-events-have-key-events -->
	<!-- svelte-ignore a11y-no-static-element-interactions -->
	<div
		bind:this={previewElement}
		class="modal fixed top-0 right-0 left-0 bottom-0 bg-black text-white w-full min-h-screen h-screen flex justify-center z-9999 overflow-hidden overscroll-contain"
	>
		<div class=" absolute left-0 w-full flex justify-between select-none z-10">
			<div>
				<button
					class=" p-5"
					aria-label="Close"
					on:pointerdown={(e) => {
						e.stopImmediatePropagation();
						e.preventDefault();
						show = false;
					}}
					on:click={(e) => {
						show = false;
					}}
				>
					<span class="i-lucide-x size-6 block"></span>
				</button>
			</div>

			<div>
				<button
					class=" p-5"
					aria-label="Download"
					on:pointerdown={(e) => {
						e.stopImmediatePropagation();
						e.preventDefault();
						downloadImage(src, src.substring(src.lastIndexOf('/') + 1), alt);
					}}
					on:click={(e) => {
						downloadImage(src, src.substring(src.lastIndexOf('/') + 1), alt);
					}}
				>
					<span class="i-lucide-download size-6 block"></span>
				</button>
			</div>
		</div>
		<div bind:this={sceneElement} class="flex h-full max-h-full justify-center items-center">
			<img {src} {alt} class=" mx-auto h-full object-scale-down select-none" draggable="false" />
		</div>
	</div>
{/if}

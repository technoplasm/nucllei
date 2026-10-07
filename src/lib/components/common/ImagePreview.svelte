<script lang="ts">
	import panzoom from 'panzoom';

	let {
		show = $bindable(false),
		src = '',
		alt = ''
	}: { show?: boolean; src?: string; alt?: string } = $props();

	let previewElement = $state<HTMLDivElement | null>(null);

	let sceneElement = $state<HTMLElement>();

	$effect(() => {
		if (sceneElement) {
			panzoom(sceneElement, {
				bounds: true,
				boundsPadding: 0.1,

				zoomSpeed: 0.065
			});
		}
	});

	const downloadImage = (url: string, filename: string, prefixName = '') => {
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
			show = false;
		}
	};

	// body のスクロール止めは、body 直下に portal 中の data-scroll-lock が 1 つも残って
	// いないときだけ解く (common/Modal.svelte と同じ。重ねたモーダルの下を守る)
	const releaseScrollLock = () => {
		if (!document.body.querySelector(':scope > [data-scroll-lock]')) {
			document.body.style.overflow = 'unset';
		}
	};

	// portal: 表示中だけ body 直下へ移す。$effect は DOM 更新後に走るため
	// (非表示化では {#if} teardown が先で previewElement は null 化済み)、
	// 後始末は else 分岐ではなく effect cleanup で行う — 要素は閉包で捕まえ、
	// body 直下にいるときだけ外す(portal の NotFoundError 対策、common/Modal.svelte と同型)
	$effect(() => {
		if (show && previewElement) {
			const element = previewElement;
			document.body.appendChild(element);
			window.addEventListener('keydown', handleKeyDown);
			document.body.style.overflow = 'hidden';

			return () => {
				window.removeEventListener('keydown', handleKeyDown);
				if (element.parentNode === document.body) {
					document.body.removeChild(element);
				}
				releaseScrollLock();
			};
		}
	});
</script>

{#if show}
	<div
		bind:this={previewElement}
		data-scroll-lock
		class="modal fixed top-0 right-0 left-0 bottom-0 bg-black text-white w-full min-h-screen h-screen flex justify-center z-9999 overflow-hidden overscroll-contain"
	>
		<div class=" absolute left-0 w-full flex justify-between select-none z-10">
			<div>
				<button
					class=" p-5"
					aria-label="Close"
					onpointerdown={(e) => {
						e.stopImmediatePropagation();
						e.preventDefault();
						show = false;
					}}
					onclick={() => {
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
					onpointerdown={(e) => {
						e.stopImmediatePropagation();
						e.preventDefault();
						downloadImage(src, src.substring(src.lastIndexOf('/') + 1), alt);
					}}
					onclick={() => {
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

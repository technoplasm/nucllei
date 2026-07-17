<script lang="ts">
	import fileSaver from 'file-saver';
	const { saveAs } = fileSaver;

	import { toast } from 'svelte-sonner';

	import panzoom, { type PanZoom } from 'panzoom';
	import DOMPurify from 'dompurify';

	import { onMount, getContext } from 'svelte';
	const i18n = getContext('i18n');

	import { copyToClipboard } from '$lib/utils';

	import Tooltip from './Tooltip.svelte';

	export let className = '';
	export let svg = '';
	export let content = '';

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

	const downloadAsSVG = () => {
		const svgBlob = new Blob([svg], { type: 'image/svg+xml' });
		saveAs(svgBlob, `diagram.svg`);
	};
</script>

<div bind:this={sceneParentElement} class="relative {className}">
	<div bind:this={sceneElement} class="flex h-full max-h-full justify-center items-center">
		<!-- llens(GHSA-r29h-37fj-x2w6): SVG レンダラの XSS 対策。mermaid/Artifacts 由来の
		     SVG を DOMPurify で無害化(script / on* ハンドラ / foreignObject 内の危険 HTML を除去)。 -->
		{@html DOMPurify.sanitize(svg)}
	</div>

	{#if content}
		<div class=" absolute top-1 right-1">
			<div class="flex gap-1">
				<Tooltip content={$i18n.t('Download as SVG')}>
					<button
						class="p-1.5 rounded-lg border border-gray-100 dark:border-none dark:bg-gray-850 hover:bg-gray-50 dark:hover:bg-gray-800 transition"
						aria-label={$i18n.t('Download as SVG')}
						on:click={() => {
							downloadAsSVG();
						}}
					>
						<span class="i-lucide-download size-4 block"></span>
					</button>
				</Tooltip>

				<Tooltip content={$i18n.t('Reset view')}>
					<button
						class="p-1.5 rounded-lg border border-gray-100 dark:border-none dark:bg-gray-850 hover:bg-gray-50 dark:hover:bg-gray-800 transition"
						aria-label={$i18n.t('Reset view')}
						on:click={() => {
							resetPanZoomViewport();
						}}
					>
						<span class="i-lucide-refresh-cw size-4 block"></span>
					</button>
				</Tooltip>

				<Tooltip content={$i18n.t('Copy to clipboard')}>
					<button
						class="p-1.5 rounded-lg border border-gray-100 dark:border-none dark:bg-gray-850 hover:bg-gray-50 dark:hover:bg-gray-800 transition"
						aria-label={$i18n.t('Copy to clipboard')}
						on:click={() => {
							copyToClipboard(content);
							toast.success($i18n.t('Copied to clipboard'));
						}}
					>
						<span class="i-lucide-clipboard size-4 block"></span>
					</button>
				</Tooltip>
			</div>
		</div>
	{/if}
</div>

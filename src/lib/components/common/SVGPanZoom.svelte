<script lang="ts">
	import fileSaver from 'file-saver';
	const { saveAs } = fileSaver;

	import { toast } from 'svelte-sonner';

	import panzoom, { type PanZoom } from 'panzoom';
	import DOMPurify from 'dompurify';

	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import { copyToClipboard } from '$lib/utils';

	import Tooltip from './Tooltip.svelte';

	let {
		className = '',
		svg = '',
		content = ''
	}: { className?: string; svg?: string; content?: string } = $props();

	let instance: PanZoom;

	let sceneElement = $state<HTMLElement>();

	$effect(() => {
		if (sceneElement) {
			instance = panzoom(sceneElement, {
				bounds: true,
				boundsPadding: 0.1,

				zoomSpeed: 0.065
			});
		}
	});

	const resetPanZoomViewport = () => {
		instance.moveTo(0, 0);
		instance.zoomAbs(0, 0, 1);
	};

	const downloadAsSVG = () => {
		const svgBlob = new Blob([svg], { type: 'image/svg+xml' });
		saveAs(svgBlob, `diagram.svg`);
	};
</script>

<div class="relative {className}">
	<div bind:this={sceneElement} class="flex h-full max-h-full justify-center items-center">
		<!-- llens(GHSA-r29h-37fj-x2w6): SVG レンダラの XSS 対策。mermaid/Artifacts 由来の
		     SVG を DOMPurify で無害化(script / on* ハンドラ / foreignObject 内の危険 HTML を除去)。 -->
		<!-- eslint-disable-next-line svelte/no-at-html-tags -- #17 XSS sink 監査済み(サニタイズは各経路) -->
		{@html DOMPurify.sanitize(svg)}
	</div>

	{#if content}
		<div class=" absolute top-1 right-1">
			<div class="flex gap-1">
				<Tooltip content={$i18n.t('Download as SVG')}>
					<button
						class="p-1.5 rounded-lg border border-gray-100 dark:border-none dark:bg-gray-850 hover:bg-gray-50 dark:hover:bg-gray-800 transition"
						aria-label={$i18n.t('Download as SVG')}
						onclick={() => {
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
						onclick={() => {
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
						onclick={() => {
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

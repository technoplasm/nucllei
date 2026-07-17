<script lang="ts">
	import { getContext } from 'svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	const i18n = getContext('i18n');

	type CitationMetadata = Record<string, any>;

	type Citation = {
		source?: { name?: string; url?: string };
		document?: string[];
		metadata?: (CitationMetadata | undefined)[];
		distances?: number[];
	};

	type MergedDocument = {
		source?: { name?: string; url?: string };
		document: string;
		metadata?: CitationMetadata;
		distance?: number;
	};

	let {
		show = $bindable(false),
		citation = null,
		showPercentage = false,
		showRelevance = true
	}: {
		show?: boolean;
		citation?: Citation | null;
		showPercentage?: boolean;
		showRelevance?: boolean;
	} = $props();

	function calculatePercentage(distance: number) {
		if (typeof distance !== 'number') return null;
		if (distance < 0) return 0;
		if (distance > 1) return 100;
		return Math.round(distance * 10000) / 100;
	}

	function getRelevanceColor(percentage: number) {
		if (percentage >= 80)
			return 'bg-green-200 dark:bg-green-800 text-green-800 dark:text-green-200';
		if (percentage >= 60)
			return 'bg-yellow-200 dark:bg-yellow-800 text-yellow-800 dark:text-yellow-200';
		if (percentage >= 40)
			return 'bg-orange-200 dark:bg-orange-800 text-orange-800 dark:text-orange-200';
		return 'bg-red-200 dark:bg-red-800 text-red-800 dark:text-red-200';
	}

	const mergedDocuments: MergedDocument[] = $derived.by(() => {
		const currentCitation = citation;
		if (!currentCitation) {
			return [];
		}

		const documents = (currentCitation.document ?? []).map((c, i) => {
			return {
				source: currentCitation.source,
				document: c,
				metadata: currentCitation.metadata?.[i],
				distance: currentCitation.distances?.[i]
			};
		});

		if (documents.every((doc) => doc.distance !== undefined)) {
			return documents.sort((a, b) => (b.distance ?? Infinity) - (a.distance ?? Infinity));
		}
		return documents;
	});

	const decodeString = (str: string) => {
		try {
			return decodeURIComponent(str);
		} catch (e) {
			return str;
		}
	};
</script>

<Modal size="lg" bind:show>
	<div>
		<div class=" flex justify-between text-content px-5 pt-4 pb-2">
			<div class=" text-lg font-medium self-center capitalize">
				{$i18n.t('Citation')}
			</div>
			<button
				class="self-center"
				aria-label={$i18n.t('Close')}
				onclick={() => {
					show = false;
				}}
			>
				<span class="i-lucide-x size-5 block"></span>
			</button>
		</div>

		<div class="flex flex-col md:flex-row w-full px-6 pb-5 md:space-x-4">
			<div
				class="flex flex-col w-full text-content overflow-y-scroll max-h-[22rem] scrollbar-hidden"
			>
				{#each mergedDocuments as document, documentIdx}
					<div class="flex flex-col w-full">
						<div class="text-sm font-medium text-content">
							{$i18n.t('Source')}
						</div>

						{#if document.source?.name}
							<Tooltip
								className="w-fit"
								content={$i18n.t('Open file')}
								placement="top-start"
								tippyOptions={{ duration: [500, 0] }}
							>
								<div class="text-sm text-content-muted flex items-center gap-2 w-fit">
									<a
										class="hover:text-content underline grow"
										href={document?.metadata?.file_id
											? `${WEBUI_API_BASE_URL}/files/${document?.metadata?.file_id}/content${document?.metadata?.page !== undefined ? `#page=${document.metadata.page + 1}` : ''}`
											: document.source?.url?.includes('http')
												? document.source.url
												: `#`}
										target="_blank"
									>
										{decodeString(document?.metadata?.name ?? document.source.name)}
									</a>
									{#if document?.metadata?.page}
										<span class="text-xs text-content-muted">
											({$i18n.t('page')}
											{document.metadata.page + 1})
										</span>
									{/if}
								</div>
							</Tooltip>
							{#if showRelevance}
								<div class="text-sm font-medium text-content mt-2">
									{$i18n.t('Relevance')}
								</div>
								{#if document.distance !== undefined}
									<Tooltip
										className="w-fit"
										content={$i18n.t('Semantic distance to query')}
										placement="top-start"
										tippyOptions={{ duration: [500, 0] }}
									>
										<div class="text-sm my-1 text-content-muted flex items-center gap-2 w-fit">
											{#if showPercentage}
												{@const percentage = calculatePercentage(document.distance)}

												{#if typeof percentage === 'number'}
													<span
														class={`px-1 rounded-sm font-medium ${getRelevanceColor(percentage)}`}
													>
														{percentage.toFixed(2)}%
													</span>
												{/if}
												<span class="text-content-muted">
													({(document?.distance ?? 0).toFixed(4)})
												</span>
											{:else}
												<span class="text-content-muted">
													{(document?.distance ?? 0).toFixed(4)}
												</span>
											{/if}
										</div>
									</Tooltip>
								{:else}
									<div class="text-sm text-content-muted">
										{$i18n.t('No distance available')}
									</div>
								{/if}
							{/if}
						{:else}
							<div class="text-sm text-content-muted">
								{$i18n.t('No source available')}
							</div>
						{/if}
					</div>
					<div class="flex flex-col w-full">
						<div class=" text-sm font-medium text-content mt-2">
							{$i18n.t('Content')}
						</div>
						{#if document.metadata?.html}
							<iframe
								class="w-full border-0 h-auto rounded-none"
								sandbox="allow-scripts allow-forms allow-same-origin"
								srcdoc={document.document}
								title={$i18n.t('Content')}
							></iframe>
						{:else}
							<pre class="text-sm text-content-muted whitespace-pre-line">
                {document.document}
              </pre>
						{/if}
					</div>

					{#if documentIdx !== mergedDocuments.length - 1}
						<hr class="border-gray-100 dark:border-gray-850 my-3" />
					{/if}
				{/each}
			</div>
		</div>
	</div>
</Modal>

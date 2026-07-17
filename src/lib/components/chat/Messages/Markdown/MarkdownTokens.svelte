<script lang="ts">
	import DOMPurify from 'dompurify';
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import fileSaver from 'file-saver';
	const { saveAs } = fileSaver;

	import { unescapeHtml } from '$lib/utils';
	import { sanitizeIframeHtml } from '$lib/utils/marked/html-token';

	import { WEBUI_BASE_URL } from '$lib/constants';

	import CodeBlock from '$lib/components/chat/Messages/CodeBlock.svelte';
	import MarkdownInlineTokens from '$lib/components/chat/Messages/Markdown/MarkdownInlineTokens.svelte';
	import KatexRenderer from './KatexRenderer.svelte';
	import AlertRenderer, { alertComponent } from './AlertRenderer.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import MarkdownTokens from './MarkdownTokens.svelte';

	import Source from './Source.svelte';

	type Token = { type: string; [key: string]: any };

	let {
		id,
		tokens,
		top = true,
		onTaskClick = () => {},
		onSourceClick = () => {}
	}: {
		id: string;
		tokens: Token[];
		top?: boolean;
		onTaskClick?: (payload: Record<string, any>) => void;
		onSourceClick?: (id: string, sourceId?: string) => void;
	} = $props();

	const headerComponent = (depth: number) => {
		return 'h' + depth;
	};

	const exportTableToCSVHandler = (token: Token, tokenIdx = 0) => {
		// Extract header row text and escape for CSV.
		const header = token.header.map(
			(headerCell: Token) => `"${headerCell.text.replace(/"/g, '""')}"`
		);

		// Create an array for rows that will hold the mapped cell text.
		const rows = token.rows.map((row: Token[]) =>
			row.map((cell: Token) => {
				// Map tokens into a single text
				const cellContent = cell.tokens.map((cellToken: Token) => cellToken.text).join('');
				// Escape double quotes and wrap the content in double quotes
				return `"${cellContent.replace(/"/g, '""')}"`;
			})
		);

		// Combine header and rows
		const csvData = [header, ...rows];

		// Join the rows using commas (,) as the separator and rows using newline (\n).
		const csvContent = csvData.map((row) => row.join(',')).join('\n');

		// To handle Unicode characters, you need to prefix the data with a BOM:
		const bom = '\uFEFF'; // BOM for UTF-8

		// Create a new Blob prefixed with the BOM to ensure proper Unicode encoding.
		const blob = new Blob([bom + csvContent], { type: 'text/csv;charset=UTF-8' });

		// Use FileSaver.js's saveAs function to save the generated CSV file.
		saveAs(blob, `table-${id}-${tokenIdx}.csv`);
	};
</script>

{#each tokens as token, tokenIdx (tokenIdx)}
	{#if token.type === 'hr'}
		<hr class=" border-line! my-6!" />
	{:else if token.type === 'heading'}
		<svelte:element this={headerComponent(token.depth)} dir="auto">
			<MarkdownInlineTokens id={`${id}-${tokenIdx}-h`} tokens={token.tokens} {onSourceClick} />
		</svelte:element>
	{:else if token.type === 'code'}
		{#if token.raw.includes('```')}
			<CodeBlock
				id={`${id}-${tokenIdx}`}
				{token}
				lang={token?.lang ?? ''}
				code={token?.text ?? ''}
			/>
		{:else}
			{token.text}
		{/if}
	{:else if token.type === 'table'}
		<div class="relative w-full group">
			<div class="scrollbar-hidden relative overflow-x-auto max-w-full rounded-lg">
				<table class=" w-full text-sm text-left text-content-muted max-w-full border-collapse">
					<thead class="text-xs text-content uppercase">
						<tr class="">
							{#each token.header as header, headerIdx}
								<th
									scope="col"
									class="px-3! py-1.5! cursor-pointer font-bold border-b-2 border-line!"
									style={token.align[headerIdx] ? `text-align: ${token.align[headerIdx]}` : ''}
								>
									<div class="gap-1.5 text-left">
										<div class="shrink-0 break-normal">
											<MarkdownInlineTokens
												id={`${id}-${tokenIdx}-header-${headerIdx}`}
												tokens={header.tokens}
												{onSourceClick}
											/>
										</div>
									</div>
								</th>
							{/each}
						</tr>
					</thead>
					<tbody>
						{#each token.rows as row, rowIdx}
							<tr class="border-b border-line! text-xs">
								{#each row ?? [] as cell, cellIdx}
									<td
										class="px-3! py-1.5! text-content dark:text-white w-max"
										style={token.align[cellIdx] ? `text-align: ${token.align[cellIdx]}` : ''}
									>
										<div class="break-normal">
											<MarkdownInlineTokens
												id={`${id}-${tokenIdx}-row-${rowIdx}-${cellIdx}`}
												tokens={cell.tokens}
												{onSourceClick}
											/>
										</div>
									</td>
								{/each}
							</tr>
						{/each}
					</tbody>
				</table>
			</div>

			<div class=" absolute top-1 right-1.5 z-20 invisible group-hover:visible">
				<Tooltip content={$i18n.t('Export to CSV')}>
					<button
						class="p-1 rounded-lg bg-transparent"
						aria-label={$i18n.t('Export to CSV')}
						onclick={(e) => {
							e.stopPropagation();
							exportTableToCSVHandler(token, tokenIdx);
						}}
					>
						<span class="i-lucide-download size-3.5 block"></span>
					</button>
				</Tooltip>
			</div>
		</div>
	{:else if token.type === 'blockquote'}
		{@const alert = alertComponent(token)}
		{#if alert}
			<AlertRenderer {alert} {id} {tokenIdx} {onTaskClick} {onSourceClick} />
		{:else}
			<blockquote dir="auto">
				<MarkdownTokens
					id={`${id}-${tokenIdx}`}
					tokens={token.tokens}
					{onTaskClick}
					{onSourceClick}
				/>
			</blockquote>
		{/if}
	{:else if token.type === 'list'}
		{#if token.ordered}
			<ol start={token.start || 1} dir="auto">
				{#each token.items as item, itemIdx}
					<li class="text-start">
						{#if item?.task}
							<input
								class=" translate-y-[1px] -translate-x-1"
								type="checkbox"
								checked={item.checked}
								onchange={(e) => {
									onTaskClick({
										id: id,
										token: token,
										tokenIdx: tokenIdx,
										item: item,
										itemIdx: itemIdx,
										checked: e.currentTarget.checked
									});
								}}
							/>
						{/if}

						<MarkdownTokens
							id={`${id}-${tokenIdx}-${itemIdx}`}
							tokens={item.tokens}
							top={token.loose}
							{onTaskClick}
							{onSourceClick}
						/>
					</li>
				{/each}
			</ol>
		{:else}
			<ul dir="auto">
				{#each token.items as item, itemIdx}
					<li class="text-start">
						{#if item?.task}
							<input
								class=" translate-y-[1px] -translate-x-1"
								type="checkbox"
								checked={item.checked}
								onchange={(e) => {
									onTaskClick({
										id: id,
										token: token,
										tokenIdx: tokenIdx,
										item: item,
										itemIdx: itemIdx,
										checked: e.currentTarget.checked
									});
								}}
							/>
						{/if}

						<MarkdownTokens
							id={`${id}-${tokenIdx}-${itemIdx}`}
							tokens={item.tokens}
							top={token.loose}
							{onTaskClick}
							{onSourceClick}
						/>
					</li>
				{/each}
			</ul>
		{/if}
	{:else if token.type === 'html'}
		{@const html = DOMPurify.sanitize(token.text)}
		{#if html && html.includes('<video')}
			{@html html}
		{:else if token.text.includes(`<iframe src="${WEBUI_BASE_URL}/api/v1/files/`)}
			{@html sanitizeIframeHtml(token.text)}
		{:else if token.text.includes(`<source_id`)}
			<Source {id} {token} onClick={onSourceClick} />
		{:else}
			{token.text}
		{/if}
	{:else if token.type === 'iframe'}
		<iframe
			src="{WEBUI_BASE_URL}/api/v1/files/{token.fileId}/content"
			title={token.fileId}
			width="100%"
			frameborder="0"
			onload={(e) => {
				const el = e.currentTarget as HTMLIFrameElement;
				const body = el.contentWindow?.document?.body;
				if (body) {
					el.style.height = body.scrollHeight + 20 + 'px';
				}
			}}
		></iframe>
	{:else if token.type === 'paragraph'}
		<p dir="auto">
			<MarkdownInlineTokens
				id={`${id}-${tokenIdx}-p`}
				tokens={token.tokens ?? []}
				{onSourceClick}
			/>
		</p>
	{:else if token.type === 'text'}
		{#if top}
			<p>
				{#if token.tokens}
					<MarkdownInlineTokens id={`${id}-${tokenIdx}-t`} tokens={token.tokens} {onSourceClick} />
				{:else}
					{unescapeHtml(token.text)}
				{/if}
			</p>
		{:else if token.tokens}
			<MarkdownInlineTokens
				id={`${id}-${tokenIdx}-p`}
				tokens={token.tokens ?? []}
				{onSourceClick}
			/>
		{:else}
			{unescapeHtml(token.text)}
		{/if}
	{:else if token.type === 'inlineKatex'}
		{#if token.text}
			<KatexRenderer content={token.text} displayMode={token?.displayMode ?? false} />
		{/if}
	{:else if token.type === 'blockKatex'}
		{#if token.text}
			<KatexRenderer content={token.text} displayMode={token?.displayMode ?? false} />
		{/if}
	{:else if token.type === 'space'}
		<div class="my-2"></div>
	{/if}
{/each}

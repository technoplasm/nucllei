import { describe, it, expect } from 'vitest';
import { lex } from '$lib/utils/markdown-it';
import DOMPurify from 'dompurify';
import { MARKDOWN_IFRAME_SANITIZE_CONFIG, sanitizeIframeHtml } from '$lib/utils/marked/html-token';

// GHSA-9f4f-jv96-8766 / CVE-2025-46719 regression tests.
//
// The `html` token branch in MarkdownTokens.svelte / MarkdownInlineTokens.svelte
// renders any token whose text contains `<iframe src="/api/v1/files/` (the
// internal file-preview iframe). It used to emit that text RAW via
// `{@html token.text}`, so an attacker could smuggle executable markup next to
// the expected iframe and achieve Stored XSS. The fix routes the branch through
// `sanitizeIframeHtml` (DOMPurify with <iframe> re-allowed).
//
// DOMPurify needs a DOM, which the default (node) vitest environment does not
// provide. The marked-tokenisation and config-shape assertions run everywhere
// and document/guard the vulnerable branch; the DOMPurify behaviour assertions
// run wherever a DOM is available (browser / jsdom / happy-dom env).

// In production WEBUI_BASE_URL === '' so the branch guard is this substring.
const IFRAME_GUARD = '<iframe src="/api/v1/files/';

const hasDom =
	typeof (DOMPurify as unknown as { sanitize?: unknown }).sanitize === 'function' &&
	(DOMPurify as unknown as { isSupported?: boolean }).isSupported === true;

describe('markdown html-token attack surface (markdown-it tokenisation)', () => {
	it('vector 1: a block <iframe> line absorbs an onerror <img> into ONE html token', () => {
		const src = `<iframe src="/api/v1/files/x"></iframe><img src=x onerror="alert(document.cookie)">`;
		const tokens = lex(src);
		const htmlToken = tokens.find((t) => t.type === 'html') as { text: string } | undefined;

		// A single html token carries both the legit iframe and the payload...
		expect(htmlToken).toBeDefined();
		expect(htmlToken!.text).toContain(IFRAME_GUARD); // ...so the iframe branch is entered
		expect(htmlToken!.text).toContain('onerror='); // ...while smuggling executable markup
	});

	it('vector 2: a block <iframe> line absorbs a <script> / svg onload into ONE html token', () => {
		const src = `<div><iframe src="/api/v1/files/x"></iframe><svg onload=alert(1)></svg><script>alert(2)</script></div>`;
		const tokens = lex(src);
		const htmlToken = tokens.find((t) => t.type === 'html') as { text: string } | undefined;

		expect(htmlToken).toBeDefined();
		expect(htmlToken!.text).toContain(IFRAME_GUARD);
		expect(htmlToken!.text).toContain('<script>');
		expect(htmlToken!.text).toContain('onload=');
	});
});

describe('sanitizeIframeHtml config', () => {
	it('re-allows only <iframe> and safe layout attributes (no script tag, no event handlers)', () => {
		expect(MARKDOWN_IFRAME_SANITIZE_CONFIG.ADD_TAGS).toContain('iframe');
		expect(MARKDOWN_IFRAME_SANITIZE_CONFIG.ADD_TAGS).not.toContain('script');

		const attrs = MARKDOWN_IFRAME_SANITIZE_CONFIG.ADD_ATTR as readonly string[];
		expect(attrs).toContain('src');
		// No event-handler attributes may be re-allowed.
		expect(attrs.some((a) => /^on/i.test(a))).toBe(false);
	});
});

describe.skipIf(!hasDom)('sanitizeIframeHtml behaviour (requires a DOM)', () => {
	it('vector 1: strips the smuggled onerror <img> but keeps the internal iframe', () => {
		const out = sanitizeIframeHtml(
			`<iframe src="/api/v1/files/x"></iframe><img src=x onerror="alert(document.cookie)">`
		);
		expect(out).toContain('<iframe');
		expect(out).toContain('/api/v1/files/x');
		expect(out.toLowerCase()).not.toContain('onerror');
	});

	it('vector 2: strips <script> and svg onload while keeping the iframe', () => {
		const out = sanitizeIframeHtml(
			`<div><iframe src="/api/v1/files/x"></iframe><svg onload=alert(1)></svg><script>alert(2)</script></div>`
		);
		expect(out).toContain('<iframe');
		expect(out.toLowerCase()).not.toContain('<script');
		expect(out.toLowerCase()).not.toContain('onload');
	});

	it('strips an event handler placed directly on the iframe itself', () => {
		const out = sanitizeIframeHtml(
			`<iframe src="/api/v1/files/x" onload="alert(document.cookie)">`
		);
		expect(out).toContain('<iframe');
		expect(out.toLowerCase()).not.toContain('onload');
	});
});

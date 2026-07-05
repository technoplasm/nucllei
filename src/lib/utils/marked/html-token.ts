import DOMPurify from 'dompurify';

/**
 * Sanitisation for marked `html` tokens that are allowed to embed an <iframe>
 * (used for internal file previews served from `/api/v1/files/.../content`).
 *
 * Historically these tokens were rendered with `{@html token.text}` — i.e. the
 * raw, model-controlled string was written straight into the DOM whenever it
 * merely *contained* the substring `<iframe src=".../api/v1/files/`. Because
 * `String.includes` matches anywhere in the token, an attacker could smuggle
 * arbitrary executable markup alongside the expected iframe, e.g.
 *
 *   <iframe src="/api/v1/files/x"></iframe><img src=x onerror="steal(token)">
 *   <iframe src="/api/v1/files/x" onload="steal(token)">
 *
 * leading to Stored XSS / account takeover (GHSA-9f4f-jv96-8766,
 * CVE-2025-46719).
 *
 * DOMPurify strips <iframe> by default, so we explicitly re-allow the <iframe>
 * tag plus the layout attributes a legitimate embed needs. DOMPurify still
 * removes <script>, event-handler attributes (onerror/onload/onclick/...) and
 * javascript: URLs, which is exactly the executable surface the CVE abused.
 */
export const MARKDOWN_IFRAME_SANITIZE_CONFIG = {
	ADD_TAGS: ['iframe'],
	ADD_ATTR: [
		'src',
		'width',
		'height',
		'frameborder',
		'title',
		'allow',
		'allowfullscreen',
		'loading',
		// llens(GHSA-8gh5-qqh8-hq3x): file プレビュー iframe の sandbox 属性を保持する。
		// これが除去されると枠内 HTML が same-origin で実行され Stored XSS→乗っ取りに繋がる。
		'sandbox'
	]
};

/**
 * Sanitise a marked `html` token that is permitted to contain an <iframe>.
 * Returns a string safe to pass to Svelte's `{@html ...}`.
 */
export const sanitizeIframeHtml = (html: string): string =>
	DOMPurify.sanitize(html ?? '', MARKDOWN_IFRAME_SANITIZE_CONFIG);

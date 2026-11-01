import { describe, it, expect } from 'vitest';
import DOMPurify from 'dompurify';
import { DIAGRAM_SVG_SANITIZE_CONFIG, sanitizeDiagramSvg } from '$lib/utils/diagram-svg';

// DOMPurify は DOM が要る。既定 (node) の vitest 環境には DOM が無いので、挙動の検査は
// DOM がある環境 (browser / jsdom / happy-dom) でだけ走る。設定の形の検査はどこでも走る。
const hasDom =
	typeof (DOMPurify as unknown as { sanitize?: unknown }).sanitize === 'function' &&
	(DOMPurify as unknown as { isSupported?: boolean }).isSupported === true;

// mermaid 11 (securityLevel 'strict') が flowchart のノードラベルに出す形
const label = (inner: string) =>
	`<svg xmlns="http://www.w3.org/2000/svg"><g class="label"><foreignObject width="80" height="24">` +
	`<div xmlns="http://www.w3.org/1999/xhtml" style="display: table-cell;">` +
	`<span class="nodeLabel">${inner}</span></div></foreignObject></g></svg>`;

describe('DIAGRAM_SVG_SANITIZE_CONFIG', () => {
	it('foreignObject とその中の HTML を通すだけで、実行面を開く設定を持たない', () => {
		expect(DIAGRAM_SVG_SANITIZE_CONFIG.ADD_TAGS).toEqual(['foreignobject']);
		expect(DIAGRAM_SVG_SANITIZE_CONFIG.HTML_INTEGRATION_POINTS).toEqual({ foreignobject: true });
		const keys = Object.keys(DIAGRAM_SVG_SANITIZE_CONFIG);
		for (const k of [
			'ALLOW_UNKNOWN_PROTOCOLS',
			'ADD_URI_SAFE_ATTR',
			'FORCE_BODY',
			'ALLOWED_URI_REGEXP'
		]) {
			expect(keys).not.toContain(k);
		}
		expect(DIAGRAM_SVG_SANITIZE_CONFIG.ADD_ATTR.some((a) => a.startsWith('on'))).toBe(false);
	});
});

describe.skipIf(!hasDom)('sanitizeDiagramSvg (DOM)', () => {
	it('ラベルの foreignObject と中の HTML が残る', () => {
		const out = sanitizeDiagramSvg(label('<p>開始</p>'));
		expect(out).toMatch(/<foreignObject/i);
		expect(out).toContain('<p>開始</p>');
		expect(out).toContain('class="nodeLabel"');
	});

	it('foreignObject の中の script・on* ハンドラ・javascript: URL は除去される', () => {
		const out = sanitizeDiagramSvg(
			label(
				'<img src=x onerror="alert(1)"><script>alert(2)</script>' +
					'<a href="javascript:alert(3)">x</a><div onclick="alert(4)">y</div>'
			)
		);
		expect(out).not.toMatch(/onerror|onclick|<script|javascript:/i);
	});

	it('SVG 側の script・onload も除去される', () => {
		const out = sanitizeDiagramSvg(
			'<svg onload="alert(1)"><script>alert(2)</script><a href="javascript:alert(3)"><text>t</text></a></svg>'
		);
		expect(out).not.toMatch(/onload|<script|javascript:/i);
	});
});

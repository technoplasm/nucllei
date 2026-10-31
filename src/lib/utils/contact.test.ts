import { describe, expect, it } from 'vitest';
import { isContactEmail } from './contact';

describe('isContactEmail', () => {
	it('普通のアドレスは mailto にする', () => {
		expect(isContactEmail('helpdesk@example.org')).toBe(true);
		expect(isContactEmail('it.support+llens@med.hokudai.ac.jp')).toBe(true);
	});

	it('アドレスの形でない値は平文に落とす', () => {
		expect(isContactEmail('')).toBe(false);
		expect(isContactEmail('医療情報部 内線 1234')).toBe(false);
		expect(isContactEmail('helpdesk@localhost')).toBe(false);
		expect(isContactEmail('a@b@example.org')).toBe(false);
		expect(isContactEmail(' helpdesk@example.org')).toBe(false);
	});

	it('mailto のヘッダに化ける文字は通さない', () => {
		expect(isContactEmail('a@example.org?subject=x')).toBe(false);
		expect(isContactEmail('a@example.org&cc=b@example.org')).toBe(false);
		expect(isContactEmail('a%40b@example.org')).toBe(false);
		expect(isContactEmail('javascript:alert(1)@example.org')).toBe(false);
	});
});

// 能力の行は nucllei が読むキーだけを出し、保存はそのキー (+ 行に出さない continuous_usage) だけを書く

import { describe, it, expect } from 'vitest';
import {
	CAPABILITY_DEFAULTS,
	CAPABILITY_KEYS,
	capabilitiesToSave,
	editableCapabilities
} from './model-capabilities';

describe('editableCapabilities', () => {
	it('保存済みが無ければ既定値', () => {
		expect(editableCapabilities(undefined)).toEqual(CAPABILITY_DEFAULTS);
		expect(editableCapabilities(null)).toEqual(CAPABILITY_DEFAULTS);
	});

	it('既知のキーは保存済みの値、未知のキー (citations 等) と行に出さないキーは出さない', () => {
		const shown = editableCapabilities({
			vision: true,
			usage: false,
			citations: true,
			continuous_usage: false
		});
		expect(shown).toEqual({ vision: true, audio: false, usage: false, preserve_reasoning: true });
		expect(Object.keys(shown)).toEqual(CAPABILITY_KEYS);
	});

	it('真偽値でない値は既定値', () => {
		expect(editableCapabilities({ usage: null, audio: 'yes' })).toEqual(CAPABILITY_DEFAULTS);
	});
});

describe('capabilitiesToSave', () => {
	it('未知のキーを落とし、continuous_usage の保存済みの値は残す', () => {
		const stored = { vision: false, citations: true, continuous_usage: false };
		const edited = { ...editableCapabilities(stored), vision: true };
		expect(capabilitiesToSave(edited, stored)).toEqual({
			vision: true,
			audio: false,
			usage: true,
			preserve_reasoning: true,
			continuous_usage: false
		});
	});

	it('continuous_usage が保存されていなければ書かない', () => {
		expect(capabilitiesToSave({ ...CAPABILITY_DEFAULTS }, { citations: true })).toEqual(
			CAPABILITY_DEFAULTS
		);
	});
});

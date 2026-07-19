// llens(#25): backend ruff(E9,F = 正しさ系のみ)と同じ哲学で再有効化。
// 型の厳しさ(any / Function 型)は svelte-check(0 errors 運用)が担うので lint では見ない。
// style は prettier が担う。ここで gate するのは「死にコード・実行時バグの芽」だけ。
module.exports = {
	root: true,
	extends: [
		'eslint:recommended',
		'plugin:@typescript-eslint/recommended',
		'plugin:svelte/recommended',
		'prettier'
	],
	parser: '@typescript-eslint/parser',
	plugins: ['@typescript-eslint'],
	parserOptions: {
		sourceType: 'module',
		ecmaVersion: 2020,
		extraFileExtensions: ['.svelte']
	},
	env: {
		browser: true,
		es2017: true,
		node: true
	},
	rules: {
		// any は意図的に多用(props/イベント payload)。型正しさは svelte-check の守備範囲
		'@typescript-eslint/no-explicit-any': 'off',
		// Function / {} 型を props で意図的に使っている(厳格化は #70 型付けの守備範囲)
		'@typescript-eslint/ban-types': 'off',
		// `_` プレフィクスは「意図的に未使用」の規約(_raw / _u 等)
		'@typescript-eslint/no-unused-vars': [
			'error',
			{ argsIgnorePattern: '^_', varsIgnorePattern: '^_' }
		],
		// while (true) のストリーム読みループは正当(SSE / ollama pull 進捗等)
		'no-constant-condition': ['error', { checkLoops: false }],
		// 空 catch は「失敗してよい」の明示(localStorage parse 等)
		'no-empty': ['error', { allowEmptyCatch: true }],
		// テンプレートリテラル内の全角空白等は CJK テストデータとして正当
		'no-irregular-whitespace': ['error', { skipTemplates: true }]
	},
	overrides: [
		{
			files: ['*.svelte'],
			parser: 'svelte-eslint-parser',
			parserOptions: {
				parser: '@typescript-eslint/parser'
			}
		},
		{
			// TS の未定義識別子はコンパイラ(svelte-check/tsc)が検出する。eslint の
			// no-undef は新しめの DOM 型(WakeLockSentinel 等)を知らず誤検出するため
			// TS 系ファイルでは切る(typescript-eslint 公式の推奨)
			files: ['*.ts', '*.svelte'],
			rules: {
				'no-undef': 'off'
			}
		}
	]
};

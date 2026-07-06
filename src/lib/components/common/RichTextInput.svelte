<script lang="ts">
	import { render } from '$lib/utils/markdown-it';
	import TurndownService from 'turndown';
	const turndownService = new TurndownService({
		codeBlockStyle: 'fenced',
		headingStyle: 'atx'
	});
	turndownService.escape = (string) => string;

	import { onMount, onDestroy } from 'svelte';
	import { createEventDispatcher } from 'svelte';
	const eventDispatch = createEventDispatcher();

	import { EditorState, Plugin, PluginKey, TextSelection } from 'prosemirror-state';
	import { Decoration, DecorationSet } from 'prosemirror-view';

	import { Editor } from '@tiptap/core';

	import { AIAutocompletion } from './RichTextInput/AutoCompletion.js';

	import CodeBlockLowlight from '@tiptap/extension-code-block-lowlight';
	import Placeholder from '@tiptap/extension-placeholder';
	import Highlight from '@tiptap/extension-highlight';
	import Typography from '@tiptap/extension-typography';
	import StarterKit from '@tiptap/starter-kit';
	import { all, createLowlight } from 'lowlight';

	import { PASTED_TEXT_CHARACTER_LIMIT } from '$lib/constants';
	import { SKILL_COMMAND_PATTERN } from '$lib/utils/skills';

	export let oncompositionstart = (e) => {};
	export let oncompositionend = (e) => {};

	// llens(issue #35): 有効な /command トークンをハイライトするための集合。
	// ハードコードせず MessageInput から $skills 由来の command 配列を受け取る。
	export let validCommands: string[] = [];

	// create a lowlight instance with all languages loaded
	const lowlight = createLowlight(all);

	export let className = 'input-prose';
	export let placeholder = 'Type here...';
	export let value = '';
	export let id = '';

	export let raw = false;

	export let preserveBreaks = false;
	export let generateAutoCompletion: Function = async () => null;
	export let autocomplete = false;
	export let messageInput = false;
	export let shiftEnter = false;
	export let largeTextAsFile = false;

	let element;
	let editor;

	const options = {
		throwOnError: false
	};

	// llens(issue #35): /command ハイライト用 ProseMirror プラグイン。
	// 装飾は毎トランザクション(= 打鍵ごと)に再計算される。valid command 集合は
	// closure(validCommandsRef)経由で常に最新を参照する。
	const skillCommandPluginKey = new PluginKey('skillCommandHighlight');
	let validCommandsRef: string[] = validCommands ?? [];

	// doc の「先頭パラグラフの行頭」が /command パターンに一致し、かつ有効 command 集合に
	// 含まれる場合だけ、そのトークン範囲に inline decoration を付ける。
	// 先頭ブロックが paragraph 以外(見出し・コードブロック等)なら装飾しない
	// (= サーバの素通しと一致。生 markdown の先頭が `/` にならないため)。
	function computeSkillDecorations(doc, commandSet: string[]) {
		const first = doc.firstChild;
		if (!first || first.type.name !== 'paragraph') {
			return DecorationSet.empty;
		}
		const match = first.textContent.match(SKILL_COMMAND_PATTERN);
		if (!match || !commandSet.includes(match[1])) {
			return DecorationSet.empty;
		}
		// 先頭パラグラフの本文は doc 位置 1 から始まる。
		return DecorationSet.create(doc, [
			Decoration.inline(1, 1 + match[0].length, { class: 'skill-command-token' })
		]);
	}

	// Function to find the next template in the document
	function findNextTemplate(doc, from = 0) {
		const patterns = [{ start: '{{', end: '}}' }];

		let result = null;

		doc.nodesBetween(from, doc.content.size, (node, pos) => {
			if (result) return false; // Stop if we've found a match
			if (node.isText) {
				const text = node.text;
				let index = Math.max(0, from - pos);
				while (index < text.length) {
					for (const pattern of patterns) {
						if (text.startsWith(pattern.start, index)) {
							const endIndex = text.indexOf(pattern.end, index + pattern.start.length);
							if (endIndex !== -1) {
								result = {
									from: pos + index,
									to: pos + endIndex + pattern.end.length
								};
								return false; // Stop searching
							}
						}
					}
					index++;
				}
			}
		});

		return result;
	}

	// Function to select the next template in the document
	function selectNextTemplate(state, dispatch) {
		const { doc, selection } = state;
		const from = selection.to;
		let template = findNextTemplate(doc, from);

		if (!template) {
			// If not found, search from the beginning
			template = findNextTemplate(doc, 0);
		}

		if (template) {
			if (dispatch) {
				const tr = state.tr.setSelection(TextSelection.create(doc, template.from, template.to));
				dispatch(tr);
			}
			return true;
		}
		return false;
	}

	export const setContent = (content) => {
		editor.commands.setContent(content);
	};

	const selectTemplate = () => {
		if (value !== '') {
			// After updating the state, try to find and select the next template
			setTimeout(() => {
				const templateFound = selectNextTemplate(editor.view.state, editor.view.dispatch);
				if (!templateFound) {
					// If no template found, set cursor at the end
					const endPos = editor.view.state.doc.content.size;
					editor.view.dispatch(
						editor.view.state.tr.setSelection(TextSelection.create(editor.view.state.doc, endPos))
					);
				}
			}, 0);
		}
	};

	onMount(async () => {
		console.log(value);

		if (preserveBreaks) {
			turndownService.addRule('preserveBreaks', {
				filter: 'br', // Target <br> elements
				replacement: function (content) {
					return '<br/>';
				}
			});
		}

		let content = value;

		if (!raw) {
			async function tryParse(value, attempts = 3, interval = 100) {
				try {
					// Try parsing the value
					return render(value.replaceAll(`\n<br/>`, `<br/>`));
				} catch (error) {
					// If no attempts remain, fallback to plain text
					if (attempts <= 1) {
						return value;
					}
					// Wait for the interval, then retry
					await new Promise((resolve) => setTimeout(resolve, interval));
					return tryParse(value, attempts - 1, interval); // Recursive call
				}
			}

			// Usage example
			content = await tryParse(value);
		}

		editor = new Editor({
			element: element,
			extensions: [
				StarterKit,
				CodeBlockLowlight.configure({
					lowlight
				}),
				Highlight,
				Typography,
				Placeholder.configure({ placeholder }),
				...(autocomplete
					? [
							AIAutocompletion.configure({
								generateCompletion: async (text) => {
									if (text.trim().length === 0) {
										return null;
									}

									const suggestion = await generateAutoCompletion(text).catch(() => null);
									if (!suggestion || suggestion.trim().length === 0) {
										return null;
									}

									return suggestion;
								}
							})
						]
					: [])
			],
			content: content,
			autofocus: messageInput ? true : false,
			onTransaction: ({ transaction }) => {
				// force re-render so `editor.isActive` works as expected
				editor = editor;

				// doc \u304c\u5909\u308f\u3063\u3066\u3044\u306a\u3044\u30c8\u30e9\u30f3\u30b6\u30af\u30b7\u30e7\u30f3(focus/selection \u7b49)\u3067\u306f value \u3092
				// \u66f8\u304d\u623b\u3055\u306a\u3044\u3002\u66f8\u304d\u623b\u3059\u3068\u300c\u5916\u304b\u3089 value='' \u304c\u8a2d\u5b9a\u3055\u308c\u305f\u76f4\u5f8c\u30fb\u53cd\u6620\u524d\u300d\u306b
				// \u53e4\u3044 editor \u5185\u5bb9\u3067 value \u3092\u4e0a\u66f8\u304d\u3057\u8fd4\u3057\u3001\u9001\u4fe1\u5f8c\u306b\u5165\u529b\u6b04\u304c\u30af\u30ea\u30a2\u3055\u308c\u306a\u3044
				// (\u521d\u56de\u9001\u4fe1\u306e Placeholder\u2192MessageInput \u5207\u66ff\u6642\u306b focus \u7531\u6765\u3067\u9855\u5728\u5316)\u3002
				if (!transaction.docChanged) {
					return;
				}

				if (!raw) {
					let newValue = turndownService
						.turndown(
							editor
								.getHTML()
								.replace(/<p><\/p>/g, '<br/>')
								.replace(/ {2,}/g, (m) => m.replace(/ /g, '\u00a0'))
						)
						.replace(/\u00a0/g, ' ');

					if (!preserveBreaks) {
						newValue = newValue.replace(/<br\/>/g, '');
					}

					if (value !== newValue) {
						value = newValue;

						// check if the node is paragraph as well
						if (editor.isActive('paragraph')) {
							if (value === '') {
								editor.commands.clearContent();
							}
						}
					}
				} else {
					value = editor.getHTML();
				}
			},
			editorProps: {
				attributes: { id },
				handleDOMEvents: {
					compositionstart: (view, event) => {
						oncompositionstart(event);
						return false;
					},
					compositionend: (view, event) => {
						oncompositionend(event);
						return false;
					},
					focus: (view, event) => {
						eventDispatch('focus', { event });
						return false;
					},
					keyup: (view, event) => {
						eventDispatch('keyup', { event });
						return false;
					},
					keydown: (view, event) => {
						if (messageInput) {
							// Handle Tab Key
							if (event.key === 'Tab') {
								const handled = selectNextTemplate(view.state, view.dispatch);
								if (handled) {
									event.preventDefault();
									return true;
								}
							}

							if (event.key === 'Enter') {
								// Check if the current selection is inside a structured block (like codeBlock or list)
								const { state } = view;
								const { $head } = state.selection;

								// Recursive function to check ancestors for specific node types
								function isInside(nodeTypes: string[]): boolean {
									let currentNode = $head;
									while (currentNode) {
										if (nodeTypes.includes(currentNode.parent.type.name)) {
											return true;
										}
										if (!currentNode.depth) break; // Stop if we reach the top
										currentNode = state.doc.resolve(currentNode.before()); // Move to the parent node
									}
									return false;
								}

								const isInCodeBlock = isInside(['codeBlock']);
								const isInList = isInside(['listItem', 'bulletList', 'orderedList']);
								const isInHeading = isInside(['heading']);

								if (isInCodeBlock || isInList || isInHeading) {
									// Let ProseMirror handle the normal Enter behavior
									return false;
								}
							}

							// Handle shift + Enter for a line break
							if (shiftEnter) {
								if (event.key === 'Enter' && event.shiftKey && !event.ctrlKey && !event.metaKey) {
									editor.commands.setHardBreak(); // Insert a hard break
									view.dispatch(view.state.tr.scrollIntoView()); // Move viewport to the cursor
									event.preventDefault();
									return true;
								}
							}
						}
						eventDispatch('keydown', { event });
						return false;
					},
					paste: (view, event) => {
						if (event.clipboardData) {
							// Extract plain text from clipboard and paste it without formatting
							const plainText = event.clipboardData.getData('text/plain');
							if (plainText) {
								if (largeTextAsFile) {
									if (plainText.length > PASTED_TEXT_CHARACTER_LIMIT) {
										// Dispatch paste event to parent component
										eventDispatch('paste', { event });
										event.preventDefault();
										return true;
									}
								}
								return false;
							}

							// Check if the pasted content contains image files
							const hasImageFile = Array.from(event.clipboardData.files).some((file) =>
								file.type.startsWith('image/')
							);

							// Check for image in dataTransfer items (for cases where files are not available)
							const hasImageItem = Array.from(event.clipboardData.items).some((item) =>
								item.type.startsWith('image/')
							);
							if (hasImageFile) {
								// If there's an image, dispatch the event to the parent
								eventDispatch('paste', { event });
								event.preventDefault();
								return true;
							}

							if (hasImageItem) {
								// If there's an image item, dispatch the event to the parent
								eventDispatch('paste', { event });
								event.preventDefault();
								return true;
							}
						}

						// For all other cases (text, formatted text, etc.), let ProseMirror handle it
						view.dispatch(view.state.tr.scrollIntoView()); // Move viewport to the cursor after pasting
						return false;
					}
				}
			}
		});

		// /command ハイライトのプラグインを登録(issue #35)。
		editor.registerPlugin(
			new Plugin({
				key: skillCommandPluginKey,
				props: {
					decorations: (state) => computeSkillDecorations(state.doc, validCommandsRef)
				}
			})
		);

		if (messageInput) {
			selectTemplate();
		}
	});

	onDestroy(() => {
		if (editor) {
			editor.destroy();
		}
	});

	// Update the editor content if the external `value` changes
	$: if (
		editor &&
		(raw
			? value !== editor.getHTML()
			: value !==
				turndownService
					.turndown(
						(preserveBreaks
							? editor.getHTML().replace(/<p><\/p>/g, '<br/>')
							: editor.getHTML()
						).replace(/ {2,}/g, (m) => m.replace(/ /g, '\u00a0'))
					)
					.replace(/\u00a0/g, ' '))
	) {
		if (raw) {
			editor.commands.setContent(value);
		} else {
			preserveBreaks
				? editor.commands.setContent(value)
				: editor.commands.setContent(render(value.replaceAll(`\n<br/>`, `<br/>`))); // Update editor content
		}

		selectTemplate();
	}

	// valid command 集合が変わったら装飾を再計算(skills の遅延ロードにも追従)。
	// editor の再代入(onTransaction の `editor = editor`)では発火しないよう、
	// 集合の内容が変化したときだけ no-op transaction を投げる(無限ループ防止)。
	let lastValidCommandsKey = '';
	$: {
		const key = (validCommands ?? []).join(',');
		if (editor && key !== lastValidCommandsKey) {
			lastValidCommandsKey = key;
			validCommandsRef = validCommands ?? [];
			editor.view.dispatch(editor.view.state.tr.setMeta(skillCommandPluginKey, true));
		}
	}
</script>

<div bind:this={element} class="relative w-full min-w-full h-full min-h-fit {className}" />

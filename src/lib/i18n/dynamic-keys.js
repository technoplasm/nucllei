// i18next-parser の動的キー登録(comment hints)。
//
// `$i18n.t(tab.label)` / `t(row.label)` のように変数経由で参照されるキーは
// 静的解析で検出できず、`pnpm i18n:parse`(keepRemoved: false)が prune してしまう。
// このファイルのコメント内の t(キー) 呼び出しをパーサが拾うことで、動的キーを生かしておく。
// 動的な t() 呼び出しにキーを追加・削除したら、ここも追従させること。
//
// また parser の input は `src/**/*.{js,svelte}` で **.ts を含まない**ため、
// .ts 内の静的な t() 呼び出しのキーもここに載せる必要がある
// (現状: ResponseMessage/tts.svelte.ts)。
//
// 参照元(2026-07-17 棚卸し):
// - SettingsModal.svelte: TABS の label
// - workspace/+layout.svelte: WORKSPACE_TABS の label
// - workspace/settings/+page.svelte: TABS の label
// - workspace/users/+page.svelte: TABS / SORTABLE_COLUMNS の label、t(user.role)(admin/user/pending)
// - Settings/{General,Interface,Chats}.svelte: ROWS/SECTIONS の label / option.label / section.title
// - chat/ShortcutsModal.svelte: SHORTCUT_COLUMNS / COMMAND_ROWS の label
// - chat/ModelSelector/Selector.svelte: タブ(All / Local / External)
// - workspace/models/FunctionsSelector.svelte: section.label(Filter / Action)
// - workspace/models/Capabilities.svelte: t(capability)(vision / usage / citations)
// - workspace/settings/Audio.svelte: TTS split(値の先頭を大文字化して t() に渡す)
// - layout/Sidebar.svelte: t(chat.time_range)(getTimeRange の固定 4 値。月名・年は
//   toLocaleString 由来のロケール依存ランタイム値なので登録しない)
// - chat/Messages/ResponseMessage/tts.svelte.ts: 静的 t() だが .ts のため parser が読まない
//
// ※ AdvancedParams.svelte / workspace/functions/+page.svelte のラベルは t() を通さず
//   生表示のため登録しない。

// t('Account')
// t('Action')
// t('All')
// t('Allow Voice Interruption in Call')
// t('Audio')
// t('Auto')
// t('Chat')
// t('Chat Bubble UI')
// t('Chat direction')
// t('Chats')
// t('Connections')
// t('Copy Formatted Text')
// t('Copy last code block')
// t('Copy last response')
// t('Created at')
// t('Ctrl+Enter to Send')
// t('Data')
// t('Database')
// t('Default')
// t('Delete chat')
// t('Display the username instead of You in the Chat')
// t('Email')
// t('Enable Call')
// t('Enter Key Behavior')
// t('Enter to Send')
// t('Expand code blocks in responses')
// t('Expand reasoning and tool calls')
// t('External')
// t('File')
// t('Files')
// t('Filter')
// t('Focus chat input')
// t('Functions')
// t('General')
// t('Generate prompt pair')
// t('Groups')
// t('Haptic Feedback')
// t('Interface')
// t('Jump to prompt variable')
// t('LTR')
// t('Landing Page Mode')
// t('Language')
// t('Last Active')
// t('License')
// t('Local')
// t('Models')
// t('Name')
// t('No content to speak')
// t('None')
// t('Notification Sound')
// t('Notifications')
// t('Open new chat')
// t('Paragraphs')
// t('Pipelines')
// t('Previous 30 days')
// t('Previous 7 days')
// t('Punctuation')
// t('RTL')
// t('Role')
// t('Scroll to bottom when switching between branches')
// t('Settings')
// t('Show shortcuts')
// t('Skills')
// t('Talk to model')
// t('Theme')
// t('Title Auto-Generation')
// t('Today')
// t('Toggle settings')
// t('Toggle sidebar')
// t('Tools')
// t('Trigger a skill')
// t('Users')
// t('Voice')
// t('Widescreen Mode')
// t('Yesterday')
// t('admin')
// t('citations')
// t('pending')
// t('usage')
// t('user')
// t('vision')

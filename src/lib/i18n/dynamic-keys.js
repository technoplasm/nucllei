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
// - routes/(app)/chat/Chat/{send,events}.ts / MessageInput/uploads.ts(#70 分割): 同上。
//   Chat.svelte / MessageInput.svelte から .ts へ移った静的 t() — 分割時に登録漏れで
//   生きた訳が prune されかけた(2026-07-19 検出)。.svelte → .ts の移動時はここへ追従
//
// - workspace/functions/+page.svelte: group.label(Attachment / Filter / Action / Pipe / Other)
// - workspace/models/AdvancedParams.svelte: section.title(Behavior / Sampling / Mirostat / Ollama)と
//   cycle の states の label(Default / Native / On / Off)。パラメータのキー名(item.key)は
//   システムへ渡す名前をそのまま見せる設計なので t() を通さない(#117)。
//   input 型の item.placeholder(Enter reasoning effort / Enter seed / Enter stop sequence /
//   logit_bias の例)
// - workspace/functions/FunctionEditor.svelte: 編集時バッジ TYPE_LABELS(Attachment / Filter /
//   Action / Pipe — 一覧の group.label と同じキー)

// t('Account')
// t('Action')
// t('All')
// t('Allow Voice Interruption in Call')
// t('Attachment')
// t('Audio')
// t('Auto')
// t('Behavior')
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
// t('Enter comma-separated "token:bias_value" pairs (example: 5432:100, 413:-100)')
// t('Enter Key Behavior')
// t('Enter reasoning effort')
// t('Enter seed')
// t('Enter stop sequence')
// t('Enter to Send')
// t('Expand code blocks in responses')
// t('Expand reasoning and tool calls')
// t('External')
// t('Failed to save the chat.')
// t('Failed to save the chat. Your message was restored to the input.')
// t('File size should not exceed {{maxSize}} MB.')
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
// t('Minimum Voice Segment Length (seconds)')
// t('Mirostat')
// t('Model {{modelId}} not found')
// t('Model {{modelName}} is not vision capable')
// t('Model not selected')
// t('Models')
// t('Name')
// t('Native')
// t('New Chat')
// t('No content to speak')
// t('None')
// t('Notification Sound')
// t('Notifications')
// t('Off')
// t('Ollama')
// t('On')
// t('Oops! There are files still uploading. Please wait for the upload to complete.')
// t('Oops! There was an error in the previous response.')
// t('Open new chat')
// t('Other')
// t('Paragraphs')
// t('Pipe')
// t('Pipelines')
// t('Please enter a prompt')
// t('Previous 30 days')
// t('Previous 7 days')
// t('Punctuation')
// t('RTL')
// llens(2026-08-14): Chat.svelte 観測モードバナーの `({t('Read-only')})` は
// 静的呼び出しだが JavascriptLexer が拾えない(parse すると prune される)ためここで生かす
// t('Read-only')
// t('Role')
// t('Sampling')
// t('Scroll to bottom when switching between branches')
// t('Selected model(s) do not support image inputs')
// t('Settings')
// t('Show shortcuts')
// t('Silence Before Transcribing (seconds)')
// t('Skills')
// t('Talk to model')
// t('Theme')
// t('Title Auto-Generation')
// t('Today')
// t('Toggle settings')
// t('Toggle sidebar')
// t('Tools')
// t('Trigger a skill')
// t('Uh-oh! There was an issue with the response.')
// t('Users')
// t('Voice')
// t('Widescreen Mode')
// t('Yesterday')
// t('You can only chat with a maximum of {{maxCount}} file(s) at a time.')
// t('You cannot upload an empty file.')
// t('You do not have permission to upload files.')
// t('admin')
// t('citations')
// t('pending')
// t('usage')
// t('user')
// t('vision')

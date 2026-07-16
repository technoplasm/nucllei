// i18next-parser の動的キー登録(comment hints)。
//
// `$i18n.t(tab.label)` / `t(row.label)` のように変数経由で参照されるキーは
// 静的解析で検出できず、`pnpm i18n:parse`(keepRemoved: false)が prune してしまう。
// このファイルのコメント内の t(キー) 呼び出しをパーサが拾うことで、動的キーを生かしておく。
// 動的な t() 呼び出しにキーを追加・削除したら、ここも追従させること。
//
// 主な参照元:
// - SettingsModal.svelte / workspace/settings/+page.svelte / workspace/+layout.svelte /
//   workspace/users/+page.svelte: タブ・カラムの label
// - Settings/{General,Interface,Chats}.svelte: 設定行の label / option.label
// - workspace/models/FunctionsSelector.svelte: section.label
// - Audio.svelte: TTS split(値の先頭を大文字化して t() に渡す)

// t('Account')
// t('Action')
// t('Allow Voice Interruption in Call')
// t('Always Collapse Code Blocks')
// t('Always Expand Details')
// t('Audio')
// t('Auto')
// t('Auto-Copy Response to Clipboard')
// t('Beta')
// t('Chat Bubble UI')
// t('Chat direction')
// t('Connections')
// t('Copy Formatted Text')
// t('Ctrl+Enter to Send')
// t('Display the username instead of You in the Chat')
// t('Documents')
// t('Enter Key Behavior')
// t('Enter Seed')
// t('Enter comma-seperated "token:bias_value" pairs (example: 5432:100, 413:-100)')
// t('Enter reasoning effort')
// t('Enter stop sequence')
// t('Enter to Send')
// t('Filter')
// t('Haptic Feedback')
// t('Home')
// t('Image Compression')
// t('Images')
// t('LTR')
// t('Landing Page Mode')
// t('Language')
// t('Last Active')
// t('License')
// t('Mirostat')
// t('Native')
// t('Notification Sound')
// t('Notifications')
// t('Off')
// t('On')
// t('Other')
// t('Paste Large Text as File')
// t('Pipe')
// t('Pipelines')
// t('RTL')
// t('Rich Text Input for Chat')
// t('Scroll to bottom when switching between branches')
// t('Theme')
// t('Title Auto-Generation')
// t('Voice')
// t('Widescreen Mode')
// t('iframe Sandbox Allow Forms')
// t('iframe Sandbox Allow Same Origin')
// t('Punctuation')
// t('Paragraphs')

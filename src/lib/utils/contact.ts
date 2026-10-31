// 配備側の問い合わせ先メール(WEBUI_CONTACT_EMAIL)を mailto リンクにしてよいかの判定。
// 形が崩れた値は平文で出す。`?` `&` `#` `%` などを通すと mailto のヘッダ(subject= 等)に
// 化けるので、ローカル部・ドメインとも通常の英数字と記号だけに絞る。
const EMAIL_SHAPE = /^[A-Za-z0-9._+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$/;

export const isContactEmail = (value: string): boolean => EMAIL_SHAPE.test(value);

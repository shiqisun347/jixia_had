import type { MessageShape, zhCN } from './messages';

declare module 'next-intl' {
  interface AppConfig {
    Locale: 'zh-CN' | 'en';
    Messages: MessageShape<typeof zhCN>;
  }
}

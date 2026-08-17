import type { Preview } from '@storybook/nextjs-vite';
import { mswLoader } from 'msw-storybook-addon/csf3';
import { createElement } from 'react';

import '../src/app/globals.css';
import { ToastProvider } from '../src/components/ui/toast-provider';
import { handlers } from '../src/mocks/handlers';
import { AuthProvider } from '../src/features/auth';

/**
 * The preview uses CSF 3, so the v3 MSW addon loader is the compatible
 * integration. Story-specific handlers can be supplied through
 * `parameters.msw` and are reset between stories by the loader.
 */
const preview: Preview = {
  decorators: [
    (Story, context) =>
      createElement(
        ToastProvider,
        {
          pathnameOverride:
            typeof context.parameters.toastPath === 'string'
              ? context.parameters.toastPath
              : undefined,
        },
        createElement(AuthProvider, null, createElement(Story)),
      ),
  ],
  loaders: [mswLoader()],
  parameters: {
    layout: 'fullscreen',
    msw: { handlers },
    a11y: {
      // Accessibility failures are visible in Storybook and fail component
      // tests instead of being silently ignored.
      test: 'error',
    },
    controls: {
      matchers: {
        color: /(background|color)$/i,
        date: /Date$/i,
      },
      // Stories are the source of truth; controls are for exploration only.
      disableSaveFromUI: true,
    },
    viewport: {
      options: {
        reference: {
          name: '参考画布 · 1672×941',
          styles: { width: '1672px', height: '941px' },
          type: 'desktop',
        },
        primary: {
          name: '主桌面 · 1440×900',
          styles: { width: '1440px', height: '900px' },
          type: 'desktop',
        },
        compact: {
          name: '小桌面 · 1280×720',
          styles: { width: '1280px', height: '720px' },
          type: 'desktop',
        },
        wide: {
          name: '大桌面 · 1920×1080',
          styles: { width: '1920px', height: '1080px' },
          type: 'desktop',
        },
      },
    },
  },
};

export default preview;

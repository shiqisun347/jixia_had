import type { StorybookConfig } from '@storybook/nextjs-vite';

/**
 * Storybook is intentionally scoped to the web package. It renders the pure
 * prototype components and never starts Next.js, jx-core, PostgreSQL, or
 * LiveKit.
 */
const config: StorybookConfig = {
  stories: ['../src/**/*.stories.@(ts|tsx)'],
  framework: '@storybook/nextjs-vite',
  addons: ['@storybook/addon-a11y', '@storybook/addon-vitest'],
  staticDirs: ['../public'],
  core: {
    // Keep the local prototype deterministic and do not send usage telemetry.
    disableTelemetry: true,
  },
  viteFinal: async (viteConfig) => ({
    ...viteConfig,
    optimizeDeps: {
      ...viteConfig.optimizeDeps,
      // Avoid the first-run lucide/framer dependency optimization reload that
      // can make browser-mode story tests flaky on a cold cache.
      include: Array.from(
        new Set([...(viteConfig.optimizeDeps?.include ?? []), 'lucide-react', 'framer-motion']),
      ),
    },
  }),
};

export default config;

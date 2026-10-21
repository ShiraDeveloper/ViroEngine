import type { Config } from 'tailwindcss';

import { tokens } from './src/design/tokens';

/** Theme is generated from src/design/tokens.ts (PLAN §9.2). Line height 1.5 for Hebrew prose. */
const fontSize = Object.fromEntries(
  Object.entries(tokens.fontSize).map(([key, size]) => [key, [size, { lineHeight: '1.5' }]]),
) as Record<string, [string, { lineHeight: string }]>;

const config: Config = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    colors: {
      transparent: 'transparent',
      current: 'currentColor',
      ...tokens.colors,
    },
    fontFamily: {
      sans: [...tokens.fontFamily.sans],
      mono: [...tokens.fontFamily.mono],
    },
    fontSize,
    borderRadius: { none: '0', full: '9999px', ...tokens.borderRadius },
    boxShadow: { none: 'none', ...tokens.boxShadow },
    extend: {
      spacing: tokens.spacing,
      transitionDuration: {
        fast: tokens.motion.fast,
        base: tokens.motion.base,
        slow: tokens.motion.slow,
      },
      transitionTimingFunction: { token: tokens.motion.ease },
    },
  },
  plugins: [],
};

export default config;

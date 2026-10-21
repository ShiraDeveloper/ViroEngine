/**
 * Design tokens (docs/PLAN.md §9.2). Together with tailwind.config.ts this is the only place a
 * literal colour may appear; ESLint enforces it everywhere else.
 */
export const tokens = {
  colors: {
    primary: { 900: '#1C1B4D', 700: '#2B2A6B', 500: '#454399', 300: '#8785C6', 100: '#E7E6F7' },
    accent: { 700: '#9E1049', 500: '#C2185B', 100: '#FBE4EE' },
    surface: { base: '#F6F7FB', raised: '#FFFFFF', sunken: '#EEF0F7', border: '#E2E4EE' },
    ink: { strong: '#1A1C2A', base: '#3A3D52', muted: '#6B6F85', inverse: '#FFFFFF' },
    state: {
      verified: { fg: '#1B5E20', bg: '#E8F5E9', border: '#2E7D32' }, // level A/B
      single: { fg: '#7A4700', bg: '#FFF6E5', border: '#B26A00' }, // level C
      review: { fg: '#B33F00', bg: '#FFE8D6', border: '#E65100' }, // level D
      neutral: { fg: '#3A3D52', bg: '#EEF0F7', border: '#C9CBD6' },
    },
    cloud: '#C2185B', // anything that left the machine
    local: '#2E7D32', // anything resolved on the machine
  },
  fontFamily: {
    sans: ['Heebo', 'system-ui', 'sans-serif'],
    mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
  },
  fontSize: {
    xs: '12px',
    sm: '14px',
    base: '16px',
    lg: '18px',
    xl: '22px',
    '2xl': '28px',
    '3xl': '36px',
  },
  spacing: {
    1: '4px',
    2: '8px',
    3: '12px',
    4: '16px',
    5: '24px',
    6: '32px',
    7: '48px',
    8: '64px',
  },
  borderRadius: { sm: '6px', md: '10px', lg: '14px', pill: '999px' },
  boxShadow: {
    card: '0 1px 2px rgba(26,28,42,.06), 0 2px 8px rgba(26,28,42,.04)',
    raised: '0 4px 16px rgba(26,28,42,.10)',
    focus: '0 0 0 3px rgba(69,67,153,.35)',
  },
  motion: { fast: '120ms', base: '180ms', slow: '260ms', ease: 'cubic-bezier(.2,.8,.2,1)' },
} as const;

export type Tokens = typeof tokens;

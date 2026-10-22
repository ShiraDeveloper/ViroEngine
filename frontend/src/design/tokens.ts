/**
 * Design tokens (docs/PLAN.md §9.2). Together with tailwind.config.ts this is the only place a
 * literal colour may appear; ESLint enforces it everywhere else.
 */
export const tokens = {
  colors: {
    // "Clinical Clarity": laboratory navy with a virology teal. Deep tones carry the brand, the
    // calm surfaces leave the confidence colours (green / amber / orange) as the loudest thing.
    primary: { 900: '#0A1F3D', 700: '#123C73', 500: '#1E63B5', 300: '#8FB6E6', 100: '#E7F0FC' },
    teal: { 700: '#0B6E6C', 500: '#12A19E', 300: '#7FD6D3', 100: '#DDF5F4' },
    accent: { 700: '#9E1049', 500: '#C2185B', 100: '#FBE4EE' },
    brand: { from: '#071733', via: '#123C73', to: '#0B6E6C', glow: '#5FE0D8' },
    surface: { base: '#F3F6FB', raised: '#FFFFFF', sunken: '#EAF0F8', border: '#DCE4EF' },
    ink: { strong: '#0E1A2E', base: '#33415A', muted: '#64718A', inverse: '#FFFFFF' },
    state: {
      verified: { fg: '#1B5E20', bg: '#F1FAF2', border: '#2E7D32' }, // level A/B
      single: { fg: '#2F5D3A', bg: '#F6FBF4', border: '#7CB342' }, // level C
      review: { fg: '#B33F00', bg: '#FFE8D6', border: '#E65100' }, // level D
      neutral: { fg: '#3A3D52', bg: '#EEF0F7', border: '#C9CBD6' },
    },
    cloud: '#12A19E', // the AI read (teal)
    local: '#1E63B5', // a stage that ran on the machine (blue)
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
    '4xl': '46px',
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
  borderRadius: { sm: '6px', md: '10px', lg: '16px', xl: '24px', pill: '999px' },
  boxShadow: {
    card: '0 1px 2px rgba(10,31,61,.06), 0 4px 14px rgba(10,31,61,.05)',
    raised: '0 10px 30px rgba(10,31,61,.12)',
    hero: '0 24px 60px rgba(7,23,51,.28)',
    focus: '0 0 0 3px rgba(30,99,181,.35)',
  },
  motion: { fast: '120ms', base: '180ms', slow: '260ms', ease: 'cubic-bezier(.2,.8,.2,1)' },
} as const;

export type Tokens = typeof tokens;

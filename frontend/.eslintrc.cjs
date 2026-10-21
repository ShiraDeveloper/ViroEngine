/* ESLint 8 config. Project-specific bans live in no-restricted-syntax (see .kiro/steering). */

const HEX = {
  selector: "Literal[value=/#[0-9a-fA-F]{3,8}\\b/]",
  message: 'Literal hex colour. Use a token from tailwind.config.ts / src/design/tokens.ts.',
};
const HEX_TEMPLATE = {
  selector: "TemplateElement[value.raw=/#[0-9a-fA-F]{3,8}\\b/]",
  message: 'Literal hex colour. Use a token from tailwind.config.ts / src/design/tokens.ts.',
};
const HEBREW = {
  selector: 'Literal[value=/[\\u0590-\\u05FF]/]',
  message: 'Literal Hebrew. Every user-visible string lives in src/copy/he.ts.',
};
const HEBREW_JSX = {
  selector: 'JSXText[value=/[\\u0590-\\u05FF]/]',
  message: 'Literal Hebrew. Every user-visible string lives in src/copy/he.ts.',
};
const HEBREW_TEMPLATE = {
  selector: 'TemplateElement[value.raw=/[\\u0590-\\u05FF]/]',
  message: 'Literal Hebrew. Every user-visible string lives in src/copy/he.ts.',
};
const PHYSICAL = {
  selector:
    'Literal[value=/(^|[\\s:])-?(ml|mr|pl|pr|left|right)-[0-9\\[]|(^|[\\s:])text-(left|right)\\b/]',
  message: 'Physical direction class. Use ms-*, me-*, ps-*, pe-*, start-*, end-*, text-start/end.',
};

const COLOUR_RULES = [HEX, HEX_TEMPLATE];
const COPY_RULES = [HEBREW, HEBREW_JSX, HEBREW_TEMPLATE];

module.exports = {
  root: true,
  env: { browser: true, es2022: true, node: true },
  parser: '@typescript-eslint/parser',
  parserOptions: { ecmaVersion: 2022, sourceType: 'module', ecmaFeatures: { jsx: true } },
  plugins: ['@typescript-eslint', 'react-hooks'],
  extends: ['eslint:recommended', 'plugin:@typescript-eslint/recommended', 'plugin:react-hooks/recommended'],
  ignorePatterns: ['dist', 'node_modules', 'playwright-report', 'test-results', 'coverage'],
  rules: {
    '@typescript-eslint/no-explicit-any': 'error',
    '@typescript-eslint/consistent-type-imports': 'error',
    'no-restricted-syntax': ['error', ...COLOUR_RULES, ...COPY_RULES, PHYSICAL],
  },
  overrides: [
    {
      // Colour literals are legitimate only where the palette is defined.
      files: ['src/design/tokens.ts', 'tailwind.config.ts'],
      rules: { 'no-restricted-syntax': ['error', ...COPY_RULES, PHYSICAL] },
    },
    {
      // The single home of Hebrew copy.
      files: ['src/copy/he.ts'],
      rules: { 'no-restricted-syntax': ['error', ...COLOUR_RULES, PHYSICAL] },
    },
    {
      // Tests may assert on copy and on class names, but never carry a colour literal.
      files: ['tests/**/*.{ts,tsx}', 'src/**/*.test.{ts,tsx}'],
      rules: { 'no-restricted-syntax': ['error', ...COLOUR_RULES] },
    },
    {
      files: ['*.cjs'],
      rules: { '@typescript-eslint/no-require-imports': 'off', 'no-restricted-syntax': 'off' },
    },
  ],
};

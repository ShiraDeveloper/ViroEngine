import '@testing-library/jest-dom/vitest';

// jsdom has no object URLs; the Evidence Lens and orphan thumbnails create them from Blobs.
if (typeof URL.createObjectURL !== 'function') {
  URL.createObjectURL = () => 'blob:test';
  URL.revokeObjectURL = () => undefined;
}

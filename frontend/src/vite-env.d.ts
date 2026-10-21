/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "1" selects the fixture-backed mock client; anything else targets the real API. */
  readonly VITE_USE_MOCK?: string;
  /** Base path of the API, default "/v1". */
  readonly VITE_API_BASE?: string;
  /** X-API-Key for the demo deployment. Shipped to the browser, so never a production secret. */
  readonly VITE_API_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** API base URL baked at build time. Empty string means same-origin "/api". */
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

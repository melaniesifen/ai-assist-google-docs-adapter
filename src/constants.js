export const PROVIDER = "google_docs";
export const CONNECTOR = "google_docs";

export const CONTEXT_MODES = Object.freeze({
  SELECTION: "SELECTION",
  ACTIVE_RESOURCE: "ACTIVE_RESOURCE"
});

export const MUTATION_TYPES = Object.freeze({
  REPLACE_TEXT: "REPLACE_TEXT",
  INSERT_TEXT: "INSERT_TEXT"
});

export const TRUST_LEVELS = Object.freeze({
  CONNECTOR_VERIFIED: "connector_verified"
});

export const SOURCE_TYPES = Object.freeze({
  CONNECTOR_SELECTION: "connector_selection",
  CONNECTOR_RESOURCE_EXCERPT: "connector_resource_excerpt"
});

export const DEFAULT_PAGE_SIZE = 25;
export const MAX_PAGE_SIZE = 100;
export const DEFAULT_CONTEXT_TTL_MS = 15 * 60 * 1000;
export const MAX_ACTIVE_RESOURCE_BYTES = 64 * 1024;

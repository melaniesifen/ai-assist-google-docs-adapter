import { createHash } from "node:crypto";

export function hashContent(content) {
  return `sha256:${createHash("sha256").update(content, "utf8").digest("hex")}`;
}

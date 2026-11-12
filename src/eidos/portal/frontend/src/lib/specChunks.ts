import * as React from "react";
import type { SpecItem } from "@/types";

export interface LoadedSpecChunk {
  id: string;
  body: string;
  frontmatter?: Record<string, any>;
}

const chunkCache = new Map<string, LoadedSpecChunk>();
const inflightRequests = new Map<string, Promise<LoadedSpecChunk | null>>();
const chunkPathById = new Map<string, string>();

/** Register spec metadata from PortalData so views can lazily fetch ./specs/<id>.json chunks. */
export function registerSpecChunks(specs: SpecItem[] = []): void {
  const seenIds = new Set<string>();
  specs.forEach((s) => {
    if (!s || !s.id) return;
    seenIds.add(s.id);
    if (s.body_chunk) {
      chunkPathById.set(s.id, s.body_chunk);
    } else {
      chunkPathById.delete(s.id);
    }
    if (typeof s.body === "string" && (s.body.length > 0 || !s.body_chunk)) {
      chunkCache.set(s.id, {
        id: s.id,
        body: s.body,
        frontmatter: s.frontmatter,
      });
    }
  });
  for (const existingId of Array.from(chunkCache.keys())) {
    if (!seenIds.has(existingId)) {
      chunkCache.delete(existingId);
    }
  }
  for (const existingId of Array.from(chunkPathById.keys())) {
    if (!seenIds.has(existingId)) {
      chunkPathById.delete(existingId);
    }
  }
}

/** True when either an inline body is already available or a local static chunk exists for specId. */
export function hasSpecBodyOrChunk(specId?: string, inlineBody?: string): boolean {
  if (typeof inlineBody === "string" && inlineBody.length > 0) return true;
  if (!specId) return false;
  return chunkCache.has(specId) || chunkPathById.has(specId);
}

/** Lazily load and memoize a local ./specs/<id>.json chunk emitted by `eidos portal build`. */
export async function loadSpecChunk(
  specId: string,
  explicitChunkPath?: string
): Promise<LoadedSpecChunk | null> {
  if (!specId) return null;
  const cached = chunkCache.get(specId);
  if (cached) return cached;

  const chunkRel = explicitChunkPath || chunkPathById.get(specId);
  if (!chunkRel) return null;

  const pending = inflightRequests.get(specId);
  if (pending) return pending;

  const cleanRel = chunkRel.replace(/^\.?\//, "");
  const request = fetch(`./${cleanRel}`)
    .then((res) => (res.ok ? res.json() : null))
    .then((payload) => {
      inflightRequests.delete(specId);
      if (payload && typeof payload.body === "string") {
        const loaded: LoadedSpecChunk = {
          id: payload.id || specId,
          body: payload.body,
          frontmatter:
            payload.frontmatter && typeof payload.frontmatter === "object"
              ? payload.frontmatter
              : undefined,
        };
        chunkCache.set(specId, loaded);
        return loaded;
      }
      return null;
    })
    .catch(() => {
      inflightRequests.delete(specId);
      return null;
    });

  inflightRequests.set(specId, request);
  return request;
}

/** React hook resolving inline spec prose or lazily fetching its local ./specs/<id>.json chunk. */
export function useResolvedSpecBody(
  specId?: string,
  inlineBody?: string,
  explicitChunkPath?: string
): {
  body: string;
  frontmatter?: Record<string, any>;
  loading: boolean;
} {
  const cached = specId ? chunkCache.get(specId) : undefined;
  const initialBody =
    (typeof inlineBody === "string" && inlineBody.length > 0 ? inlineBody : undefined) ??
    cached?.body ??
    "";

  const [body, setBody] = React.useState<string>(initialBody);
  const [frontmatter, setFrontmatter] = React.useState<Record<string, any> | undefined>(
    cached?.frontmatter
  );
  const [loading, setLoading] = React.useState<boolean>(
    Boolean(!initialBody && specId && (explicitChunkPath || chunkPathById.has(specId)))
  );

  React.useEffect(() => {
    let cancelled = false;
    if (typeof inlineBody === "string" && inlineBody.length > 0) {
      setBody(inlineBody);
      setFrontmatter(specId ? chunkCache.get(specId)?.frontmatter : undefined);
      setLoading(false);
      return;
    }
    if (!specId) {
      setBody("");
      setFrontmatter(undefined);
      setLoading(false);
      return;
    }
    const hit = chunkCache.get(specId);
    if (hit) {
      setBody(hit.body);
      setFrontmatter(hit.frontmatter);
      setLoading(false);
      return;
    }
    if (!explicitChunkPath && !chunkPathById.has(specId)) {
      setBody("");
      setLoading(false);
      return;
    }
    setLoading(true);
    loadSpecChunk(specId, explicitChunkPath).then((loaded) => {
      if (cancelled) return;
      if (loaded) {
        setBody(loaded.body);
        setFrontmatter(loaded.frontmatter);
      } else {
        setBody("");
      }
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [specId, inlineBody, explicitChunkPath]);

  return { body, frontmatter, loading };
}

/**
 * Models Catalog loader for React CLI
 *
 * Loads the authoritative model catalog from models.dev with a safe fallback
 * to the embedded snapshot at src/modules/config/models/models_snapshot.json.
 *
 * Notes:
 * - This module caches results in-memory for the process lifetime
 */

import * as fs from 'fs/promises';
import * as path from 'path';

type Json = any;

export interface CatalogModel {
  id: string;
  name?: string;
  family?: string;
  limit?: { context?: number; output?: number };
  cost?: {
    input?: number;
    output?: number;
    cache_read?: number;
    cache_write?: number;
    reasoning?: number;
  };
}

export interface CatalogProvider {
  id: string;
  name?: string;
  api?: string;
  models: Record<string, CatalogModel>;
}

export type ModelsCatalog = Record<string, CatalogProvider>;

class ModelsCatalogLoader {
  private catalog: ModelsCatalog | null = null;
  private loading: Promise<ModelsCatalog> | null = null;

  async getCatalog(): Promise<ModelsCatalog> {
    if (this.catalog) return this.catalog;
    if (this.loading) return this.loading;
    this.loading = this.load();
    this.catalog = await this.loading;
    this.loading = null;
    return this.catalog;
  }

  // Return currently loaded catalog synchronously (or null if not yet loaded)
  peekCatalog(): ModelsCatalog | null {
    return this.catalog;
  }

  async getAllModels(): Promise<Array<{ provider: string; model: CatalogModel }>> {
    const cat = await this.getCatalog();
    const out: Array<{ provider: string; model: CatalogModel }> = [];
    for (const [providerId, provider] of Object.entries(cat)) {
      if (!provider?.models) continue;
      for (const m of Object.values(provider.models)) {
        out.push({ provider: providerId, model: { id: m.id, ...m } });
      }
    }
    return out;
  }

  peekAllModels(): Array<{ provider: string; model: CatalogModel }> | null {
    const cat = this.peekCatalog();
    if (!cat) return null;
    const out: Array<{ provider: string; model: CatalogModel }> = [];
    for (const [providerId, provider] of Object.entries(cat)) {
      if (!provider?.models) continue;
      for (const m of Object.values(provider.models)) {
        out.push({ provider: providerId, model: { id: m.id, ...m } });
      }
    }
    return out;
  }

  async findModel(modelId: string): Promise<{ provider: string; model: CatalogModel } | null> {
    const all = await this.getAllModels();
    // Direct ID match first
    let found = all.find(x => x.model.id === modelId);
    if (found) return found;
    // Heuristic: try last path segment if ID contains '/'
    const shortId = modelId.includes('/') ? modelId.split('/').pop()! : modelId;
    found = all.find(x => x.model.id === shortId || x.model.name === shortId);
    if (found) return found;
    // Heuristic: strip region/provider prefixes like "us.anthropic." if present
    const dotted = shortId.includes('.') ? shortId.split('.').pop()! : shortId;
    found = all.find(x => x.model.id.endsWith(dotted) || (x.model.name?.replace(/\s+/g, '-').toLowerCase().includes(dotted.toLowerCase())));
    return found || null;
  }

  private async load(): Promise<ModelsCatalog> {
    // Tier 1: Environment-provided cache file path
    const cachePath = process.env.MODELS_DEV_CACHE_PATH;
    if (cachePath) {
      try {
        const txt = await fs.readFile(cachePath, 'utf8');
        const data = JSON.parse(txt) as ModelsCatalog;
        return data;
      } catch {
        // ignore and continue
      }
    }

    // Tier 2: Live API (unless explicitly offline)
    const offline = (process.env.DEV_CLIENT_OFFLINE || '').toLowerCase() === 'true';
    if (!offline && typeof fetch === 'function') {
      try {
        const res = await fetch('https://models.dev/api.json', { method: 'GET' });
        if (res.ok) {
          const data = (await res.json()) as ModelsCatalog;
          return data;
        }
      } catch {
        // ignore and continue
      }
    }

    // Tier 3: Embedded snapshot within the repository
    const candidates: string[] = [];
    // 1) Resolve from current working directory (tests usually run from project root)
    candidates.push(path.resolve(process.cwd(), 'src/modules/config/models/models_snapshot.json'));
    // 2) Resolve relative to this file (in case cwd differs)
    candidates.push(path.resolve(path.dirname(new URL(import.meta.url).pathname), '../../../../config/models/models_snapshot.json'));

    for (const p of candidates) {
      try {
        const txt = await fs.readFile(p, 'utf8');
        const data = JSON.parse(txt) as ModelsCatalog;
        return data;
      } catch {
        // try next candidate
      }
    }

    // Absolute last resort: empty catalog
    return {} as ModelsCatalog;
  }
}

export const modelsCatalog = new ModelsCatalogLoader();

// Top-level helpers to facilitate ESM-friendly testing/mocking
export function peekAllModels(): Array<{ provider: string; model: CatalogModel }> | null {
  return modelsCatalog.peekAllModels();
}

export async function loadAllModels(): Promise<Array<{ provider: string; model: CatalogModel }>> {
  return modelsCatalog.getAllModels();
}

export async function getContextLimit(modelId: string): Promise<number | null> {
  const found = await modelsCatalog.findModel(modelId);
  return found?.model?.limit?.context ?? null;
}

export function getContextLimitSync(modelId: string): number | null {
  const cat = modelsCatalog.peekCatalog();
  if (!cat) return null;
  for (const provider of Object.values(cat)) {
    for (const m of Object.values(provider.models || {})) {
      if (m.id === modelId) {
        return m.limit?.context ?? null;
      }
    }
  }
  return null;
}

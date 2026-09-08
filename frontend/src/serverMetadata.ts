import type { Server } from './types';

/**
 * Frontend fallback helpers for controller metadata.
 *
 * Prefer the backend-supplied `controller_label` / `controller_source` fields.
 * These local heuristics only exist so the UI still renders sensible labels if
 * an older API payload is returned.
 */
function inferPowerEdgeGeneration(model?: string | null): number | null {
  if (!model) return null;
  const normalized = model.toUpperCase();
  const match = normalized.match(/POWEREDGE\s+[A-Z]*?(\d{3,4})/i) || normalized.match(/\b([A-Z])(\d{3,4})/i);
  const digits = match?.[1] && /^\d+$/.test(match[1]) ? match[1] : match?.[2];
  if (!digits || digits.length < 3) return null;

  const generationDigit = digits[1];
  if (!/^\d$/.test(generationDigit)) return null;

  const generation = 10 + Number(generationDigit);
  return generation >= 11 && generation <= 20 ? generation : null;
}

export function describeController(server: Pick<Server, 'model' | 'drac_version' | 'controller_label'>): string {
  if (server.controller_label) return server.controller_label;

  const generation = inferPowerEdgeGeneration(server.model);
  if (generation === 12) return 'iDRAC7 (12G)';
  if (generation === 13) return 'iDRAC8 (13G)';
  if (generation != null && generation >= 14) return `iDRAC9 (${generation}G)`;

  if (server.drac_version === 'idrac7') return 'iDRAC7';
  if (server.drac_version === 'idrac8') return 'iDRAC8';
  return 'Unknown';
}

export function describeControllerSource(server: Pick<Server, 'model' | 'drac_version' | 'controller_source'>): string {
  if (server.controller_source) return server.controller_source;

  const generation = inferPowerEdgeGeneration(server.model);
  if (generation != null) return `Derived from ${generation}G PowerEdge platform model`;
  if (server.drac_version) return 'Derived from stored DSM controller profile';
  return 'Controller generation not yet identified';
}

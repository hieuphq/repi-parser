/**
 * instruction-parser.client.ts
 *
 * Thin client for the repi-parser service (localhost:7878).
 * Called from the Repi API layer — never directly from the browser.
 *
 * Place this file in: apps/api/src/lib/instruction-parser.client.ts
 */

// ── Types ──────────────────────────────────────────────────────────────────

export type ApplianceType =
  | "oven"
  | "stovetop"
  | "airfryer"
  | "mixer"
  | "blender"
  | "instant_pot"
  | "steamer"
  | "microwave"
  | "rice_cooker"
  | "grill";

export type FlameLevel = "low" | "medium" | "high";
export type TimerType = "passive" | "active" | "resting";

export interface ParsedInstruction {
  appliance: ApplianceType | null;
  temp_min_celsius: number | null;
  temp_max_celsius: number | null;
  flame_level: FlameLevel | null;
  duration_min_minutes: number | null;
  duration_max_minutes: number | null;
  timer_type: TimerType | null;
  confidence: number;
  parsed_by: "regex" | "model" | "none";
}

// ── Config ─────────────────────────────────────────────────────────────────

const PARSER_BASE_URL = process.env.PARSER_SERVICE_URL ?? "http://127.0.0.1:7878";
const REQUEST_TIMEOUT_MS = 3000; // 3s — fail fast, don't block recipe save

// ── Client ─────────────────────────────────────────────────────────────────

/**
 * Parse a single cooking instruction text.
 * Returns null if parser service is down or times out — caller should
 * treat null as "no structured data available" and proceed without it.
 */
export async function parseInstruction(
  text: string
): Promise<ParsedInstruction | null> {
  if (!text || text.trim().length === 0) return null;

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch(`${PARSER_BASE_URL}/parse`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text.trim() }),
      signal: controller.signal,
    });

    if (!response.ok) {
      console.warn(`[repi-parser] HTTP ${response.status} for text: "${text.slice(0, 60)}"`);
      return null;
    }

    return (await response.json()) as ParsedInstruction;
  } catch (err) {
    if ((err as Error).name === "AbortError") {
      console.warn("[repi-parser] Request timed out — proceeding without parse.");
    } else {
      console.warn("[repi-parser] Service unavailable:", (err as Error).message);
    }
    return null;
  } finally {
    clearTimeout(timeout);
  }
}

/**
 * Parse multiple instructions in parallel (e.g., full recipe save).
 * Returns array aligned with input — null entries mean parse failed.
 */
export async function parseInstructions(
  texts: string[]
): Promise<(ParsedInstruction | null)[]> {
  return Promise.all(texts.map(parseInstruction));
}

/**
 * Health check — use this in startup probes or admin dashboards.
 */
export async function checkParserHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${PARSER_BASE_URL}/health`, {
      signal: AbortSignal.timeout(1000),
    });
    const data = await res.json();
    return data?.status === "ok";
  } catch {
    return false;
  }
}

// ── Helpers for Cook Mode display ──────────────────────────────────────────

/** Convert stored °C to display string based on user preference */
export function formatTemperature(
  celsius: number,
  unit: "C" | "F",
  applianceOffset = 0
): string {
  const adjusted = celsius + applianceOffset;
  if (unit === "F") {
    const f = Math.round((adjusted * 9) / 5 + 32);
    return `${f}°F`;
  }
  return `${Math.round(adjusted)}°C`;
}

/** Format duration range for display */
export function formatDuration(
  minMinutes: number | null,
  maxMinutes: number | null
): string | null {
  if (minMinutes === null) return null;
  const fmt = (m: number) => {
    if (m < 60) return `${m} phút`;
    const h = Math.floor(m / 60);
    const rem = m % 60;
    return rem > 0 ? `${h} tiếng ${rem} phút` : `${h} tiếng`;
  };
  if (maxMinutes !== null && maxMinutes !== minMinutes) {
    return `${fmt(minMinutes)} – ${fmt(maxMinutes)}`;
  }
  return fmt(minMinutes);
}

import fs from "fs";
import os from "os";
import path from "path";

export const DEFAULT_COMMAND_HISTORY_LIMIT = 100;

export const getCommandHistoryPath = (): string => {
  const configDir = process.env.CYBER_CONFIG_DIR || path.join(os.homedir(), ".cyber-autoagent");
  return path.join(configDir, "command-history.json");
};

export const normalizeCommandHistory = (
  entries: unknown,
  limit: number = DEFAULT_COMMAND_HISTORY_LIMIT
): string[] => {
  if (!Array.isArray(entries)) {
    return [];
  }

  const normalized: string[] = [];
  for (const entry of entries) {
    if (typeof entry !== "string") {
      continue;
    }

    const command = entry.trim();
    if (!command) {
      continue;
    }

    if (normalized[normalized.length - 1] !== command) {
      normalized.push(command);
    }
  }

  return normalized.slice(-limit);
};

export const loadCommandHistory = (): string[] => {
  try {
    const raw = fs.readFileSync(getCommandHistoryPath(), "utf8");
    return normalizeCommandHistory(JSON.parse(raw));
  } catch {
    return [];
  }
};

export const saveCommandHistory = (entries: string[]): void => {
  const historyPath = getCommandHistoryPath();
  const normalized = normalizeCommandHistory(entries);
  fs.mkdirSync(path.dirname(historyPath), { recursive: true });
  fs.writeFileSync(historyPath, `${JSON.stringify(normalized, null, 2)}\n`, "utf8");
};

export const appendCommandHistory = (entries: string[], command: string): string[] => {
  const trimmed = command.trim();
  if (!trimmed || trimmed.startsWith("/")) {
    return normalizeCommandHistory(entries);
  }

  const previous = normalizeCommandHistory(entries);
  const next = previous[previous.length - 1] === trimmed ? previous : [...previous, trimmed];
  const normalized = normalizeCommandHistory(next);
  try {
    saveCommandHistory(normalized);
  } catch {
    // History recall should remain available in-process even if persistence fails.
  }
  return normalized;
};

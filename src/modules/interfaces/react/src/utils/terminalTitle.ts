import type { OperationHealthSnapshot } from './operationHealthFormatting.js';
import { formatOperationHealth } from './operationHealthFormatting.js';

interface TerminalTitleStream {
  isTTY?: boolean;
  write: (chunk: string) => unknown;
}

const lastTitleByStream = new WeakMap<object, string>();

const sanitizeTerminalTitleTarget = (target: unknown): string => String(target ?? '')
  .replace(/[\u0000-\u001F\u007F-\u009F]/g, '')
  .trim();

export const formatOperationTerminalTitle = (health: unknown, target?: string | null): string => {
  const visual = formatOperationHealth(health);
  const targetValue = sanitizeTerminalTitleTarget(target);
  const healthTitle = visual ? `CAA ${visual.label}` : 'CAA';
  return targetValue ? `${healthTitle} | ${targetValue}` : healthTitle;
};

export const setOperationTerminalTitle = (
  health: OperationHealthSnapshot | null | undefined,
  target?: string | null,
  stream: TerminalTitleStream = process.stdout,
): boolean => {
  if (stream.isTTY !== true) return false;

  const title = formatOperationTerminalTitle(health, target);
  if (lastTitleByStream.get(stream as object) === title) return false;

  stream.write(`\u001B]0;${title}\u0007`);
  lastTitleByStream.set(stream as object, title);
  return true;
};

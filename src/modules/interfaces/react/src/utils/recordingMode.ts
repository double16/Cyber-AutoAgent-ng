import { execFileSync } from 'node:child_process';

export type ProcessInfo = {
  ppid: number | null;
  command: string;
};

export type ProcessLookup = (pid: number) => ProcessInfo | null;

export const detectAsciinemaInHierarchy = (
  startPid: number,
  lookup: ProcessLookup,
  maxDepth = 30
): boolean => {
  if (!Number.isFinite(startPid) || startPid <= 0) {
    return false;
  }

  let currentPid = Math.floor(startPid);
  for (let depth = 0; depth < maxDepth; depth += 1) {
    const info = lookup(currentPid);
    if (!info) {
      return false;
    }
    if ((info.command || '').toLowerCase().includes('asciinema')) {
      return true;
    }
    if (!info.ppid || info.ppid <= 0 || info.ppid === currentPid) {
      return false;
    }
    currentPid = info.ppid;
  }

  return false;
};

const psLookup: ProcessLookup = (pid: number) => {
  try {
    const ppidRaw = execFileSync('ps', ['-o', 'ppid=', '-p', String(pid)], {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore']
    }).trim();
    const command = execFileSync('ps', ['-o', 'command=', '-p', String(pid)], {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore']
    }).trim();
    const parsedPpid = Number.parseInt(ppidRaw, 10);
    return {
      ppid: Number.isFinite(parsedPpid) ? parsedPpid : null,
      command
    };
  } catch {
    return null;
  }
};

export const detectAsciinemaParentProcess = (
  lookup: ProcessLookup = psLookup,
  startPid = process.ppid
): boolean => detectAsciinemaInHierarchy(startPid, lookup);

export const resolveRecordingMode = (
  cliRecordingFlag?: boolean,
  detector: () => boolean = () => detectAsciinemaParentProcess()
): boolean => {
  if (cliRecordingFlag === true) {
    return true;
  }
  return detector();
};

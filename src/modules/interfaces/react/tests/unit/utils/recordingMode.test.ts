import { describe, expect, it } from '@jest/globals';
import {
  detectAsciinemaInHierarchy,
  resolveRecordingMode,
  type ProcessLookup,
} from '../../../src/utils/recordingMode.js';

describe('recordingMode', () => {
  it('detects asciinema in parent process hierarchy', () => {
    const table = new Map<number, { ppid: number | null; command: string }>([
      [1234, { ppid: 4321, command: 'node dist/index.js' }],
      [4321, { ppid: 5000, command: '/usr/local/bin/asciinema rec' }],
    ]);
    const lookup: ProcessLookup = (pid) => table.get(pid) ?? null;

    expect(detectAsciinemaInHierarchy(1234, lookup)).toBe(true);
  });

  it('returns false when asciinema is not present in parent hierarchy', () => {
    const table = new Map<number, { ppid: number | null; command: string }>([
      [1234, { ppid: 4321, command: 'node dist/index.js' }],
      [4321, { ppid: 1, command: '/bin/zsh' }],
      [1, { ppid: null, command: '/sbin/launchd' }],
    ]);
    const lookup: ProcessLookup = (pid) => table.get(pid) ?? null;

    expect(detectAsciinemaInHierarchy(1234, lookup)).toBe(false);
  });

  it('prefers explicit --recording over parent detection', () => {
    const detector = () => false;
    expect(resolveRecordingMode(true, detector)).toBe(true);
  });

  it('uses parent-process detection when --recording is not explicitly enabled', () => {
    const detector = () => true;
    expect(resolveRecordingMode(false, detector)).toBe(true);
    expect(resolveRecordingMode(undefined, detector)).toBe(true);
  });
});

import {afterEach, describe, expect, it, jest} from '@jest/globals';
const exec = jest.fn<any>();
jest.unstable_mockModule('node:child_process', () => ({execFileSync: exec}));
const {detectAsciinemaInHierarchy, detectAsciinemaParentProcess, resolveRecordingMode} =
  await import('../../../src/utils/recordingMode.js');
afterEach(() => exec.mockReset());
describe('recording process discovery failures and boundaries', () => {
  it.each([NaN, Infinity, 0, -1])('rejects invalid process id %s', pid => {
    const lookup = jest.fn();
    expect(detectAsciinemaInHierarchy(pid, lookup)).toBe(false);
    expect(lookup).not.toHaveBeenCalled();
  });
  it('stops on missing, cyclic, empty, and depth-limited processes', () => {
    expect(detectAsciinemaInHierarchy(1, () => null)).toBe(false);
    expect(detectAsciinemaInHierarchy(1, () => ({ppid: 1, command: ''}))).toBe(false);
    expect(detectAsciinemaInHierarchy(1, () => ({ppid: -1, command: ''}))).toBe(false);
    expect(detectAsciinemaInHierarchy(1, pid => ({ppid: pid + 1, command: ''}), 2)).toBe(false);
  });
  it('reads parent command with ps and detects recorder case-insensitively', () => {
    exec.mockReturnValueOnce('42\n').mockReturnValueOnce('ASC I\n')
      .mockReturnValueOnce('1').mockReturnValueOnce('ASCIINEMA rec');
    expect(detectAsciinemaParentProcess(undefined, 12)).toBe(true);
    expect(exec).toHaveBeenCalledWith('ps', ['-o', 'ppid=', '-p', '12'], expect.any(Object));
  });
  it('handles invalid ps output and unavailable ps', () => {
    exec.mockReturnValueOnce('unknown').mockReturnValueOnce('shell');
    expect(resolveRecordingMode()).toBe(false);
    exec.mockImplementation(() => {throw new Error('ps unavailable');});
    expect(detectAsciinemaParentProcess()).toBe(false);
  });
});

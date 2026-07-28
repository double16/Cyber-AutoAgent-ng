import { describe, expect, it, jest } from '@jest/globals';
import {
  formatOperationTerminalTitle,
  setOperationTerminalTitle,
} from '../../../src/utils/terminalTitle.js';

const ttyStream = () => ({
  isTTY: true,
  write: jest.fn<(chunk: string) => boolean>(() => true),
});

describe('operation terminal title', () => {
  it.each([
    ['excellent', '💚'],
    ['good', '💚'],
    ['degraded', '💛'],
    ['poor', '♥️'],
  ])('formats the %s health band with its canonical visual', (band, emoji) => {
    expect(formatOperationTerminalTitle({status: 'available', score: 0.86, band}))
      .toBe(`CAA ${emoji} 86% ${band.toUpperCase()}`);
  });

  it('falls back to CAA for unavailable and invalid health', () => {
    expect(formatOperationTerminalTitle(null)).toBe('CAA');
    expect(formatOperationTerminalTitle({status: 'unavailable', score: 0.86, band: 'good'})).toBe('CAA');
    expect(formatOperationTerminalTitle({status: 'available', score: 1.2, band: 'good'})).toBe('CAA');
  });

  it('appends the entered target and removes terminal control characters', () => {
    expect(formatOperationTerminalTitle(
      {status: 'available', score: 0.86, band: 'good'},
      ' https://target.test\u0007\u001B]0;spoofed ',
    )).toBe('CAA 💚 86% GOOD | https://target.test]0;spoofed');
    expect(formatOperationTerminalTitle(null, 'target.test')).toBe('CAA | target.test');
  });

  it('writes a TTY title once for each distinct health label', () => {
    const stream = ttyStream();
    const health = {status: 'available', score: 0.86, band: 'good'};

    expect(setOperationTerminalTitle(health, 'target.test', stream)).toBe(true);
    expect(setOperationTerminalTitle(health, 'target.test', stream)).toBe(false);
    expect(setOperationTerminalTitle(null, null, stream)).toBe(true);
    expect(stream.write.mock.calls).toEqual([
      ['\u001B]0;CAA 💚 86% GOOD | target.test\u0007'],
      ['\u001B]0;CAA\u0007'],
    ]);
  });

  it('does not emit terminal control sequences to non-TTY streams', () => {
    const stream = {
      isTTY: false,
      write: jest.fn<(chunk: string) => boolean>(() => true),
    };

    expect(setOperationTerminalTitle(
      {status: 'available', score: 0.86, band: 'good'},
      'target.test',
      stream,
    )).toBe(false);
    expect(stream.write).not.toHaveBeenCalled();
  });
});

import {EventEmitter} from 'events';
import {describe, expect, it, jest} from '@jest/globals';
import {installAutoRunInterruptFallback} from '../../../src/utils/autoRunInterrupt.js';

class FakeInput extends EventEmitter {
  isTTY = true;
  isRaw = false;
  readableFlowing: boolean | null = false;
  setRawMode = jest.fn((enabled: boolean) => { this.isRaw = enabled; });
  resume = jest.fn(() => { this.readableFlowing = true; return this; });
  pause = jest.fn(() => { this.readableFlowing = false; return this; });
}

describe('installAutoRunInterruptFallback', () => {
  it('routes Ctrl-C bytes to the interrupt callback and restores terminal state', () => {
    const input = new FakeInput();
    const onInterrupt = jest.fn();
    const cleanup = installAutoRunInterruptFallback(onInterrupt, input);

    expect(input.setRawMode).toHaveBeenCalledWith(true);
    input.emit('data', Buffer.from('x\x03y'));
    expect(onInterrupt).toHaveBeenCalledTimes(1);

    cleanup();
    expect(input.setRawMode).toHaveBeenLastCalledWith(false);
    expect(input.pause).toHaveBeenCalledTimes(1);
    input.emit('data', Buffer.from('\x03'));
    expect(onInterrupt).toHaveBeenCalledTimes(1);
  });

  it('does nothing for non-TTY input', () => {
    const input = new FakeInput();
    input.isTTY = false;
    const onInterrupt = jest.fn();
    const cleanup = installAutoRunInterruptFallback(onInterrupt, input);

    input.emit('data', Buffer.from('\x03'));
    cleanup();
    expect(onInterrupt).not.toHaveBeenCalled();
    expect(input.setRawMode).not.toHaveBeenCalled();
  });
});

it('preserves an already raw and flowing terminal, and accepts string input', () => {
  const input = new FakeInput();
  input.isRaw = true;
  input.readableFlowing = true;
  const interrupt = jest.fn();
  const cleanup = installAutoRunInterruptFallback(interrupt, input);
  input.emit('data', 'hello');
  expect(interrupt).not.toHaveBeenCalled();
  input.emit('data', '\x03');
  expect(interrupt).toHaveBeenCalledTimes(1);
  cleanup();
  expect(input.setRawMode).toHaveBeenCalledTimes(1);
  expect(input.pause).not.toHaveBeenCalled();
});

it('supports TTY input without optional flow controls', () => {
  const input = new FakeInput();
  (input as any).resume = undefined;
  (input as any).pause = undefined;
  const cleanup = installAutoRunInterruptFallback(jest.fn(), input);
  (input as any).setRawMode = undefined;
  expect(cleanup).not.toThrow();
  expect(installAutoRunInterruptFallback(jest.fn(), input)).not.toThrow();
});

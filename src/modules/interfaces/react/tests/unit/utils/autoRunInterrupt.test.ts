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

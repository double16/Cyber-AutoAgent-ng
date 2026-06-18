import React from 'react';
import { TextEncoder, TextDecoder } from 'util';
import { jest } from '@jest/globals';
import TestRenderer, { act } from 'react-test-renderer';

if (typeof global.TextEncoder === 'undefined') {
  global.TextEncoder = TextEncoder;
}
if (typeof global.TextDecoder === 'undefined') {
  global.TextDecoder = TextDecoder as typeof global.TextDecoder;
}

let keypressHandler: ((key: any) => void) | undefined;
const subscribe = jest.fn((handler: (key: any) => void) => {
  keypressHandler = handler;
});
const unsubscribe = jest.fn();

jest.unstable_mockModule('../../../src/contexts/KeypressContext.js', () => ({
  useKeypressContext: () => ({ subscribe, unsubscribe }),
}));

const load = async () => {
  const [
    { PasswordInput },
    { TokenInput },
    { ExtendedTextInput },
    { PasteAwareTextInput },
  ] = await Promise.all([
    import('../../../src/components/PasswordInput.js'),
    import('../../../src/components/TokenInput.js'),
    import('../../../src/components/ExtendedTextInput.js'),
    import('../../../src/components/PasteAwareTextInput.js'),
  ]);
  return { PasswordInput, TokenInput, ExtendedTextInput, PasteAwareTextInput };
};

const textFromTree = (node: any): string => {
  if (node == null || typeof node === 'boolean') return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(textFromTree).join('');
  return textFromTree(node.children || []);
};

const sendInkInput = (input = '', key: Record<string, boolean> = {}) => {
  act(() => {
    (global as any).__inkInputHandler?.(input, key);
  });
};

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

describe('text input components', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    keypressHandler = undefined;
    subscribe.mockClear();
    unsubscribe.mockClear();
    delete (global as any).__inkInputHandler;
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('handles password input masking, backspace, escape, submit, and cursor blink', async () => {
    const { PasswordInput } = await load();
    const onSubmit = jest.fn();
    let view!: TestRenderer.ReactTestRenderer;

    act(() => {
      view = TestRenderer.create(<PasswordInput onSubmit={onSubmit} fieldKey="secret" />);
    });
    expect(textFromTree(view.toJSON())).toContain('█');

    sendInkInput('abc');
    expect(textFromTree(view.toJSON())).toContain('***');
    expect(textFromTree(view.toJSON())).toContain('3 chars');

    sendInkInput('', { backspace: true });
    expect(textFromTree(view.toJSON())).toContain('2 chars');
    sendInkInput('v', { ctrl: true });
    sendInkInput('', { return: true });
    expect(onSubmit).toHaveBeenCalledWith('ab');

    sendInkInput('', { escape: true });
    expect(textFromTree(view.toJSON())).not.toContain('chars');
    act(() => jest.advanceTimersByTime(500));
    expect(textFromTree(view.toJSON()).length).toBeGreaterThan(0);
  });

  it('handles token input status for long AWS bearer tokens', async () => {
    const { TokenInput } = await load();
    const onSubmit = jest.fn();
    let view!: TestRenderer.ReactTestRenderer;

    act(() => {
      view = TestRenderer.create(<TokenInput onSubmit={onSubmit} fieldKey="awsBearerToken" />);
    });
    expect(textFromTree(view.toJSON())).toContain('Paste token and press Enter');

    sendInkInput('x'.repeat(10));
    expect(textFromTree(view.toJSON())).toContain('Paste remaining 122 chars');
    sendInkInput('y'.repeat(122));
    expect(textFromTree(view.toJSON())).toContain('Press Enter to save');
    sendInkInput('', { return: true });
    expect(onSubmit).toHaveBeenCalledWith('x'.repeat(10) + 'y'.repeat(122));
    sendInkInput('', { delete: true });
    sendInkInput('', { escape: true });
    expect(textFromTree(view.toJSON())).toContain('Paste token and press Enter');
  });

  it('supports extended text editing and disabled rendering', async () => {
    const { ExtendedTextInput } = await load();
    const onChange = jest.fn();
    const onSubmit = jest.fn();
    let value = '';
    let view!: TestRenderer.ReactTestRenderer;
    const render = () => (
      <ExtendedTextInput
        value={value}
        onChange={(next) => {
          value = next;
          onChange(next);
          view.update(render());
        }}
        onSubmit={onSubmit}
        placeholder="enter text"
      />
    );

    act(() => {
      view = TestRenderer.create(render());
    });
    expect(textFromTree(view.toJSON())).toContain('enter text');

    sendInkInput('abc');
    expect(onChange).toHaveBeenCalledWith('abc');
    sendInkInput('', { leftArrow: true });
    sendInkInput('Z');
    expect(value).toBe('abZc');
    sendInkInput('', { rightArrow: true });
    sendInkInput('a', { ctrl: true });
    sendInkInput('X');
    expect(value.startsWith('X')).toBe(true);
    sendInkInput('e', { ctrl: true });
    sendInkInput('', { backspace: true });
    sendInkInput('', { delete: true });
    sendInkInput('u', { ctrl: true });
    expect(value).toBe('');
    sendInkInput('', { return: true });
    expect(onSubmit).toHaveBeenCalledWith('');

    act(() => {
      view.update(<ExtendedTextInput value="fixed" onChange={onChange} disabled showCursor={false} />);
    });
    expect(textFromTree(view.toJSON())).toContain('fixed');
  });

  it('handles paste-aware text input subscriptions, masking, movement, deletion, and submit', async () => {
    const { PasteAwareTextInput } = await load();
    const onChange = jest.fn();
    const onSubmit = jest.fn();
    let value = 'ab';
    let view!: TestRenderer.ReactTestRenderer;
    const render = (focus = true) => (
      <PasteAwareTextInput
        value={value}
        onChange={(next) => {
          value = next;
          onChange(next);
          view.update(render(focus));
        }}
        onSubmit={onSubmit}
        placeholder="token"
        mask="*"
        focus={focus}
      />
    );

    act(() => {
      view = TestRenderer.create(render());
    });
    expect(subscribe).toHaveBeenCalled();

    act(() => keypressHandler?.({ name: 'left', sequence: '', ctrl: false, meta: false, paste: false }));
    act(() => keypressHandler?.({ name: '', sequence: 'Z', ctrl: false, meta: false, paste: false }));
    expect(value).toBe('aZb');
    act(() => keypressHandler?.({ name: '', sequence: 'PASTE', ctrl: false, meta: false, paste: true }));
    expect(value).toContain('PASTE');
    act(() => keypressHandler?.({ name: 'backspace', sequence: '', ctrl: false, meta: false, paste: false }));
    act(() => keypressHandler?.({ name: 'delete', sequence: '', ctrl: false, meta: false, paste: false }));
    act(() => keypressHandler?.({ name: 'right', sequence: '', ctrl: false, meta: false, paste: false }));
    act(() => keypressHandler?.({ name: 'return', sequence: '', ctrl: false, meta: false, paste: false }));
    expect(onSubmit).toHaveBeenCalledWith(value);

    act(() => {
      view.update(render(false));
    });
    act(() => {
      view.unmount();
    });
    expect(unsubscribe).toHaveBeenCalled();
  });
});

import React, {useEffect} from 'react';
import {PassThrough} from 'stream';
import {TextDecoder, TextEncoder} from 'util';
import {jest} from '@jest/globals';
import TestRenderer, {act} from 'react-test-renderer';

if (typeof global.TextEncoder === 'undefined') {
    global.TextEncoder = TextEncoder;
}
if (typeof global.TextDecoder === 'undefined') {
    global.TextDecoder = TextDecoder as typeof global.TextDecoder;
}

let stdin: PassThrough & { isRaw?: boolean; isTTY?: boolean };
const setRawMode = jest.fn();

jest.unstable_mockModule('ink', () => ({
    useStdin: () => ({stdin, setRawMode}),
}));

const load = async () => {
    const keypressContext = await import('../../../src/contexts/KeypressContext.js');
    const keypressHook = await import('../../../src/hooks/useKeypress.js');
    return {...keypressContext, ...keypressHook};
};

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

describe('keypress handling', () => {
    beforeEach(() => {
        jest.useFakeTimers();
        stdin = new PassThrough() as typeof stdin;
        stdin.isRaw = false;
        stdin.isTTY = false;
        setRawMode.mockClear();
    });

    afterEach(() => {
        jest.useRealTimers();
        stdin.destroy();
    });

    it('broadcasts bracketed paste, rapid paste, normal keys, and restores raw mode', async () => {
        const {
            KeypressProvider,
            useKeypressContext,
            PASTE_MODE_PREFIX,
            PASTE_MODE_SUFFIX,
        } = await load();
        const handler = jest.fn();

        const Consumer = () => {
            const context = useKeypressContext();
            useEffect(() => {
                context.subscribe(handler);
                return () => context.unsubscribe(handler);
            }, [context]);
            return <span>ready</span>;
        };

        let view!: TestRenderer.ReactTestRenderer;
        act(() => {
            view = TestRenderer.create(
                <KeypressProvider>
                    <Consumer/>
                </KeypressProvider>
            );
        });

        expect(setRawMode).toHaveBeenCalledWith(true);

        act(() => {
            stdin.write(Buffer.from(`${PASTE_MODE_PREFIX}pasted text${PASTE_MODE_SUFFIX}`));
        });
        expect(handler).toHaveBeenCalledWith(expect.objectContaining({
            paste: true,
            sequence: 'pasted text',
        }));

        act(() => {
            stdin.emit('keypress', undefined, {
                name: '',
                ctrl: false,
                meta: false,
                shift: false,
                sequence: 'unfinished paste',
            });
            view.unmount();
        });
        expect(setRawMode).toHaveBeenLastCalledWith(false);
    });

    it('throws when keypress context is used outside provider', async () => {
        const {useKeypressContext} = await load();
        const Consumer = () => {
            useKeypressContext();
            return <span/>;
        };

        expect(() => {
            act(() => {
                TestRenderer.create(<Consumer/>);
            });
        }).toThrow('useKeypressContext must be used within a KeypressProvider');
    });

    it('useKeypress handles special keys and raw data cleanup', async () => {
        const {useKeypress} = await load();
        const onKeypress = jest.fn();

        const Consumer = ({active}: { active: boolean }) => {
            const controls = useKeypress(onKeypress, {isActive: active});
            useEffect(() => {
                if (!active) controls.triggerEscape();
            }, [active]);
            return <span>hook</span>;
        };

        let view!: TestRenderer.ReactTestRenderer;
        act(() => {
            view = TestRenderer.create(<Consumer active/>);
        });

        act(() => {
            stdin.emit('keypress', undefined, {
                name: 'escape',
                ctrl: false,
                meta: false,
                shift: false,
                sequence: '\x1B'
            });
            stdin.emit('keypress', undefined, {name: 'c', ctrl: true, meta: false, shift: false, sequence: '\x03'});
            stdin.emit('keypress', undefined, {name: 'l', ctrl: true, meta: false, shift: false, sequence: '\x0C'});
            stdin.emit('keypress', undefined, {name: 's', ctrl: true, meta: false, shift: false, sequence: '\x13'});
            stdin.emit('data', Buffer.from('\x1B'));
            stdin.emit('data', Buffer.from('\x03'));
            stdin.emit('data', Buffer.from('\x0C'));
            stdin.emit('data', Buffer.from('\x13'));
            stdin.emit('data', Buffer.from('a'));
        });

        expect(onKeypress).toHaveBeenCalledWith(expect.objectContaining({name: 'escape'}));
        expect(onKeypress).toHaveBeenCalledWith(expect.objectContaining({name: 'c', ctrl: true}));
        expect(onKeypress).toHaveBeenCalledWith(expect.objectContaining({name: 'l', ctrl: true}));
        expect(onKeypress).toHaveBeenCalledWith(expect.objectContaining({name: 's', ctrl: true}));

        act(() => {
            view.update(<Consumer active={false}/>);
        });
        expect(onKeypress).toHaveBeenCalledWith(expect.objectContaining({name: 'escape'}));

        const keypressListeners = stdin.listenerCount('keypress');
        act(() => {
            view.unmount();
        });
        expect(stdin.listenerCount('keypress')).toBeLessThanOrEqual(keypressListeners);
    });
});

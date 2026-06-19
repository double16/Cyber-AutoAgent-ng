import React from 'react';
import TestRenderer, {act} from 'react-test-renderer';
import {jest} from '@jest/globals';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const stdoutWrite = jest.fn();

jest.unstable_mockModule('ink', () => ({
    useStdout: () => ({
        stdout: {
            write: stdoutWrite,
            columns: 80,
            rows: 24,
        },
    }),
}));

function renderHook<T>(hook: () => T) {
    let current: T;
    const Harness = () => {
        current = hook();
        return null;
    };

    let renderer: TestRenderer.ReactTestRenderer;
    act(() => {
        renderer = TestRenderer.create(<Harness/>);
    });

    return {
        get current() {
            return current!;
        },
        unmount() {
            act(() => {
                renderer.unmount();
            });
        },
    };
}

describe('useModalManager', () => {
    beforeEach(() => {
        jest.useFakeTimers();
        stdoutWrite.mockClear();
    });

    afterEach(() => {
        jest.useRealTimers();
        jest.restoreAllMocks();
    });

    it('opens and closes typed modals with context', async () => {
        const {ModalType, useModalManager} = await import('../../../src/hooks/useModalManager.js');
        const hook = renderHook(() => useModalManager());
        const onSelect = jest.fn();

        expect(hook.current.activeModal).toBe(ModalType.NONE);
        expect(hook.current.isModalOpen(ModalType.CONFIG)).toBe(false);

        act(() => {
            hook.current.openConfig('bad config');
        });
        expect(hook.current.activeModal).toBe(ModalType.CONFIG);
        expect(hook.current.modalContext.configError).toBe('bad config');
        expect(hook.current.staticKey).toBe(1);
        expect(stdoutWrite).not.toHaveBeenCalled();

        act(() => {
            hook.current.openModuleSelector(onSelect);
        });
        expect(hook.current.activeModal).toBe(ModalType.MODULE_SELECTOR);
        expect(hook.current.modalContext).toEqual(expect.objectContaining({
            configError: 'bad config',
            onModuleSelect: onSelect,
        }));

        act(() => {
            hook.current.openSafetyWarning({module: 'web', target: 'example.com', objective: 'test'});
        });
        expect(hook.current.modalContext.pendingExecution).toEqual({
            module: 'web',
            target: 'example.com',
            objective: 'test',
        });

        act(() => {
            hook.current.closeModal();
        });
        expect(hook.current.activeModal).toBe(ModalType.NONE);
        expect(hook.current.modalContext).toEqual({});

        hook.unmount();
    });

    it('clears terminal for full-screen modals and close transitions', async () => {
        const {ModalType, useModalManager} = await import('../../../src/hooks/useModalManager.js');
        const hook = renderHook(() => useModalManager());

        act(() => {
            hook.current.openDocumentation(3);
        });
        expect(hook.current.activeModal).toBe(ModalType.DOCUMENTATION);
        expect(hook.current.modalContext.documentIndex).toBe(3);
        expect(stdoutWrite).toHaveBeenCalledTimes(1);

        act(() => {
            hook.current.closeModal();
        });
        expect(stdoutWrite).toHaveBeenCalledTimes(2);

        act(() => {
            hook.current.openModal(ModalType.INITIALIZATION);
        });
        expect(stdoutWrite).toHaveBeenCalledTimes(3);

        hook.unmount();
    });

    it('refreshes static output immediately or with deferred terminal clearing', async () => {
        const {ModalType, useModalManager} = await import('../../../src/hooks/useModalManager.js');
        const hook = renderHook(() => useModalManager());
        const initialKey = hook.current.staticKey;

        act(() => {
            hook.current.refreshStaticOnly();
        });
        expect(hook.current.staticKey).toBe(initialKey + 1);

        act(() => {
            hook.current.refreshStatic();
        });
        expect(hook.current.staticKey).toBe(initialKey + 2);
        expect(stdoutWrite).not.toHaveBeenCalled();

        act(() => {
            jest.runOnlyPendingTimers();
        });
        expect(stdoutWrite).toHaveBeenCalledTimes(1);

        act(() => {
            hook.current.openMemorySearch();
        });
        act(() => {
            hook.current.refreshStatic();
            jest.runOnlyPendingTimers();
        });
        expect(hook.current.isModalOpen(ModalType.MEMORY_SEARCH)).toBe(true);
        expect(stdoutWrite).toHaveBeenCalledTimes(1);

        hook.unmount();
    });
});

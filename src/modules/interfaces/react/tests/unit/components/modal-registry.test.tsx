import React from 'react';
import TestRenderer, {act} from 'react-test-renderer';
import {describe, expect, it, jest} from '@jest/globals';
import {ModalType} from '../../../src/hooks/useModalManager.js';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

jest.unstable_mockModule('../../../src/components/LazyComponents.js', () => ({
    ConfigEditorLazy: ({onClose}: any) => <button onClick={onClose}>config</button>,
    ModuleSelectorLazy: ({onClose, onSelect}: any) => (
        <div>
            <button onClick={() => onSelect('api')}>module</button>
            <button onClick={onClose}>module-close</button>
        </div>
    ),
    DocumentationViewerLazy: ({onClose, selectedDoc}: any) => <button onClick={onClose}>doc:{selectedDoc}</button>,
}));

jest.unstable_mockModule('../../../src/components/SafetyWarning.js', () => ({
    SafetyWarning: ({target, module, onConfirm, onCancel}: any) => (
        <div>
            <span>safety:{module}:{target}</span>
            <button onClick={onConfirm}>confirm</button>
            <button onClick={onCancel}>cancel</button>
        </div>
    ),
}));

jest.unstable_mockModule('../../../src/components/InitializationFlow.js', () => ({
    InitializationFlow: ({onComplete}: any) => <button onClick={onComplete}>initialization</button>,
}));

const load = async () => {
    const {ModalRegistry} = await import('../../../src/components/ModalRegistry.js');
    return {ModalRegistry};
};

describe('ModalRegistry', () => {
    it('renders nothing for none and memory search modals', async () => {
        const {ModalRegistry} = await load();
        let view!: TestRenderer.ReactTestRenderer;
        act(() => {
            view = TestRenderer.create(
                <ModalRegistry activeModal={ModalType.NONE} modalContext={{}} onClose={jest.fn()} terminalWidth={100}/>
            );
        });
        expect(view.toJSON()).toBeNull();
        act(() => {
            view = TestRenderer.create(
                <ModalRegistry activeModal={ModalType.MEMORY_SEARCH} modalContext={{}} onClose={jest.fn()} terminalWidth={100}/>
            );
        });
        expect(view.toJSON()).toBeNull();
    });

    it('routes modal-specific callbacks', async () => {
        const {ModalRegistry} = await load();
        const onClose = jest.fn();
        const addOperationHistoryEntry = jest.fn();
        const onSafetyConfirm = jest.fn();
        const setIsFirstRunExperience = jest.fn();
        const setIsConfigurationModalOpen = jest.fn();
        const onModuleSelect = jest.fn();

        let view!: TestRenderer.ReactTestRenderer;
        act(() => {
            view = TestRenderer.create(
                <ModalRegistry
                    activeModal={ModalType.CONFIG}
                    modalContext={{}}
                    onClose={onClose}
                    terminalWidth={100}
                    isFirstRunExperience
                    addOperationHistoryEntry={addOperationHistoryEntry}
                    setIsFirstRunExperience={setIsFirstRunExperience}
                />
            );
        });
        act(() => view.root.findByType('button').props.onClick());
        expect(onClose).toHaveBeenCalled();
        expect(addOperationHistoryEntry).toHaveBeenCalledWith('info', expect.stringContaining('Configuration complete'));
        expect(setIsFirstRunExperience).toHaveBeenCalledWith(false);

        act(() => {
            view = TestRenderer.create(
                <ModalRegistry
                    activeModal={ModalType.MODULE_SELECTOR}
                    modalContext={{onModuleSelect}}
                    onClose={onClose}
                    terminalWidth={100}
                />
            );
        });
        act(() => view.root.findAllByType('button')[0].props.onClick());
        expect(onModuleSelect).toHaveBeenCalledWith('api');

        act(() => {
            view = TestRenderer.create(
                <ModalRegistry
                    activeModal={ModalType.SAFETY_WARNING}
                    modalContext={{pendingExecution: {target: 'example.com', module: 'web'}}}
                    onClose={onClose}
                    terminalWidth={100}
                    onSafetyConfirm={onSafetyConfirm}
                />
            );
        });
        act(() => view.root.findAllByType('button')[0].props.onClick());
        expect(onSafetyConfirm).toHaveBeenCalled();

        act(() => {
            view = TestRenderer.create(
                <ModalRegistry
                    activeModal={ModalType.INITIALIZATION}
                    modalContext={{}}
                    onClose={onClose}
                    terminalWidth={100}
                    setIsConfigurationModalOpen={setIsConfigurationModalOpen}
                />
            );
        });
        act(() => view.root.findByType('button').props.onClick());
        expect(setIsConfigurationModalOpen).toHaveBeenCalledWith(true);

        act(() => {
            view = TestRenderer.create(
                <ModalRegistry
                    activeModal={ModalType.DOCUMENTATION}
                    modalContext={{documentIndex: 2}}
                    onClose={onClose}
                    terminalWidth={100}
                />
            );
        });
        expect(JSON.stringify(view.toJSON())).toContain('doc:');
    });

    it('handles optional modal context and fallback sizing paths', async () => {
        const {ModalRegistry} = await load();
        const onClose = jest.fn();
        const addOperationHistoryEntry = jest.fn();
        const setIsFirstRunExperience = jest.fn();
        const originalColumns = (process as any).stdout?.columns;
        Object.defineProperty(process.stdout, 'columns', {value: 30, configurable: true});

        let view!: TestRenderer.ReactTestRenderer;
        act(() => {
            view = TestRenderer.create(
                <ModalRegistry
                    activeModal={ModalType.CONFIG}
                    modalContext={{}}
                    onClose={onClose}
                    terminalWidth={0}
                    isFirstRunExperience={false}
                    addOperationHistoryEntry={addOperationHistoryEntry}
                    setIsFirstRunExperience={setIsFirstRunExperience}
                />
            );
        });
        act(() => view.root.findByType('button').props.onClick());
        expect(onClose).toHaveBeenCalled();
        expect(addOperationHistoryEntry).not.toHaveBeenCalled();
        expect(setIsFirstRunExperience).not.toHaveBeenCalled();

        act(() => {
            view = TestRenderer.create(
                <ModalRegistry
                    activeModal={ModalType.MODULE_SELECTOR}
                    modalContext={{}}
                    onClose={onClose}
                    terminalWidth={0}
                />
            );
        });
        expect(() => {
            act(() => view.root.findAllByType('button')[0].props.onClick());
        }).not.toThrow();

        act(() => {
            view = TestRenderer.create(
                <ModalRegistry
                    activeModal={ModalType.SAFETY_WARNING}
                    modalContext={{}}
                    onClose={onClose}
                    terminalWidth={0}
                />
            );
        });
        expect(view.root.findAllByType('button')).toHaveLength(0);

        act(() => {
            view = TestRenderer.create(
                <ModalRegistry
                    activeModal={'unknown' as ModalType}
                    modalContext={{}}
                    onClose={onClose}
                    terminalWidth={0}
                />
            );
        });
        expect(view.toJSON()).toBeNull();

        Object.defineProperty(process.stdout, 'columns', {value: originalColumns, configurable: true});
    });
});

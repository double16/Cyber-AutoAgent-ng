import React from 'react';
import {TextDecoder, TextEncoder} from 'util';
import {jest} from '@jest/globals';

if (typeof global.TextEncoder === 'undefined') {
    global.TextEncoder = TextEncoder;
}
if (typeof global.TextDecoder === 'undefined') {
    global.TextDecoder = TextDecoder as typeof global.TextDecoder;
}

jest.unstable_mockModule('ink-spinner', () => ({
    default: () => <span>spinner</span>,
}));

const load = async () => {
    const [{render}, icons] = await Promise.all([
        import('ink-testing-library'),
        import('../../../src/components/icons.js'),
    ]);
    return {render, icons};
};

describe('icon components', () => {
    it('renders all status icons', async () => {
        const {render, icons} = await load();

        for (const Icon of Object.values(icons.StatusIcons)) {
            const frame = render(<Icon/>).lastFrame();
            expect(frame.length).toBeGreaterThan(0);
        }
    });

    it('renders tool status indicators in compact and expanded modes', async () => {
        const {render, icons} = await load();
        const statuses = ['pending', 'executing', 'success', 'error', 'canceled', 'confirming'] as const;

        for (const status of statuses) {
            expect(render(<icons.ToolStatusIndicator status={status}/>).lastFrame()).toContain(
                status === 'executing' ? 'spinner' : status === 'canceled' ? 'Canceled' : ''
            );
            expect(render(<icons.ToolStatusIndicator status={status} compact/>).lastFrame().length).toBeGreaterThan(0);
        }
    });

    it('renders progress, connection, bullet, divider, and log indicators', async () => {
        const {render, icons} = await load();

        expect(render(<icons.ProgressIndicator current={3} total={4} width={8}/>).lastFrame()).toContain('75%');
        expect(render(<icons.ProgressIndicator current={1} total={2}
                                               showPercentage={false}/>).lastFrame()).not.toContain('%');

        for (const status of ['connected', 'connecting', 'disconnected', 'error'] as const) {
            expect(render(<icons.ConnectionStatus status={status}/>).lastFrame().length).toBeGreaterThan(0);
            expect(render(<icons.ConnectionStatus status={status}
                                                  showLabel={false}/>).lastFrame()).not.toContain('Connected');
        }

        expect(render(<icons.Bullet level={3}/>).lastFrame()).toContain('[ ]');
        expect(render(<icons.Divider width={4} char="="/>).lastFrame()).toContain('====');

        for (const level of ['info', 'success', 'warning', 'error', 'debug'] as const) {
            expect(render(<icons.LogLevelIcon level={level}/>).lastFrame()).toContain('[');
        }
    });

    it('renders security and step indicators', async () => {
        const {render, icons} = await load();

        for (const type of ['scanning', 'vulnerable', 'secure', 'unknown'] as const) {
            expect(render(<icons.SecurityIcon type={type}/>).lastFrame().length).toBeGreaterThan(0);
        }

        expect(render(<icons.StepIndicator current={2} total={5}/>).lastFrame()).toContain('Step 2/5');
        expect(render(<icons.StepIndicator current={5} total={5}
                                           status="completed"/>).lastFrame()).toContain('Step 5/5');
        expect(render(<icons.StepIndicator current={1} total={5} status="pending"/>).lastFrame()).toContain('Step 1/5');
    });
});

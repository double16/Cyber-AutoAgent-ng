import {InputParser} from '../../../src/services/InputParser.js';

describe('InputParser', () => {
    let parser: InputParser;

    beforeEach(() => {
        parser = new InputParser();
    });

    it('parses slash commands with arguments', () => {
        expect(parser.parse('/config provider bedrock')).toEqual({
            type: 'slash',
            command: 'config',
            args: ['provider', 'bedrock'],
            confidence: 1,
        });
    });

    it('parses guided flow commands', () => {
        expect(parser.parse('module web')).toEqual(expect.objectContaining({
            type: 'flow',
            command: 'module',
            module: 'web',
            confidence: 1,
        }));

        expect(parser.parse('target https://example.com')).toEqual(expect.objectContaining({
            type: 'flow',
            command: 'target',
            target: 'https://example.com',
        }));

        expect(parser.parse('objective focus on auth bypass')).toEqual(expect.objectContaining({
            type: 'flow',
            command: 'objective',
            objective: 'focus on auth bypass',
        }));

        expect(parser.parse('execute')).toEqual(expect.objectContaining({
            type: 'flow',
            command: 'execute',
        }));
    });

    it('normalizes natural language domain targets and keeps explicit URL targets', () => {
        expect(parser.parse('scan example.com for SQL injection')).toEqual(expect.objectContaining({
            type: 'natural',
            target: 'https://example.com',
            objective: 'SQL injection',
            confidence: 0.8,
        }));

        expect(parser.parse('audit https://api.example.com focusing on auth')).toEqual(expect.objectContaining({
            type: 'natural',
            target: 'https://api.example.com',
            objective: 'auth',
            confidence: 0.8,
        }));
    });

    it('parses search-style natural language commands', () => {
        expect(parser.parse('find admin.example.org on production scope')).toEqual(expect.objectContaining({
            type: 'natural',
            target: 'https://admin.example.org',
            objective: 'production scope',
            confidence: 0.8,
        }));
    });

    it('falls back to simple action plus target extraction', () => {
        expect(parser.parse('please check 192.168.1.10 quickly')).toEqual(expect.objectContaining({
            type: 'natural',
            target: '192.168.1.10',
            objective: '',
            confidence: 0.7,
        }));
    });

    it('returns unknown for empty or unrecognized input', () => {
        expect(parser.parse('   ')).toEqual({type: 'unknown', confidence: 0});
        expect(parser.parse('hello there')).toEqual({type: 'unknown', confidence: 0});
        expect(parser.parse('scan something-that-is-not-a-target')).toEqual({type: 'unknown', confidence: 0});
    });

    it('stores available modules and returns generic module descriptions', () => {
        parser.setAvailableModules(['web', 'ctf']);

        expect(parser.getAvailableModules()).toEqual(['web', 'ctf']);
        expect(parser.getModuleDescription('web')).toBe('Security assessment module');
    });
});

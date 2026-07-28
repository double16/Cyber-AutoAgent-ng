/**
 * Tool formatter tests for specialist tools
 *
 * Tests the formatting of validation_specialist and mem0 tool inputs
 * for display in the UI.
 */
import { describe, it, expect } from '@jest/globals';

describe('Specialist tool formatters', () => {
  describe('validation_specialist formatter', () => {
    it('formats validation specialist with finding and artifacts', async () => {
      const mod: any = await import('../../../src/utils/toolFormatters.js');
      const { toolFormatters } = mod;

      const input = {
        finding_description: 'SQL injection in /api/users?id=1 allows data extraction',
        artifact_paths: ['baseline.html', 'exploit.html'],
        claimed_severity: 'HIGH'
      };

      const formatted = toolFormatters.validation_specialist(input);

      expect(formatted).toContain('validating finding');
      expect(formatted).toContain('2 artifacts');
      expect(formatted).toContain('SQL injection');
    });

    it('truncates long finding descriptions', async () => {
      const mod: any = await import('../../../src/utils/toolFormatters.js');
      const { toolFormatters } = mod;

      const longFinding = 'A'.repeat(100);
      const input = {
        finding_description: longFinding,
        artifact_paths: ['test.html']
      };

      const formatted = toolFormatters.validation_specialist(input);

      // Should be truncated to ~60 chars + "..."
      expect(formatted.length).toBeLessThan(100);
      expect(formatted).toContain('...');
    });

    it('handles empty artifact_paths array', async () => {
      const mod: any = await import('../../../src/utils/toolFormatters.js');
      const { toolFormatters } = mod;

      const input = {
        finding_description: 'Test finding',
        artifact_paths: []
      };

      const formatted = toolFormatters.validation_specialist(input);

      expect(formatted).toContain('0 artifacts');
      expect(formatted).toContain('Test finding');
    });

    it('handles camelCase field names (artifactPaths)', async () => {
      const mod: any = await import('../../../src/utils/toolFormatters.js');
      const { toolFormatters } = mod;

      const input = {
        finding: 'XSS vulnerability',
        artifactPaths: ['poc1.html', 'poc2.html', 'poc3.html']
      };

      const formatted = toolFormatters.validation_specialist(input);

      expect(formatted).toContain('3 artifacts');
      expect(formatted).toContain('XSS');
    });

    it('handles missing fields gracefully', async () => {
      const mod: any = await import('../../../src/utils/toolFormatters.js');
      const { toolFormatters } = mod;

      const input = {};

      const formatted = toolFormatters.validation_specialist(input);

      expect(formatted).toContain('0 artifacts');
      // Should not crash
      expect(typeof formatted).toBe('string');
    });
  });

  describe('mem0 formatter enhancements', () => {
    it('formats list action without truncating JSON', async () => {
      const mod: any = await import('../../../src/utils/toolFormatters.js');
      const { toolFormatters } = mod;

      const input = {
        query: 'vulnerabilities'
      };

      const formatted = toolFormatters.mem0_list(input);

      expect(formatted).toContain('list memories');
      // Should NOT show truncated JSON
      expect(formatted).not.toContain('[{');
    });

    it('formats retrieve action without truncating JSON', async () => {
      const mod: any = await import('../../../src/utils/toolFormatters.js');
      const { toolFormatters } = mod;

      const input = {
        query: 'findings'
      };

      const formatted = toolFormatters.mem0_retrieve(input);

      expect(formatted).toContain('retrieve memories');
      expect(formatted).not.toContain('[{');
    });

    it('shows previews for typed memory actions', async () => {
      const mod: any = await import('../../../src/utils/toolFormatters.js');
      const { toolFormatters } = mod;

      expect(toolFormatters.store_observation({content: 'Observed SQLi', artifacts: []}))
        .toContain('Observed SQLi');
      expect(toolFormatters.store_knowledge({content: 'Test with a control'}))
        .toContain('storing knowledge');
    });

    it('formats finding candidates with verification language', async () => {
      const mod: any = await import('../../../src/utils/toolFormatters.js');
      const { toolFormatters } = mod;

      const formatted = toolFormatters.store_finding({
        title: 'SQL injection',
        severity: 'HIGH',
        target: '/search',
        artifacts: ['response.txt'],
      });

      expect(formatted).toContain('submitting finding for verification');
      expect(formatted).toContain('severity: HIGH');
      expect(formatted).toContain('1 artifact');
    });

    it('handles missing typed memory input gracefully', async () => {
      const mod: any = await import('../../../src/utils/toolFormatters.js');
      const { toolFormatters } = mod;

      expect(toolFormatters.store_observation(null)).toContain('storing observation');
      expect(toolFormatters.store_knowledge(undefined)).toContain('storing knowledge');
      expect(toolFormatters.store_finding({})).toContain('severity: UNKNOWN');
      expect(toolFormatters.record_finding_validation({})).toContain('outcome: unknown');
    });

  });

  describe('formatter error handling', () => {
    it('handles null input', async () => {
      const mod: any = await import('../../../src/utils/toolFormatters.js');
      const { toolFormatters } = mod;

      const formatted = toolFormatters.validation_specialist(null as any);

      // Should not crash
      expect(typeof formatted).toBe('string');
    });

    it('handles undefined input', async () => {
      const mod: any = await import('../../../src/utils/toolFormatters.js');
      const { toolFormatters } = mod;

      const formatted = toolFormatters.validation_specialist(undefined as any);

      // Should not crash
      expect(typeof formatted).toBe('string');
    });

    it('handles non-object input', async () => {
      const mod: any = await import('../../../src/utils/toolFormatters.js');
      const { toolFormatters } = mod;

      const formatted = toolFormatters.validation_specialist('string input' as any);

      // Should not crash
      expect(typeof formatted).toBe('string');
    });
  });
});

import {afterEach, describe, expect, it, jest} from '@jest/globals';
const execFile = jest.fn<any>();
jest.unstable_mockModule('node:child_process', () => ({execFile}));
const {getDocumentationUrl} = await import('../../../src/utils/documentationLinks.js');
const env = {...process.env};
afterEach(() => {process.env = {...env}; execFile.mockReset();});
function git(remote: string | null, branch: string | null) {
  execFile.mockImplementation((_command: string, args: string[], cb: any) => {
    const value = args.includes('config') ? remote : branch;
    cb(value === null ? new Error('no git') : null, {stdout: value ?? ''});
  });
}
describe('documentation repository links', () => {
  it.each([
    ['git@github.com:team/project.git', 'https://github.com/team/project'],
    ['ssh://git@example.com/team/project.git', 'https://example.com/team/project'],
    ['https://example.com/team/project.git', 'https://example.com/team/project'],
    ['http://example.com/team/project', 'http://example.com/team/project'],
    ['git@invalid', 'https://github.com/double16/Cyber-AutoAgent-ng'],
    ['local/path', 'https://github.com/double16/Cyber-AutoAgent-ng'],
    ['  ', 'https://github.com/double16/Cyber-AutoAgent-ng'],
  ])('normalizes %s', async (remote, expected) => {
    git(remote, 'feature/docs');
    await expect(getDocumentationUrl('guide.md', '/repo')).resolves.toBe(`${expected}/blob/feature%2Fdocs/docs/guide.md`);
  });
  it('falls back to environment and defaults outside git', async () => {
    git(null, null);
    process.env.CYBER_REPOSITORY_URL = 'https://example.com/fork.git';
    process.env.CYBER_REPOSITORY_BRANCH = 'release';
    await expect(getDocumentationUrl('guide.md')).resolves.toBe('https://example.com/fork/blob/release/docs/guide.md');
    delete process.env.CYBER_REPOSITORY_URL;
    delete process.env.CYBER_REPOSITORY_BRANCH;
    git('', '');
    await expect(getDocumentationUrl('guide.md')).resolves.toContain('/blob/main/docs/guide.md');
  });
});

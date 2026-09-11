import {execFile} from "node:child_process";
import {promisify} from "node:util";

const execFileAsync = promisify(execFile);
const DEFAULT_REPOSITORY_URL = "https://github.com/double16/Cyber-AutoAgent-ng";
const DEFAULT_BRANCH = "main";

const normalizeRemoteUrl = (remote: string): string | null => {
  const value = remote.trim().replace(/\.git$/, "");
  if (!value) return null;

  if (value.startsWith("git@")) {
    const [, host, repository] = value.match(/^git@([^:]+):(.+)$/) ?? [];
    return host && repository ? `https://${host}/${repository}` : null;
  }

  if (value.startsWith("ssh://")) {
    return value.replace(/^ssh:\/\//, "https://").replace(/^https:\/\/git@/, "https://");
  }

  if (/^https?:\/\//.test(value)) return value;
  return null;
};

const readGitValue = async (cwd: string, args: string[]): Promise<string | null> => {
  try {
    const result = await execFileAsync("git", ["-C", cwd, ...args]);
    return result.stdout.trim() || null;
  } catch {
    return null;
  }
};

export const getDocumentationUrl = async (filename: string, cwd = process.cwd()): Promise<string> => {
  const remote = await readGitValue(cwd, ["config", "--get", "remote.origin.url"]);
  const branch = await readGitValue(cwd, ["branch", "--show-current"]);
  const repositoryUrl = normalizeRemoteUrl(remote ?? process.env.CYBER_REPOSITORY_URL ?? DEFAULT_REPOSITORY_URL)
    ?? DEFAULT_REPOSITORY_URL;
  const branchName = branch ?? process.env.CYBER_REPOSITORY_BRANCH ?? DEFAULT_BRANCH;

  return `${repositoryUrl}/blob/${encodeURIComponent(branchName)}/docs/${filename}`;
};


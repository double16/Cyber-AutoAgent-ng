/** Resolve the TUI's single proxy setting into HTTP and HTTPS environment variables. */
export function resolveHttpProxyEnvironment(value?: string): Record<string, string> {
  const proxyUrl = value?.trim();
  if (!proxyUrl) {
    return {};
  }

  try {
    const parsed = new URL(proxyUrl);
    if ((parsed.protocol === 'http:' || parsed.protocol === 'https:') && parsed.hostname) {
      return {
        http_proxy: proxyUrl,
        https_proxy: proxyUrl,
        HTTP_PROXY: proxyUrl,
        HTTPS_PROXY: proxyUrl,
      };
    }
  } catch {
    // The same validation message applies to malformed and unsupported URLs.
  }

  throw new Error('Invalid HTTP proxy URL. Use an http:// or https:// URL with a host.');
}

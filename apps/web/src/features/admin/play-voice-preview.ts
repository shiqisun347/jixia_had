export async function playVoicePreview({
  cachedUrl,
  generate,
  makeUrl,
  play,
}: {
  cachedUrl?: string;
  generate: () => Promise<void>;
  makeUrl: () => string;
  play: (url: string) => Promise<void>;
}): Promise<{ url: string; played: boolean }> {
  let url = cachedUrl;
  if (!url) {
    await generate();
    url = makeUrl();
  }
  try {
    await play(url);
    return { url, played: true };
  } catch {
    return { url, played: false };
  }
}

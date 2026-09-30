// Launch the pinned Chromium (Playwright build 1194 = Chromium 141.0.7390.37).
// Never runs `playwright install`; uses the preinstalled browser.
import fs from 'node:fs';
import { chromium, type Browser } from 'playwright';

const CANDIDATES = [
  process.env.LADDER_CHROMIUM,
  '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
].filter(Boolean) as string[];

export async function launchChromium(): Promise<Browser> {
  process.env.PLAYWRIGHT_BROWSERS_PATH ??= '/opt/pw-browsers';
  const executablePath = CANDIDATES.find((p) => fs.existsSync(p));
  return chromium.launch(executablePath ? { executablePath } : {});
}

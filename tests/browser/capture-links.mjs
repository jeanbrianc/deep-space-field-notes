// Run against the isolated dev server. Does not visit supplier checkout URLs.
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
import identities from '../../app/capture-identities.json' with { type: 'json' };
import catalog from '../../print_products/catalog.json' with { type: 'json' };
import comparisons from '../../app/comparisons.generated.json' with { type: 'json' };
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.PREVIEW_URL || 'http://localhost:5173';
const browser = await chromium.launch({ headless: true, ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}) });
const errors = [];
const artifacts = process.env.BROWSER_ARTIFACT_DIR || "outputs/capture-links";
await mkdir(artifacts, { recursive: true });
async function waitCapture(page, id) {
  await page.waitForFunction(id => new URL(location.href).searchParams.get('capture') === id && !!document.querySelector('.capture-frame img') && document.querySelector('.portal-stage')?.getAttribute('aria-busy') === 'false', id);
  assert.equal(await page.getByRole('link', { name: 'Capture link', exact: true }).getAttribute('href'), `/?capture=${id}`);
  assert.equal(await page.locator('.field-note .print-link').getAttribute('href'), `/prints/${id}`);
}
try {
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    const context = await browser.newContext({ viewport, reducedMotion: 'reduce' });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    for (const { id, file } of identities) {
      await page.goto(`${base}/?capture=${id}`);
      await waitCapture(page, id);
      const selected = await page.locator('.capture-frame img').getAttribute('src');
      const comparison = comparisons.captures.find(x => x.baseline.filename === file);
      const expected = comparison?.curatedDefault === 'nightskyai'
        ? `/comparisons/${comparison.nightSkyAI.alignment?.sourceFilename ?? comparison.nightSkyAI.filename}`
        : `/images/${encodeURIComponent(file)}`;
      assert.equal(selected, expected);
      await page.reload(); await waitCapture(page, id);
      assert.equal(await page.locator('.capture-frame img').getAttribute('src'), selected);
      await page.locator('.field-note .print-link').click();
      await page.waitForURL(`**/prints/${id}`);
      assert.equal(await page.getByRole('link', { name: 'Back to the observatory' }).getAttribute('href'), `/?capture=${id}`);
      assert.match(await page.locator('.print-disclosure').first().innerText(), /crop differs/);
      assert.match(await page.locator('.print-size').innerText(), /\$20 USD/);
      assert.equal(await page.getByRole('link', { name: 'Buy this print', exact: true }).getAttribute('href'), catalog.products.find(x => x.id === id).checkoutUrl);
      await page.getByRole('link', { name: 'Back to the observatory' }).click(); await waitCapture(page, id);
      assert.equal(await page.locator('.capture-frame img').getAttribute('src'), selected);
    }
    console.log(JSON.stringify({ viewport, directReloadPrintReturn: 33 }));
    await page.goto(base); await page.getByRole('button', { name: 'Open the telescope on the Andromeda Galaxy' }).click(); await waitCapture(page, 'andromeda-galaxy');
    const first = page.url();
    await page.getByRole('button', { name: 'Next capture', exact: true }).click();
    await page.waitForFunction(first => location.href !== first, first);
    const next = page.url(); const nextId = new URL(next).searchParams.get('capture'); await waitCapture(page, nextId);
    await page.goBack(); await waitCapture(page, 'andromeda-galaxy');
    await page.goBack(); await page.getByRole('button', { name: 'Open the telescope on the Andromeda Galaxy' }).waitFor();
    await page.goForward(); await waitCapture(page, 'andromeda-galaxy');
    await page.goForward(); await waitCapture(page, nextId);
    await page.getByRole('button', { name: 'Previous capture', exact: true }).click(); await waitCapture(page, 'andromeda-galaxy');
    await page.evaluate(() => { const button = document.querySelector('[aria-label="Next capture"]'); for (let i = 0; i < 10; i++) button.click(); });
    await waitCapture(page, nextId); await page.goBack(); await waitCapture(page, 'andromeda-galaxy');
    const newTab = await context.newPage(); await newTab.goto(page.url()); await waitCapture(newTab, 'andromeda-galaxy'); await newTab.close();
    await page.evaluate(() => document.activeElement?.blur());
    await page.keyboard.press('ArrowRight'); await waitCapture(page, nextId);
    await page.keyboard.press('ArrowLeft'); await waitCapture(page, 'andromeda-galaxy');
    await page.locator('.capture-frame').evaluate(element => {
      const start = new Touch({ identifier: 2, target: element, clientX: 250, clientY: 100 });
      const end = new Touch({ identifier: 2, target: element, clientX: 50, clientY: 100 });
      element.dispatchEvent(new TouchEvent('touchstart', { bubbles: true, touches: [start] }));
      element.dispatchEvent(new TouchEvent('touchend', { bubbles: true, changedTouches: [end] }));
    }); await waitCapture(page, nextId);
    await page.getByRole('button', { name: 'Previous capture', exact: true }).click(); await waitCapture(page, 'andromeda-galaxy');
    // Deterministic clipboard success and denial, exercising both UI outcomes.
    await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async text => { window.__copiedCapture = text; } } }));
    await page.getByRole('button', { name: 'Copy capture link' }).click(); await page.getByText('Capture link copied.', { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => window.__copiedCapture), page.url());
    await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async () => { throw new Error('denied'); } } }));
    await page.getByRole('button', { name: 'Copy capture link' }).click();
    const fallback = page.getByLabel('Capture URL', { exact: true }); await fallback.waitFor(); assert.equal(await fallback.inputValue(), page.url());
    await fallback.focus(); const before = page.url(); await page.keyboard.press('ArrowRight'); assert.equal(page.url(), before);
    await page.getByRole('button', { name: 'Copy capture link' }).focus(); await page.keyboard.press('ArrowRight'); assert.equal(page.url(), before);
    // Interactive swipe must not navigate the gallery.
    await fallback.evaluate(element => {
      const start = new Touch({ identifier: 1, target: element, clientX: 250, clientY: 100 });
      const end = new Touch({ identifier: 1, target: element, clientX: 50, clientY: 100 });
      element.dispatchEvent(new TouchEvent('touchstart', { bubbles: true, touches: [start] }));
      element.dispatchEvent(new TouchEvent('touchend', { bubbles: true, changedTouches: [end] }));
    }); assert.equal(page.url(), before);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: `${artifacts}/share-fallback-${viewport.width}.png`, fullPage: true });
    for (const invalid of ['missing', '%E0%A4%A', 'https%3A%2F%2Fevil.test', 'andromeda-galaxy&capture=orion-nebula']) {
      await page.goto(`${base}/?capture=${invalid}`); await page.getByRole('button', { name: 'Open the telescope on the Andromeda Galaxy' }).waitFor(); assert.equal(await page.locator('.capture-frame').count(), 0);
    }
    await page.goto(`${base}/prints/andromeda-galaxy?return=https://evil.test&capture=orion-nebula`);
    assert.equal(await page.getByRole('link', { name: 'Back to the observatory' }).getAttribute('href'), '/?capture=andromeda-galaxy');
    console.log(JSON.stringify({ viewport, history: true, rapidClicks: true, newTab: true, clipboard: true, invalidInputs: true, keyboardAndTouchIsolation: true }));
    await context.close();
  }
  // Normal-motion timer cancellation on Back, both before and after URL commit.
  const page = await browser.newPage({ reducedMotion: 'no-preference' });
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base); await page.getByRole('button', { name: 'Open the telescope on the Andromeda Galaxy' }).click(); await waitCapture(page, 'andromeda-galaxy');
  await page.getByRole('button', { name: 'Next capture', exact: true }).click(); await page.waitForTimeout(100); await page.goBack();
  await page.waitForTimeout(3100); assert.equal(new URL(page.url()).search, ''); assert.equal(await page.locator('.capture-frame').count(), 0);
  await page.goForward(); await waitCapture(page, 'andromeda-galaxy');
  await page.evaluate(() => { const button = document.querySelector('[aria-label="Next capture"]'); for (let i = 0; i < 10; i++) button.click(); });
  await page.waitForFunction(() => new URL(location.href).searchParams.get('capture') !== 'andromeda-galaxy');
  await page.goBack(); await waitCapture(page, 'andromeda-galaxy'); await page.waitForTimeout(3100); await waitCapture(page, 'andromeda-galaxy');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ normalMotionHistoryCancellation: true, pageErrors: errors }));
  await page.close();
} finally { await browser.close(); }

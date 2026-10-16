#!/usr/bin/env node
import { createRequire } from 'node:module';
import { mkdir, readdir, writeFile, rename, stat } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';

const BASE_URL = 'http://127.0.0.1:8795';
const API_URL = `${BASE_URL}/api/run`;
const MAX_RUN_MS = 180_000;

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    const name = argv[i];
    if (!['--mode', '--output-dir', '--playwright-module', '--browser-cache', '--browser-executable'].includes(name)) {
      throw new Error(`Unknown argument: ${name}`);
    }
    if (args[name]) throw new Error(`Duplicate argument: ${name}`);
    const value = argv[++i];
    if (!value || value.startsWith('--')) throw new Error(`Missing value for ${name}`);
    args[name] = value;
  }
  if (!['scripted', 'nova'].includes(args['--mode'])) {
    throw new Error('Pass an explicit --mode scripted or --mode nova.');
  }
  if (!args['--output-dir']) throw new Error('--output-dir is required and must be a new empty directory.');
  if (!args['--playwright-module']) throw new Error('--playwright-module must point to the existing Playwright installation.');
  return args;
}

function now() { return new Date().toISOString(); }

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const outputDir = path.resolve(args['--output-dir']);
  await mkdir(outputDir, { recursive: true });
  const existing = await readdir(outputDir);
  if (existing.length) throw new Error(`Refusing to overwrite non-empty output directory: ${outputDir}`);

  if (args['--browser-cache']) process.env.PLAYWRIGHT_BROWSERS_PATH = path.resolve(args['--browser-cache']);
  const require = createRequire(import.meta.url);
  const playwright = require(path.resolve(args['--playwright-module']));
  const browserType = playwright.chromium;
  if (!browserType) throw new Error('The supplied Playwright module has no Chromium browser type.');

  const report = {
    status: 'started',
    mode_requested: args['--mode'],
    base_url: BASE_URL,
    started_at: now(),
    max_run_wait_ms: MAX_RUN_MS,
    response_file: 'response.json',
    raw_video_file: 'capture.webm',
    screenshots: [],
    browser_page_errors: [],
    external_requests_blocked: 0,
    api_response_received: false,
    api_status: null,
    api_mode: null,
    api_run_id: null,
    provider_calls_claimed: null,
    limitation: 'Evidence records this browser session and the actual backend response only. A successful page load or click is not a completed run; the saved HTTP response is authoritative.'
  };

  let browser;
  let video;
  let videoTempPath;
  let exitError;
  try {
    const launchOptions = { headless: true, args: ['--no-sandbox'] };
    if (args['--browser-executable']) launchOptions.executablePath = path.resolve(args['--browser-executable']);
    browser = await browserType.launch(launchOptions);
    const context = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      recordVideo: { dir: outputDir, size: { width: 1440, height: 900 } },
      acceptDownloads: false
    });
    const page = await context.newPage();
    page.on('pageerror', (error) => report.browser_page_errors.push(String(error?.message || error).slice(0, 500)));
    await page.route('**/*', async (route) => {
      let requestUrl;
      try { requestUrl = new URL(route.request().url()); } catch { await route.abort(); return; }
      if (requestUrl.origin !== BASE_URL) {
        report.external_requests_blocked += 1;
        await route.abort('blockedbyclient');
        return;
      }
      await route.continue();
    });

    report.navigation_started_at = now();
    const nav = await page.goto(BASE_URL, { waitUntil: 'networkidle', timeout: 20_000 });
    report.navigation_status = nav?.status() ?? null;
    if (!nav || !nav.ok()) throw new Error(`Local UI navigation failed with HTTP ${report.navigation_status}.`);
    await page.screenshot({ path: path.join(outputDir, 'start.png') });
    report.screenshots.push('start.png');
    await page.locator('#service-status[data-state="ready"]').waitFor({ state: 'visible', timeout: 15_000 });
    const button = args['--mode'] === 'scripted' ? '#run-scripted' : '#run-nova';
    await page.locator(button).waitFor({ state: 'visible', timeout: 5_000 });
    if (await page.locator(button).isDisabled()) {
      throw new Error(`The explicitly selected ${args['--mode']} button is disabled by the served backend.`);
    }

    const runStart = Date.now();
    report.click_started_at = now();
    const responsePromise = page.waitForResponse((response) =>
      response.url() === API_URL && response.request().method() === 'POST',
      { timeout: MAX_RUN_MS });
    await page.locator(button).click({ timeout: 5_000 });
    const response = await responsePromise;
    report.api_response_received = true;
    report.api_status = response.status();
    report.api_response_at = now();
    report.run_elapsed_ms = Date.now() - runStart;
    const responseBytes = await response.body();
    await writeFile(path.join(outputDir, 'response.json'), responseBytes, { flag: 'wx' });
    report.response_bytes = responseBytes.byteLength;
    report.response_content_type = response.headers()['content-type'] || null;
    let payload;
    try { payload = JSON.parse(responseBytes.toString('utf8')); }
    catch { payload = null; }
    if (payload && typeof payload === 'object' && !Array.isArray(payload)) {
      report.api_mode = payload.mode ?? null;
      report.api_run_id = payload.run_id ?? null;
      report.provider_calls_claimed = payload.usage?.model_calls ?? null;
    }

    // Require the actual UI completion state before capturing rendered evidence.
    const expectedLabel = response.ok() ? 'FRESH RUN' : 'RUN FAILED';
    await page.locator('#run-label').filter({ hasText: expectedLabel }).waitFor({ state: 'visible', timeout: 10_000 });
    report.run_label_text = (await page.locator('#run-label').innerText()).trim();
    report.run_status_text = (await page.locator('#run-status').innerText()).trim();
    report.dom_summary = {
      deal_discount: (await page.locator('#deal-discount').innerText()).trim(),
      event_count_text: (await page.locator('#event-count').innerText()).trim(),
      event_cards: await page.locator('#event-list .event-card').count(),
      blocked_event_cards: await page.locator('#event-list .event-card[data-decision="block"]').count(),
      blocked_adapter_contacts: (await page.locator('#blocked-adapter-count').innerText()).trim(),
      audit_chain_text: (await page.locator('#audit-chain').innerText()).trim(),
      error_banner: (await page.locator('#run-error').innerText().catch(() => '')).trim(),
      page_error_count: report.browser_page_errors.length
    };
    report.dom_assertions = {
      mode_matches_request: report.api_mode === args['--mode'],
      completion_label_matches_http_status: report.run_label_text === expectedLabel,
      no_browser_page_errors: report.browser_page_errors.length === 0
    };
    if (!report.dom_assertions.mode_matches_request || !report.dom_assertions.completion_label_matches_http_status) {
      throw new Error('Rendered UI completion label or response mode does not match the actual HTTP response.');
    }
    if (response.ok()) {
      report.dom_assertions.expected_success_scenario = report.api_mode === args['--mode']
        && report.dom_summary.deal_discount === '0%'
        && report.dom_summary.event_cards === 8
        && report.dom_summary.blocked_event_cards === 1
        && report.dom_summary.blocked_adapter_contacts === '0'
        && report.dom_assertions.no_browser_page_errors;
      if (!report.dom_assertions.expected_success_scenario) {
        throw new Error('Successful HTTP response did not render the expected 0% readback, eight events, one block, zero blocked contacts, and error-free UI.');
      }
    }
    if (report.browser_page_errors.length) {
      throw new Error('The browser reported a page error while rendering the actual response.');
    }

    // Keep each real result section visible long enough for the continuous raw
    // recording to be readable; this does not add or animate any result data.
    for (const [name, selector] of [
      ['readback', '#task-list'],
      ['drafts', '#draft-list'],
      ['events', '#event-list'],
      ['audit', '#audit-list']
    ]) {
      const target = page.locator(selector);
      await target.scrollIntoViewIfNeeded();
      await page.waitForTimeout(5_000);
      const filename = `after-run-${name}.png`;
      await page.screenshot({ path: path.join(outputDir, filename) });
      report.screenshots.push(filename);
    }
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
    await page.waitForTimeout(2_000);
    const finalPath = path.join(outputDir, 'end.png');
    await page.screenshot({ path: finalPath, fullPage: true });
    report.screenshots.push('end.png');
    report.status = response.ok() ? 'response_received' : 'http_error_response_received';
    report.response_is_success = response.ok();
  } catch (error) {
    exitError = error;
    report.status = report.api_response_received ? 'capture_failed_after_response' : 'capture_failed_before_response';
    report.error = String(error?.message || error).slice(0, 1000);
    if (browser) {
      try {
        const pages = browser.contexts().flatMap((context) => context.pages());
        if (pages[0] && !pages[0].isClosed()) {
          await pages[0].screenshot({ path: path.join(outputDir, 'failure.png') });
          report.screenshots.push('failure.png');
        }
      } catch { /* Preserve the primary capture error; manifest records the failure. */ }
    }
  } finally {
    report.ended_at = now();
    if (browser) {
      const contexts = browser.contexts();
      try {
        const pages = contexts.flatMap((context) => context.pages());
        video = pages[0]?.video();
      } catch { /* Video may be unavailable if Chromium failed before page creation. */ }
      for (const context of contexts) {
        try { await context.close(); }
        catch (error) { report.context_close_error = String(error?.message || error).slice(0, 500); }
      }
    }
    if (video) {
      try { videoTempPath = await video.path(); } catch { /* Manifest records absent video below. */ }
    }
    if (videoTempPath) {
      const videoPath = path.join(outputDir, 'capture.webm');
      try {
        const sourceStat = await stat(videoTempPath);
        if (sourceStat.size <= 0) throw new Error('Finalized raw WebM is empty.');
        await rename(videoTempPath, videoPath);
        report.video_bytes = (await stat(videoPath)).size;
        if (report.video_bytes <= 0) throw new Error('Saved raw WebM is empty.');
      } catch (error) {
        report.video_error = String(error?.message || error).slice(0, 500);
        report.video_bytes = null;
        report.status = 'capture_failed_video';
        if (!exitError) exitError = error instanceof Error ? error : new Error(report.video_error);
      }
    } else {
      report.video_error = report.video_error || 'Playwright did not produce a finalized video path.';
      report.video_bytes = null;
      report.status = 'capture_failed_video';
      if (!exitError) exitError = new Error(report.video_error);
    }
    if (browser) {
      await browser.close().catch((error) => {
        report.browser_close_error = String(error?.message || error).slice(0, 500);
      });
    }
    await writeFile(path.join(outputDir, 'manifest.json'), `${JSON.stringify(report, null, 2)}\n`, { flag: 'wx' });
  }
  console.log(JSON.stringify(report, null, 2));
  if (exitError) process.exitCode = 1;
}

main().catch((error) => {
  console.error(String(error?.message || error));
  process.exitCode = 2;
});

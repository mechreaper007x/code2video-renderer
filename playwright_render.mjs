#!/usr/bin/env node

import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

let chromium;
try {
  ({ chromium } = await import('playwright'));
} catch (error) {
  console.error('Playwright is not installed. Run: npm install playwright');
  throw error;
}

const [, , inputPath, outputPath] = process.argv;

if (!inputPath || !outputPath) {
  console.error('Usage: node playwright_render.mjs <input.json> <output.mp4>');
  process.exit(1);
}

const ffmpegExe = process.env.FFMPEG_EXE;
if (!ffmpegExe) {
  console.error('FFMPEG_EXE environment variable is required.');
  process.exit(1);
}

const CONTROL_SCRIPT = `
(() => {
  const __state = {
    timeMs: 0,
    nextId: 1,
    timers: new Map(),
    rafQueue: []
  };

  const __RealDate = Date;

  function __MockDate(...args) {
    return args.length ? new __RealDate(...args) : new __RealDate(__state.timeMs);
  }

  __MockDate.UTC = __RealDate.UTC;
  __MockDate.parse = __RealDate.parse;
  __MockDate.now = () => __state.timeMs;
  __MockDate.prototype = __RealDate.prototype;
  window.Date = __MockDate;

  try {
    Object.defineProperty(window.performance, 'now', {
      configurable: true,
      value: () => __state.timeMs
    });
  } catch (_) {}

  function __scheduleTimer(cb, delay, repeat, args) {
    const id = __state.nextId++;
    const wait = Math.max(0, Number(delay) || 0);
    __state.timers.set(id, {
      id,
      cb,
      args,
      repeat,
      delay: repeat ? Math.max(1, wait) : wait,
      nextTime: __state.timeMs + wait
    });
    return id;
  }

  function __runCallback(cb, args) {
    try {
      if (typeof cb === 'function') cb(...args);
      else new Function(String(cb))();
    } catch (err) {
      console.error(err);
    }
  }

  function __runDueTimers(targetMs) {
    while (true) {
      let nextTimer = null;
      for (const timer of __state.timers.values()) {
        if (timer.nextTime <= targetMs && (!nextTimer || timer.nextTime < nextTimer.nextTime)) {
          nextTimer = timer;
        }
      }
      if (!nextTimer) break;
      __state.timeMs = nextTimer.nextTime;
      __runCallback(nextTimer.cb, nextTimer.args);
      if (nextTimer.repeat) nextTimer.nextTime += nextTimer.delay;
      else __state.timers.delete(nextTimer.id);
    }
  }

  function __flushAnimationFrame(nowMs) {
    const queue = __state.rafQueue.slice();
    __state.rafQueue.length = 0;
    queue.forEach(entry => {
      try {
        entry.cb(nowMs);
      } catch (err) {
        console.error(err);
      }
    });
  }

  function __syncAnimations(targetMs) {
    if (!document.getAnimations) return;
    document.getAnimations({ subtree: true }).forEach(anim => {
      try {
        anim.pause();
        anim.currentTime = targetMs;
      } catch (_) {}
    });
  }

  window.requestAnimationFrame = function(cb) {
    const id = __state.nextId++;
    __state.rafQueue.push({ id, cb });
    return id;
  };

  window.cancelAnimationFrame = function(id) {
    __state.rafQueue = __state.rafQueue.filter(entry => entry.id !== id);
  };

  window.setTimeout = function(cb, delay, ...args) {
    return __scheduleTimer(cb, delay, false, args);
  };

  window.setInterval = function(cb, delay, ...args) {
    return __scheduleTimer(cb, delay, true, args);
  };

  window.clearTimeout = function(id) {
    __state.timers.delete(id);
  };

  window.clearInterval = window.clearTimeout;

  window.__code2videoSetTime = function(seconds) {
    const targetMs = Math.max(0, (Number(seconds) || 0) * 1000);
    __runDueTimers(targetMs);
    __state.timeMs = targetMs;
    __syncAnimations(targetMs);
    __flushAnimationFrame(targetMs);
    return targetMs;
  };

  window.__code2videoCaptureMeta = function() {
    const animations = document.getAnimations ? document.getAnimations({ subtree: true }) : [];
    let activeAnimations = 0;
    let hasInfiniteAnimation = false;

    animations.forEach(anim => {
      try {
        const timing = anim.effect && typeof anim.effect.getComputedTiming === 'function'
          ? anim.effect.getComputedTiming()
          : null;
        if (!timing) return;

        if (Number.isFinite(timing.endTime)) {
          if (__state.timeMs < timing.endTime) activeAnimations++;
        } else {
          activeAnimations++;
          hasInfiniteAnimation = true;
        }
      } catch (_) {}
    });

    return {
      activeAnimations,
      hasInfiniteAnimation
    };
  };
})();
`;

function hashFrame(buffer) {
  return createHash('sha1').update(buffer).digest('hex');
}

function runFfmpeg(args) {
  return new Promise((resolve, reject) => {
    const child = spawn(ffmpegExe, args, { stdio: ['ignore', 'ignore', 'pipe'] });
    let stderr = '';

    child.stderr.on('data', chunk => {
      stderr += chunk.toString();
    });

    child.on('error', reject);
    child.on('close', code => {
      if (code === 0) resolve();
      else reject(new Error(stderr.trim() || `ffmpeg exited with code ${code}`));
    });
  });
}

async function captureFrames(page, settings) {
  const {
    frameRate,
    maxDuration,
    minDuration,
    settleWindow,
    tempDir
  } = settings;

  const frameTimes = [];
  let lastSignature = '';
  let lastChangeTime = 0;
  let capped = false;
  const frameTotal = Math.ceil(maxDuration * frameRate) + 2;

  for (let index = 0; index < frameTotal; index++) {
    const t = Number((index / frameRate).toFixed(4));
    await page.evaluate(seconds => {
      if (typeof window.__code2videoSetTime === 'function') {
        window.__code2videoSetTime(seconds);
      }
    }, t);

    const buffer = await page.screenshot({ type: 'png' });
    const signature = hashFrame(buffer);
    if (!lastSignature || signature !== lastSignature) {
      lastSignature = signature;
      lastChangeTime = t;
    }

    const framePath = path.join(tempDir, `frame${String(index).padStart(5, '0')}.png`);
    await fs.writeFile(framePath, buffer);
    frameTimes.push(t);

    const meta = await page.evaluate(() => (
      typeof window.__code2videoCaptureMeta === 'function'
        ? window.__code2videoCaptureMeta()
        : { activeAnimations: 0, hasInfiniteAnimation: false }
    ));

    const idleFor = t - lastChangeTime;
    const settled = t >= minDuration && idleFor >= settleWindow && meta.activeAnimations === 0;
    if (settled) {
      return { frameTimes, capped: false };
    }
  }

  capped = true;
  return { frameTimes, capped };
}

async function main() {
  const payload = JSON.parse(await fs.readFile(inputPath, 'utf8'));
  const {
    code,
    width,
    height,
    bitrate = '5M',
    frameRate = 60,
    maxDuration = 12,
    minDuration = 0.35,
    settleWindow = 0.45
  } = payload;

  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), 'code2video-playwright-'));
  let browser;

  try {
    browser = await chromium.launch({
      headless: true,
      args: ['--disable-dev-shm-usage', '--hide-scrollbars', '--mute-audio']
    });

    const context = await browser.newContext({
      viewport: { width, height },
      screen: { width, height },
      deviceScaleFactor: 1,
      colorScheme: 'dark'
    });

    const page = await context.newPage();
    page.on('pageerror', err => console.error(`[pageerror] ${err.message}`));
    await page.addInitScript({ content: CONTROL_SCRIPT });
    await page.setContent(code, { waitUntil: 'load' });
    await page.evaluate(async () => {
      try {
        if (document.fonts && document.fonts.ready) {
          await document.fonts.ready;
        }
      } catch (_) {}
    });

    const capture = await captureFrames(page, {
      frameRate,
      maxDuration,
      minDuration,
      settleWindow,
      tempDir
    });

    const args = [
      '-y',
      '-framerate', String(frameRate),
      '-i', path.join(tempDir, 'frame%05d.png'),
      '-c:v', 'libx264',
      '-pix_fmt', 'yuv420p',
      '-preset', 'fast',
      '-b:v', bitrate,
      '-maxrate', bitrate,
      '-bufsize', bitrate,
      outputPath
    ];

    await runFfmpeg(args);
    console.error(JSON.stringify({
      duration: capture.frameTimes.length ? capture.frameTimes[capture.frameTimes.length - 1] : 0,
      frameCount: capture.frameTimes.length,
      capped: capture.capped
    }));
  } finally {
    if (browser) await browser.close();
    await fs.rm(tempDir, { recursive: true, force: true });
  }
}

main().catch(error => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});

// Run against a production build: pnpm build && pnpm start --port 3148
// API fixtures exercise UI behavior without spending provider credits. Backend contracts use real Postgres tests.
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';

const baseURL = process.env.BROWSER_BASE_URL ?? 'http://127.0.0.1:3148';
const output = process.env.BROWSER_ARTIFACTS ?? '/tmp/duanju-cost-controls';
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
const report = [];
const stages = ['summarize', 'plan', 'script', 'storyboard', 'reformat'];
const catalog = Object.fromEntries(stages.map(stage => [stage, Object.fromEntries(['standard', 'draft'].map(tier => [tier, {
  model: tier === 'draft' ? 'economy-model' : 'standard-model', provider: 'test', price_note: '',
  effective_model: tier === 'draft' ? 'economy-model' : 'standard-model', effective_provider: 'test',
}]))]));
const project = { id: 'p1', title: '雾港来信', category: 'drama', content_mode: 'standard_sfw', aspect_ratio: '9:16', status: 'scripted', has_source_text: true, series_bible_json: { title: '雾港来信', logline: '在反复试片之前，先确定值得拍的故事。' }, created_at: '2026-10-24' };
const episode = { episode_number: 1, title: '一封迟到的信', synopsis: '雾港码头，林晚收到一封旧信。', source_segments: [1], script_text: '场1 日 外 码头\n林晚：这封信是给我的？', status: 'scripted' };
const shot = { id: 's1', episode_number: 1, shot_order: 1, template_id: null, render_tier: 'L1_draft', duration: 5, resolution: '480p', shot_prompt: '淡妆，浅棕色眼影，清晨自然光，人物从码头转身。', dialogue_transcript: '这封信是给我的？', first_frame_url: null, last_frame_url: null, refs: {}, status: 'rendered_L1_draft', preview_url: null, shot_kind: 'dialogue', continuous_from_prev: false, keyframe_url: null, keyframe_status: 'none', selected_job_id: null, takes: [{ job_id: 'old', render_tier: 'L1_draft', model_key: 'seedance_2_5_draft', final: false, url: '#old', created_at: '2026-10-24' }] };
const model = key => ({ model_key: key, display_name: key, task_type: 'video.generate', provider: 'test', is_nsfw: false, is_active: true, credits_per_unit: '1', max_duration: 10, max_refs: 10, fallback_model_key: null, resolution_multipliers: { '480p': 0.4, '720p': 1 }, health_score: '1', usd_per_unit: '0.1', usd_resolution_factors: {} });
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function geometry(page, label, selector = 'main') {
  const box = await page.locator(selector).boundingBox();
  assert(box && box.width > 0 && box.height > 0, `${label}: root has dimensions`);
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `${label}: no horizontal overflow`);
  const controls = await page.locator('main button:visible, main select:visible, main textarea:visible').evaluateAll(elements => ({
    count: elements.length,
    valid: elements.every(element => { const box = element.getBoundingClientRect(); return box.width > 0 && box.height > 0; }),
  }));
  assert(controls.count > 0 && controls.valid, `${label}: interactive controls have dimensions`);
}

try {
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    const context = await browser.newContext({ viewport });
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    const consoleErrors = [], pageErrors = [], writes = [], renders = [];
    let saved = { lookdev_image: 'draft' }, failSave = false, failRead = false, emptyCatalog = false, longList = false;
    const customCatalog = structuredClone(catalog);
    page.on('console', msg => { if (msg.type() === 'error') consoleErrors.push(msg.text()); });
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.route('**/api/v1/**', async route => {
      const request = route.request(), path = new URL(request.url()).pathname.replace('/api/v1', '');
      let data = {};
      if (path === '/auth/login') data = { ok: true, auth_enabled: false, expires_at: 0 };
      else if (path === '/projects') data = [project];
      else if (path === '/me') data = { credits: '100', daily_limit_credits: '200', age_verified_18plus: false };
      else if (path === '/stage-tiers') { await pause(300); data = emptyCatalog ? {} : customCatalog; }
      else if (path.endsWith('/quality-tiers')) {
        if (request.method() === 'PUT') {
          writes.push(request.postDataJSON()); await pause(250);
          if (failSave) return route.fulfill({ status: 503, json: { detail: '临时存储故障' } });
          saved = request.postDataJSON().tiers;
        } else if (failRead) return route.fulfill({ status: 503, json: { detail: '暂时无法读取设置' } });
        data = { tiers: saved };
      } else if (path.endsWith('/episodes')) data = longList ? Array.from({ length: 1000 }, (_, index) => ({ ...episode, episode_number: index + 1, title: '长标题'.repeat(70) })) : [episode];
      else if (path.endsWith('/shots')) data = [shot, { ...shot, id: 's2', shot_order: 2, takes: [], status: 'draft' }];
      else if (path.endsWith('/render')) {
        renders.push({ path, ...request.postDataJSON() }); await pause(300);
        data = { id: 'j1', shot_id: 's1', project_id: 'p1', task_type: 'video.generate', status: 'success', model_key: 'seedance_2_5_draft', credit_estimate: '2', actual_credits: null, error_message: null, created_at: '2026-10-24', usd: '0.1', stage: 'video_draft', cached_from: request.postDataJSON().force ? null : 'old' };
      } else if (path === '/models') data = ['seedance_2_5_draft', 'seedance_2_5_spicy'].map(model);
      else if (path === '/admin/stages' || path.endsWith('/keyframes')) data = [];
      else if (path.endsWith('/estimate')) data = { episode_number: 1, usd: '1', total_usd: '1', credits: '10', budget_usd: null, keyframes: { count: 0, model_key: 'seedream', usd: '0' }, shots: [] };
      else if (path.endsWith('/llm-usage')) data = { calls: 0, total_usd: 0, by_stage: {} };
      else if (path.endsWith('/source')) data = { text: '雾港来信' };
      return route.fulfill({ json: data });
    });
    await page.goto(baseURL);
    await page.getByRole('navigation').getByRole('button', { name: /剧本/ }).click();
    await page.getByRole('status', { name: '正在加载岗位设置' }).waitFor();
    const loadingHeight = (await page.locator('.cost-controls').boundingBox()).height;
    await page.getByRole('combobox', { name: '全剧规划' }).waitFor();
    assert.equal(await page.getByRole('combobox', { name: '全剧规划' }).inputValue(), 'standard');
    assert.equal((await page.locator('.cost-controls').boundingBox()).height, loadingHeight, 'loading reserves the settings panel geometry');
    console.log(`${viewport.width}: script loaded`);
    await geometry(page, 'script');
    await page.screenshot({ path: `${output}/${viewport.width}-script.png`, fullPage: true });
    await page.getByRole('button', { name: '全部设为经济档' }).click();
    assert(await page.getByRole('button', { name: '重新规划全剧' }).isDisabled());
    const save = page.getByRole('button', { name: '保存岗位设置', exact: true });
    await save.evaluate(el => { el.click(); el.click(); });
    await page.getByText('已保存。后续生成使用这些档位，已有剧本和缓存保持可用。').waitFor();
    assert.equal(writes.length, 1, 'double submit sends one settings write');
    assert.equal(saved.lookdev_image, 'draft', 'other departments remain configured');
    for (const stage of stages) assert.equal(saved[stage], 'draft');
    await page.getByRole('navigation').getByRole('button', { name: /分镜/ }).click();
    const firstShot = page.getByRole('listitem', { name: '第 1 镜', exact: true });
    await firstShot.waitFor();
    await geometry(page, 'storyboard');
    await firstShot.getByRole('button', { name: /^草稿/ }).click();
    await page.getByText('第 1 镜复用了已有视频', { exact: false }).waitFor();
    assert.equal(renders.at(-1).force, false);
    const regenerate = firstShot.getByRole('button', { name: /^重新生成草稿/ });
    await regenerate.evaluate(el => { el.click(); el.click(); });
    await page.getByText('第 1 镜已提交重新生成', { exact: false }).waitFor();
    assert.equal(renders.filter(row => row.force).length, 1);
    assert.equal(renders.at(-1).path, '/shots/s1/render', 'only the selected shot is regenerated');
    await page.screenshot({ path: `${output}/${viewport.width}-storyboard.png`, fullPage: true });
    await page.getByRole('navigation').getByRole('button', { name: /剧本/ }).click();
    await page.getByRole('combobox', { name: '全剧规划' }).waitFor();
    assert.equal(await page.getByRole('combobox', { name: '全剧规划' }).inputValue(), 'draft', 'saved settings survive remount');
    console.log(`${viewport.width}: regeneration and persistence verified`);
    await page.getByRole('combobox', { name: '全剧规划' }).focus();
    assert(await page.getByRole('combobox', { name: '全剧规划' }).evaluate(el => parseFloat(getComputedStyle(el).outlineWidth) > 0));
    await page.keyboard.press('Tab');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    assert.equal(await save.evaluate(el => getComputedStyle(el).transitionDuration), '0s');
    assert.equal(consoleErrors.length, 0, 'successful flows: zero console errors');
    assert.equal(pageErrors.length, 0, 'zero uncaught page errors');
    const successErrors = consoleErrors.length;

    // Intentional HTTP failures may emit browser resource-error logs; test them separately and report them.
    console.log(`${viewport.width}: keyboard and reduced motion verified`);
    failSave = true;
    await page.getByRole('combobox', { name: '全剧规划' }).selectOption('standard');
    await save.click();
    await page.getByRole('alert').filter({ hasText: '未保存' }).waitFor();
    assert.equal(saved.plan, 'draft', 'failed save preserves persisted settings');
    failSave = false;
    await page.getByRole('button', { name: '重试保存' }).click();
    await page.getByText('已保存。后续生成使用这些档位，已有剧本和缓存保持可用。').waitFor();
    assert.equal(saved.plan, 'standard');
    await page.getByRole('navigation').getByRole('button', { name: /分镜/ }).click();
    failRead = true;
    await page.getByRole('navigation').getByRole('button', { name: /剧本/ }).click();
    await page.getByText('岗位设置读取失败：', { exact: false }).waitFor();
    failRead = false;
    await page.getByRole('button', { name: '重新读取设置' }).click();
    await page.getByRole('combobox', { name: '全剧规划' }).waitFor();
    await page.getByRole('navigation').getByRole('button', { name: /分镜/ }).click();
    console.log(`${viewport.width}: error retry verified`);
    emptyCatalog = true;
    await page.getByRole('navigation').getByRole('button', { name: /剧本/ }).click();
    await page.getByText('尚无可用档位', { exact: false }).waitFor();
    emptyCatalog = false; longList = true;
    customCatalog.plan.standard.effective_model = 'deployment-override-'.repeat(12);
    await page.getByRole('button', { name: '重新读取设置' }).click();
    await page.getByText('部署设置已覆盖该档位', { exact: false }).waitFor();
    await page.getByRole('navigation').getByRole('button', { name: /分镜/ }).click();
    await page.getByRole('navigation').getByRole('button', { name: /剧本/ }).click();
    await page.getByRole('combobox', { name: '全剧规划' }).waitFor();
    assert.equal(await page.getByRole('list', { name: '分集', exact: true }).getByRole('button').count(), 1000);
    await geometry(page, '1000 episodes and long model names');
    assert.equal(pageErrors.length, 0);
    assert(consoleErrors.every(message => message.includes('503')), 'only intentional HTTP 503 failures may log resource errors');
    report.push({ viewport, loadingGeometryStable: true, consoleErrors: successErrors, pageErrors: pageErrors.length, rootAndControlsHaveDimensions: true, horizontalOverflow: false, savedEconomyTiers: true, isolatedForceRegeneration: true, doubleSubmitPrevented: true, errorRetryAndEmptyStates: true, thousandEpisodes: true, reducedMotion: true, expectedResourceErrorsDuringFaultInjection: consoleErrors.length });
    await context.close();
  }
  await writeFile(`${output}/runtime.json`, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
} finally { await browser.close(); }

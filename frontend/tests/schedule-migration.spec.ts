import { expect, test } from '@playwright/test'

/**
 * 调度时间口径迁移的端到端验收：
 * - 定时调度页生成兼容报告，展示存量 vs 迁移窗口、时区三要素、拦截、历史批次、版本去重；
 * - 爬虫引擎页读取 active 报告生成待下发清单，下发时间已换算目标时区，并能核对差异；
 * - 旧版/新版报告给爬虫引擎带来不同下游结果。
 */

test('定时调度：生成兼容报告，时区三要素齐全且缺依据计划被拦截', async ({ page }) => {
  await page.goto('/schedule')
  await page.locator('.timeline-bar').first().waitFor({ timeout: 10000 })

  // 选目标时区 UTC，覆盖纽约秋退日的窗口起点
  await page.getByTestId('target-tz').selectOption('UTC')
  await page.getByTestId('window-start').fill('2026-10-29T00:00')
  await page.getByTestId('generate-report').click()

  await page.getByTestId('report-summary').waitFor({ timeout: 10000 })
  const summary = page.getByTestId('report-summary')

  // 报告三要素：原时区 / 目标时区 / Cron 同时展示
  await expect(summary.getByTestId('report-row-SCHE-0001')).toContainText('Asia/Shanghai')
  await expect(summary.getByTestId('report-row-SCHE-0001')).toContainText('UTC')
  await expect(summary.getByTestId('report-row-SCHE-0001')).toContainText('0 9 * * *')

  // 缺时区依据的 SCHE-0004 被拦截，不进入自动执行
  const blockedRow = summary.getByTestId('report-row-SCHE-0004')
  await expect(blockedRow).toHaveAttribute('data-eligible', 'false')
  await expect(summary.getByTestId('blocked-SCHE-0004')).toContainText('MISSING_SOURCE_TIMEZONE')

  // 历史批次仍按原时区显示
  const history = summary.getByTestId('history-HIST-20261015-0230')
  await expect(history).toContainText('America/New_York')
  await expect(history).toContainText('2026-10-15T02:30:00-04:00')
})

test('定时调度：连续两次提交只保留一个 active 版本，提示去重', async ({ page }) => {
  await page.goto('/schedule')
  // 固定窗口起始，保证两次提交内容一致，命中内容级去重
  await page.getByTestId('window-start').fill('2026-01-05T09:00')
  await page.getByTestId('generate-report').click()
  await page.getByTestId('report-summary').waitFor({ timeout: 10000 })
  const firstId = await page.getByTestId('version-SCR-0001').count()
  expect(firstId).toBeGreaterThan(0)

  // 再点一次相同提交
  await page.getByTestId('generate-report').click()
  await expect(page.getByTestId('report-message')).toContainText('去重')

  // 任意时刻只有一个 active
  const activeRows = page.locator('[data-testid^="version-SCR-"][data-status="active"]')
  await expect(activeRows).toHaveCount(1)
})

test('定时调度：夏令时秋退窗口只保留一个批次并标记', async ({ page }) => {
  await page.goto('/schedule')
  await page.getByTestId('target-tz').selectOption('UTC')
  await page.getByTestId('window-start').fill('2026-10-29T00:00')
  await page.getByTestId('generate-report').click()
  await page.getByTestId('report-summary').waitFor({ timeout: 10000 })

  const nyRow = page.getByTestId('report-row-SCHE-0002')
  await expect(nyRow).toContainText('秋退合并')
  // 秋退日同墙钟只出现一次
  const walls = await nyRow.locator('.window-list .wall').allInnerTexts()
  expect(new Set(walls).size).toBe(walls.length)
})

test('定时调度：春跳缺口被统计并跳过', async ({ page }) => {
  await page.goto('/schedule')
  await page.getByTestId('target-tz').selectOption('UTC')
  await page.getByTestId('window-start').fill('2026-03-06T00:00')
  await page.getByTestId('generate-report').click()
  await page.getByTestId('report-summary').waitFor({ timeout: 10000 })

  const gapRow = page.getByTestId('report-row-SCHE-0005')
  await expect(gapRow).toContainText('春跳跳过')
  const walls = await gapRow.locator('.window-list .wall').allInnerTexts()
  expect(walls.every((w) => !w.includes('2026-03-08T02:30'))).toBe(true)
})

test('爬虫引擎：待下发清单读 active 报告、时间已换算目标时区、差异为零', async ({ page }) => {
  // 先生成一份报告
  await page.goto('/schedule')
  await page.getByTestId('target-tz').selectOption('Asia/Shanghai')
  await page.getByTestId('generate-report').click()
  await page.getByTestId('report-summary').waitFor({ timeout: 10000 })

  // 再到爬虫引擎页看待下发清单
  await page.goto('/crawler')
  await page.getByTestId('dispatch-panel').waitFor({ timeout: 10000 })
  const dispatchRows = page.locator('[data-testid^="dispatch-row-"]')
  await expect(dispatchRows.first()).toBeVisible()

  // 缺时区依据的计划不下发
  expect(await page.getByTestId('dispatch-row-SCHE-0004-0').count()).toBe(0)

  // 目标时区为上海，下发时间带 +08:00 偏移
  const firstWall = await dispatchRows.first().locator('.wall').innerText()
  expect(firstWall).toContain('+08:00')

  // 差异核对：无差异
  await page.getByTestId('load-reconcile').click()
  await expect(page.getByTestId('reconcile-clean')).toBeVisible()
})

test('新旧报告版本给爬虫引擎带来不同下游结果', async ({ page }) => {
  await page.goto('/schedule')
  // 版本 A：UTC
  await page.getByTestId('target-tz').selectOption('UTC')
  await page.getByTestId('generate-report').click()
  await page.getByTestId('report-summary').waitFor({ timeout: 10000 })
  await page.goto('/crawler')
  await page.getByTestId('dispatch-panel').waitFor({ timeout: 10000 })
  const utcTime = await page.locator('[data-testid^="dispatch-row-"]').first().locator('.wall').innerText()

  // 版本 B：上海
  await page.goto('/schedule')
  await page.getByTestId('target-tz').selectOption('Asia/Shanghai')
  await page.getByTestId('generate-report').click()
  await page.getByTestId('report-summary').waitFor({ timeout: 10000 })
  await page.goto('/crawler')
  await page.getByTestId('dispatch-panel').waitFor({ timeout: 10000 })
  const shTime = await page.locator('[data-testid^="dispatch-row-"]').first().locator('.wall').innerText()

  expect(utcTime).not.toBe(shTime)
  expect(shTime).toContain('+08:00')
  expect(utcTime).toContain('+00:00')
})

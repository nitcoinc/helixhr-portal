import { test, expect, type Page } from '@playwright/test'

// Plan 2026-09-30-001 U5. The employee sets, replaces and removes their own
// photo from the Profile identity band; every avatar falls back to the
// initials monogram when there is no photo or it fails to load.
//
// Serial: the scenarios share one employee's photo, and the file ends with
// it removed so no other spec sees a changed shell.
test.describe.configure({ mode: 'serial' })

// A 24x24 JPEG, well inside the 5 MB policy. The server re-encodes it.
const JPEG = Buffer.from(
  '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAoHBwgHBgoICAgLCgoLDhgQDg0NDh0VFhEYIx8lJCIfIiEmKzcvJik0KSEiMEExNDk7Pj4+JS5ESUM8SDc9Pjv/2wBDAQoLCw4NDhwQEBw7KCIoOzs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozv/wAARCAAYABgDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwCjRRRXz59kFFFFABRRRQAUUUUAf//Z',
  'base64',
)

async function openProfile(page: Page) {
  await page.goto('/helixhr/profile')
  await expect(page.getByTestId('profile-identity')).toBeVisible()
  await page.waitForLoadState('networkidle')
}

const tagOf = (page: Page, testId: string) =>
  page.getByTestId(testId).evaluate((element) => element.tagName)

test.describe('employee', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenarios')
  })

  test('a wrong type is refused inline and nothing is uploaded', async ({ page }) => {
    await openProfile(page)
    let uploads = 0
    page.on('request', (request) => {
      if (request.url().includes('helixhr.api.upload_my_photo')) uploads += 1
    })
    await page.getByTestId('profile-photo-input').setInputFiles({
      name: 'notes.pdf',
      mimeType: 'application/pdf',
      buffer: Buffer.from('%PDF-1.4 not a photo'),
    })
    await expect(page.getByTestId('profile-photo-error')).toHaveText('Your photo must be a PNG or JPEG image.')
    expect(uploads).toBe(0)
  })

  test('an uploaded JPEG shows in the band and the shell after a reload, and Remove brings initials back', async ({
    page,
  }) => {
    await openProfile(page)
    const identity = page.getByTestId('profile-identity')

    // The button is the labelled control; the hidden input is its engine.
    await expect(identity.getByRole('button', { name: /(Add|Change) photo/ })).toBeVisible()
    const uploaded = page.waitForResponse((response) => response.url().includes('helixhr.api.upload_my_photo'))
    await page.getByTestId('profile-photo-input').setInputFiles({
      name: 'me.jpg',
      mimeType: 'image/jpeg',
      buffer: JPEG,
    })
    expect((await uploaded).ok()).toBe(true)
    // In place, before any reload: the band and the shell both follow.
    await expect(page.getByTestId('profile-avatar')).toHaveJSProperty('tagName', 'IMG')
    await expect(page.getByTestId('shell-avatar')).toHaveJSProperty('tagName', 'IMG')

    await page.reload()
    await expect(page.getByTestId('profile-identity')).toBeVisible()
    const bandImg = page.getByTestId('profile-avatar')
    await expect(bandImg).toHaveJSProperty('tagName', 'IMG')
    await expect(bandImg).toHaveJSProperty('complete', true)
    expect(await bandImg.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0)
    await expect(page.getByTestId('shell-avatar')).toHaveJSProperty('tagName', 'IMG')

    // Remove is confirmed, never a single tap.
    await identity.getByRole('button', { name: 'Remove photo' }).click()
    const dialog = page.getByRole('dialog', { name: 'Remove your photo?' })
    await expect(dialog).toBeVisible()
    await dialog.getByRole('button', { name: 'Remove photo' }).click()
    await expect(dialog).toBeHidden()
    await expect(page.getByTestId('profile-avatar')).toHaveJSProperty('tagName', 'SPAN')
    await expect(page.getByTestId('shell-avatar')).toHaveJSProperty('tagName', 'SPAN')

    await page.reload()
    await expect(page.getByTestId('profile-identity')).toBeVisible()
    expect(await tagOf(page, 'profile-avatar')).toBe('SPAN')
    await expect(identity.getByRole('button', { name: 'Add photo' })).toBeVisible()
    await expect(identity.getByRole('button', { name: 'Remove photo' })).toHaveCount(0)
  })

  test('a photo URL that fails to load falls back to the initials', async ({ page }) => {
    await page.route('**/api/method/helixhr.api.get_my_profile*', async (route) => {
      const response = await route.fetch()
      const body = await response.json()
      body.message.photo_url = '/api/method/helixhr.api.get_employee_photo?employee=nobody&v=broken'
      await route.fulfill({ response, json: body })
    })
    await openProfile(page)
    const avatar = page.getByTestId('profile-avatar')
    await expect(avatar).toHaveJSProperty('tagName', 'SPAN')
    await expect(avatar).not.toHaveText('')
  })

  test('in the Directory a colleague with a photo shows it, one without shows initials', async ({ page }) => {
    await page.goto('/helixhr/directory')
    await page.waitForLoadState('networkidle')
    const search = page.getByLabel('Search')

    // Seeded by `ensure_directory_fixtures`: the colleague has a photo.
    await search.fill('Directory Colleague')
    // The name is printed beside the avatar, so the photo is decorative:
    // alt="" and aria-hidden, never a second announcement of the name.
    const withPhoto = page.getByTestId('avatar-img').first()
    await expect(withPhoto).toBeVisible()
    await expect(withPhoto).toHaveAttribute('alt', '')
    await expect(withPhoto).toHaveAttribute('aria-hidden', 'true')
    await expect(withPhoto).toHaveJSProperty('complete', true)
    expect(await withPhoto.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0)

    // The manager has none.
    await search.fill('Manager')
    await expect(page.getByText('Manager', { exact: true }).first()).toBeVisible()
    await expect(page.getByTestId('avatar-initials').first()).toBeVisible()
    await expect(page.getByTestId('avatar-img')).toHaveCount(0)
  })

  test('the identity band does not overflow at 375px', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    await openProfile(page)
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    expect(overflow).toBe(0)
    const band = await page.getByTestId('profile-identity').boundingBox()
    expect(band && band.x + band.width).toBeLessThanOrEqual(375)
  })
})

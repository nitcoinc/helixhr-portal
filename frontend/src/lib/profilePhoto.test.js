import { describe, it, expect } from 'vitest'
import { PHOTO_MAX_MB, photoRefusal } from './profilePhoto'

const file = (name, type, size = 1024) => ({ name, type, size })

describe('photoRefusal', () => {
  it('lets a PNG or JPEG under the cap through', () => {
    expect(photoRefusal(file('me.png', 'image/png'))).toBe('')
    expect(photoRefusal(file('me.JPG', 'image/jpeg'))).toBe('')
    expect(photoRefusal(file('me.jpeg', ''))).toBe('')
  })

  it('refuses any other type, by extension or by the browser’s type', () => {
    expect(photoRefusal(file('me.gif', 'image/gif'))).toBe('Your photo must be a PNG or JPEG image.')
    expect(photoRefusal(file('me.png', 'application/pdf'))).toBe('Your photo must be a PNG or JPEG image.')
    expect(photoRefusal(file('me', ''))).toBe('Your photo must be a PNG or JPEG image.')
  })

  it('refuses an empty or oversized file', () => {
    expect(photoRefusal(file('me.png', 'image/png', 0))).toBe('That file is empty. Pick another one.')
    expect(photoRefusal(file('me.png', 'image/png', PHOTO_MAX_MB * 1024 * 1024))).toBe('')
    expect(photoRefusal(file('me.png', 'image/png', PHOTO_MAX_MB * 1024 * 1024 + 1))).toBe(
      'That file is bigger than 5 MB. Send a smaller one.',
    )
  })

  it('asks for a file when there is none', () => {
    expect(photoRefusal(null)).toBe('Pick a photo first.')
  })
})

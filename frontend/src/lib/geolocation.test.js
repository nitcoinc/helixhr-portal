import { describe, it, expect, vi, afterEach } from 'vitest'
import {
  classifyGeolocationError,
  getPosition,
  GEO_DENIED,
  GEO_INSECURE,
  GEO_TIMEOUT,
  GEO_UNAVAILABLE,
  GEO_UNSUPPORTED,
} from './geolocation'

// P3-U4 scenario 7. The classification is the whole contract between this
// module and the check-in sheet: the sheet's copy differs by kind, and
// telling somebody to allow location in their browser when the real problem
// is a plain-HTTP deployment or a cold GPS is the failure this guards.

const POSITION = { coords: { latitude: 12.9716, longitude: 77.5946, accuracy: 18 } }

/** A `navigator.geolocation` whose `getCurrentPosition` answers from a
 * queue: one entry per attempt, so a test can fail the high-accuracy attempt
 * and succeed on the low-accuracy retry. */
function stubGeolocation(answers) {
  const calls = []
  vi.stubGlobal('navigator', {
    geolocation: {
      getCurrentPosition(onSuccess, onError, options) {
        calls.push(options)
        const answer = answers[calls.length - 1]
        if (answer && answer.code) onError(answer)
        else onSuccess(answer || POSITION)
      },
    },
  })
  vi.stubGlobal('window', { isSecureContext: true })
  return calls
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('classifyGeolocationError', () => {
  it('maps the spec codes to the kinds the sheet renders', () => {
    expect(classifyGeolocationError({ code: 1 })).toBe(GEO_DENIED)
    expect(classifyGeolocationError({ code: 2 })).toBe(GEO_UNAVAILABLE)
    expect(classifyGeolocationError({ code: 3 })).toBe(GEO_TIMEOUT)
  })

  it('reads an unrecognised or missing code as unavailable, never as denied', () => {
    // "Your browser is blocking this" is advice about a setting. Saying it
    // about an error we did not recognise sends somebody into a settings
    // screen that has nothing wrong with it.
    expect(classifyGeolocationError({ code: 99 })).toBe(GEO_UNAVAILABLE)
    expect(classifyGeolocationError({})).toBe(GEO_UNAVAILABLE)
    expect(classifyGeolocationError(undefined)).toBe(GEO_UNAVAILABLE)
  })
})

describe('getPosition', () => {
  it('returns the coordinates and the accuracy on the first attempt', async () => {
    const calls = stubGeolocation([POSITION])

    await expect(getPosition()).resolves.toEqual({
      latitude: 12.9716,
      longitude: 77.5946,
      accuracy: 18,
    })
    expect(calls).toHaveLength(1)
    expect(calls[0]).toEqual({ enableHighAccuracy: true, timeout: 10000, maximumAge: 0 })
  })

  it('retries once at low accuracy after a timeout', async () => {
    const calls = stubGeolocation([{ code: 3 }, POSITION])

    await expect(getPosition()).resolves.toMatchObject({ latitude: 12.9716 })
    expect(calls).toHaveLength(2)
    expect(calls[1]).toEqual({ enableHighAccuracy: false, timeout: 5000, maximumAge: 60000 })
  })

  it('retries once when the position is unavailable', async () => {
    const calls = stubGeolocation([{ code: 2 }, POSITION])

    await expect(getPosition()).resolves.toMatchObject({ accuracy: 18 })
    expect(calls).toHaveLength(2)
  })

  it('reports the retry failure rather than the first one', async () => {
    const calls = stubGeolocation([{ code: 3 }, { code: 2 }])

    await expect(getPosition()).rejects.toMatchObject({ kind: GEO_UNAVAILABLE })
    expect(calls).toHaveLength(2)
  })

  it('never retries a denial -- the answer will not change', async () => {
    const calls = stubGeolocation([{ code: 1 }])

    await expect(getPosition()).rejects.toMatchObject({ kind: GEO_DENIED })
    expect(calls).toHaveLength(1)
  })

  it('short-circuits an insecure context without touching the API', async () => {
    const calls = stubGeolocation([POSITION])
    vi.stubGlobal('window', { isSecureContext: false })

    await expect(getPosition()).rejects.toMatchObject({ kind: GEO_INSECURE })
    expect(calls).toHaveLength(0)
  })

  it('short-circuits a browser with no geolocation at all', async () => {
    vi.stubGlobal('window', { isSecureContext: true })
    vi.stubGlobal('navigator', {})

    await expect(getPosition()).rejects.toMatchObject({ kind: GEO_UNSUPPORTED })
  })
})

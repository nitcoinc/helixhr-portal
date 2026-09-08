// P3-U4 step 3 / P3-R6, P3-KTD4. The browser's location, asked for once,
// from a tap.
//
// Three rules, all of them deliberate:
//
//   * **Never on load.** `getPosition()` is called from the check-in sheet
//     and from nowhere else. A permission prompt that appears because
//     somebody opened the Attendance page is a prompt they will deny, and a
//     denial is remembered by the browser for the whole origin.
//   * **Two attempts, not a loop.** A high-accuracy fix is worth ten seconds
//     of waiting on a phone that has just woken its GPS; when that times out
//     or the position is simply unavailable, one low-accuracy retry with a
//     minute of cache is almost always answered by the wifi database. A
//     denial is not retried -- the answer will not change.
//   * **`maximumAge: 0` on the first attempt.** The punch is a claim about
//     where somebody is now. A cached fix from this morning is not that.
//
// The five kinds below are the whole vocabulary the sheet renders, and they
// are what `geolocation.test.js` holds this file to: the sheet's copy differs
// by kind, and "your browser is blocking this" is a very different sentence
// from "we couldn't get a fix".

export const GEO_DENIED = 'denied'
export const GEO_UNAVAILABLE = 'unavailable'
export const GEO_TIMEOUT = 'timeout'
export const GEO_UNSUPPORTED = 'unsupported'
export const GEO_INSECURE = 'insecure'

/** One failure kind, carried on the error rather than parsed out of a
 * message: `GeolocationPositionError.message` is browser-specific prose. */
export class GeolocationError extends Error {
  constructor(kind) {
    super(`Location unavailable: ${kind}`)
    this.name = 'GeolocationError'
    this.kind = kind
  }
}

const HIGH_ACCURACY = { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 }
const LOW_ACCURACY = { enableHighAccuracy: false, timeout: 5000, maximumAge: 60000 }

/** `GeolocationPositionError.code` -> one of our five kinds. The numeric
 * codes are the spec's (1 PERMISSION_DENIED, 2 POSITION_UNAVAILABLE,
 * 3 TIMEOUT) and are read directly: the constants live on the *instance* in
 * every engine, and an error object we did not get from the API at all (a
 * WebView passing something else through) must still classify. */
export function classifyGeolocationError(error) {
  switch (error?.code) {
    case 1:
      return GEO_DENIED
    case 3:
      return GEO_TIMEOUT
    default:
      // Code 2, and anything unrecognised: the honest reading of both is
      // "the device could not work out where it is".
      return GEO_UNAVAILABLE
  }
}

function attempt(options) {
  return new Promise((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(
      (position) =>
        resolve({
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
          // Shown in the sheet, never stored: Employee Checkin has no field
          // for it (P3-KTD4).
          accuracy: position.coords.accuracy,
        }),
      (error) => reject(new GeolocationError(classifyGeolocationError(error))),
      options,
    )
  })
}

/**
 * One position fix, from a tap. Resolves `{ latitude, longitude, accuracy }`
 * or rejects with a `GeolocationError` carrying one of the five kinds.
 */
export async function getPosition() {
  // Before the API, not after: over plain HTTP Chrome and Safari answer
  // `getCurrentPosition` with PERMISSION_DENIED, which would send the sheet
  // into "allow location in your browser" copy for a problem no browser
  // setting can fix.
  if (typeof window !== 'undefined' && window.isSecureContext === false) {
    throw new GeolocationError(GEO_INSECURE)
  }
  if (typeof navigator === 'undefined' || !navigator.geolocation) {
    throw new GeolocationError(GEO_UNSUPPORTED)
  }
  try {
    return await attempt(HIGH_ACCURACY)
  } catch (error) {
    if (error.kind === GEO_TIMEOUT || error.kind === GEO_UNAVAILABLE) {
      return attempt(LOW_ACCURACY)
    }
    throw error
  }
}

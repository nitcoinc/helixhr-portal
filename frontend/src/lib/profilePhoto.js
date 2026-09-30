// Plan 2026-09-30-001 U5. The client half of the photo rule. It is a
// courtesy that saves a doomed upload, never the boundary:
// `helixhr.utils.prepare_profile_photo` checks size, extension and content
// again and re-encodes. The numbers and sentences mirror the server's
// (`PHOTO_MAX_BYTES`, `_PHOTO_KIND_MESSAGE`, `validate_portal_upload`) so the
// two refusals read the same.
export const PHOTO_MAX_MB = 5
export const PHOTO_ACCEPT = '.png,.jpg,.jpeg,image/png,image/jpeg'

const EXTENSIONS = ['.png', '.jpg', '.jpeg']
const TYPES = ['image/png', 'image/jpeg']

/** The plain sentence refusing `file`, or '' when it may be sent. */
export function photoRefusal(file) {
  if (!file) return 'Pick a photo first.'
  const name = (file.name || '').toLowerCase()
  const extensionOk = EXTENSIONS.some((extension) => name.endsWith(extension))
  // An empty `type` is the browser not knowing; the extension and the
  // server's content check decide then.
  const typeOk = !file.type || TYPES.includes(file.type)
  if (!extensionOk || !typeOk) return 'Your photo must be a PNG or JPEG image.'
  if (!file.size) return 'That file is empty. Pick another one.'
  if (file.size > PHOTO_MAX_MB * 1024 * 1024) {
    return `That file is bigger than ${PHOTO_MAX_MB} MB. Send a smaller one.`
  }
  return ''
}

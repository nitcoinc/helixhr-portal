// P2-U9, accessibility: frappe-ui's `Dialog` renders its close control as
// `<Button variant="ghost">` containing only an `<svg>`, so the button has
// no accessible name. A screen-reader user hears "button" and a keyboard
// user tabbing the dialog hits an unlabelled control; the e2e suite had to
// address it by position, which is the tell.
//
// It is fixed here rather than in the component because the alternative --
// overriding Dialog's `body-header` slot -- also removes reka-ui's
// `DialogTitle`, and the dialog's own `aria-labelledby` points at the id
// that component registers. Trading a labelled close button for an unnamed
// dialog is not a fix. Editing `node_modules/frappe-ui` is not an option.
//
// So: one observer, installed once by the app shell, that names the control
// after reka-ui teleports the dialog into `document.body`. `frontend/tests/
// e2e/hardening.spec.ts` asserts every dialog's close button has a name, so
// if a frappe-ui upgrade moves this markup the suite says so instead of the
// label quietly disappearing.

const LABEL = 'Close'

/**
 * The close control of one open dialog, or null.
 *
 * Found structurally, not by class name: `DialogTitle as="header"` renders
 * the `<header>`, its grandparent is the header row, and the row's only
 * direct-child button is the close control. Every other button in a dialog
 * belongs to this app's own content and is already labelled.
 */
function closeButton(dialog) {
  const header = dialog.querySelector('header')
  const row = header?.parentElement?.parentElement
  return row?.querySelector(':scope > button') || null
}

function labelDialogsIn(root) {
  const dialogs =
    root instanceof Element
      ? [...(root.matches('[role="dialog"]') ? [root] : []), ...root.querySelectorAll('[role="dialog"]')]
      : []
  for (const dialog of dialogs) {
    const button = closeButton(dialog)
    if (button && !button.getAttribute('aria-label') && !button.textContent.trim()) {
      button.setAttribute('aria-label', LABEL)
    }
  }
}

let observer = null

/** Start naming dialog close buttons. Idempotent. */
export function watchDialogs() {
  if (observer || typeof document === 'undefined') return
  labelDialogsIn(document.body)
  observer = new MutationObserver((records) => {
    for (const record of records) {
      for (const node of record.addedNodes) labelDialogsIn(node)
    }
  })
  // `subtree`, because where reka-ui's `DialogPortal` mounts is its
  // decision, not ours -- `body` today, the shell's `#modals` container if
  // that ever changes. The callback does nothing unless an added node
  // contains a `[role="dialog"]`, so the cost is one `querySelectorAll` on
  // nodes that are being inserted anyway.
  observer.observe(document.body, { childList: true, subtree: true })
}

export function unwatchDialogs() {
  observer?.disconnect()
  observer = null
}

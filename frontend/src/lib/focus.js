// KTD16: when a row leaves a list, focus moves to the row that took its place
// (or the last one, at the end), else to the first fallback that exists.
// Synchronous: callers wait for the DOM (nextTick, a frame) themselves.

/** Focus `nodes[index]`, clamped into range, else the first truthy
 * `fallback`. Returns what it focused, or undefined. */
export function focusNear(nodes, index, ...fallbacks) {
  const target = nodes[Math.min(Math.max(index, 0), nodes.length - 1)] || fallbacks.find(Boolean)
  target?.focus()
  return target
}

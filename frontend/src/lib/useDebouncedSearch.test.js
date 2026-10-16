import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope } from 'vue'
import { SEARCH_DELAY_MS, createDebouncedSearch, useDebouncedSearch } from './useDebouncedSearch'

// Plan 2026-10-17-001 U3: the debounce and latest-wins contract every search
// field will share (U15 migrates them).

/** Types `word` one character at a time, `gap` ms apart, then lets the last
 * delay run out. */
async function type(search, word, gap) {
  for (let i = 1; i <= word.length; i += 1) {
    search(word.slice(0, i))
    await vi.advanceTimersByTimeAsync(gap)
  }
  await vi.advanceTimersByTimeAsync(SEARCH_DELAY_MS)
}

/** A lookup whose answers arrive when the test says so. */
function deferredLookup() {
  const calls = []
  const lookup = vi.fn(
    (query) =>
      new Promise((resolve) => {
        calls.push({ query, resolve })
      }),
  )
  return { lookup, calls }
}

describe('createDebouncedSearch', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('waits 250ms by default', async () => {
    const lookup = vi.fn((query) => [query])
    const { search } = createDebouncedSearch(lookup)
    search('p')
    await vi.advanceTimersByTimeAsync(SEARCH_DELAY_MS - 1)
    expect(lookup).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    expect(lookup).toHaveBeenCalledTimes(1)
    expect(SEARCH_DELAY_MS).toBe(250)
  })

  it('fires once for "patel" typed at 100ms a character', async () => {
    const lookup = vi.fn((query) => [query])
    const { search, results } = createDebouncedSearch(lookup)
    await type(search, 'patel', 100)
    expect(lookup).toHaveBeenCalledTimes(1)
    expect(lookup).toHaveBeenCalledWith('patel')
    expect(results.value).toEqual(['patel'])
  })

  it('fires at most once a character for "patel" typed at 300ms a character', async () => {
    const lookup = vi.fn((query) => [query])
    const { search, results } = createDebouncedSearch(lookup)
    await type(search, 'patel', 300)
    expect(lookup.mock.calls.length).toBeGreaterThan(1)
    expect(lookup.mock.calls.length).toBeLessThanOrEqual(5)
    expect(results.value).toEqual(['patel'])
  })

  it('never lets an older answer replace a newer one', async () => {
    const { lookup, calls } = deferredLookup()
    const { search, results, loading } = createDebouncedSearch(lookup)

    search('pat')
    await vi.advanceTimersByTimeAsync(SEARCH_DELAY_MS)
    search('patel')
    await vi.advanceTimersByTimeAsync(SEARCH_DELAY_MS)
    expect(calls.map((call) => call.query)).toEqual(['pat', 'patel'])

    // The newer answer lands first, then the slow older one.
    calls[1].resolve(['Patel'])
    await vi.advanceTimersByTimeAsync(0)
    expect(results.value).toEqual(['Patel'])
    expect(loading.value).toBe(false)
    calls[0].resolve(['Patel', 'Patrick'])
    await vi.advanceTimersByTimeAsync(0)
    expect(results.value).toEqual(['Patel'])
  })

  it('drops an answer already in flight once a new keystroke arrives', async () => {
    const { lookup, calls } = deferredLookup()
    const { search, results } = createDebouncedSearch(lookup, { initial: [] })

    search('pat')
    await vi.advanceTimersByTimeAsync(SEARCH_DELAY_MS)
    search('pate')
    calls[0].resolve(['Patrick'])
    await vi.advanceTimersByTimeAsync(0)
    expect(results.value).toEqual([])
  })

  it('cancels the waiting timer on a new keystroke', async () => {
    const lookup = vi.fn((query) => [query])
    const { search } = createDebouncedSearch(lookup)
    search('p')
    await vi.advanceTimersByTimeAsync(200)
    search('pa')
    await vi.advanceTimersByTimeAsync(200)
    expect(lookup).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(50)
    expect(lookup).toHaveBeenCalledTimes(1)
    expect(lookup).toHaveBeenCalledWith('pa')
  })

  it('is loading from the keystroke, not only from the request', async () => {
    const { lookup, calls } = deferredLookup()
    const { search, loading } = createDebouncedSearch(lookup)
    search('p')
    expect(loading.value).toBe(true)
    await vi.advanceTimersByTimeAsync(SEARCH_DELAY_MS)
    calls[0].resolve(['p'])
    await vi.advanceTimersByTimeAsync(0)
    expect(loading.value).toBe(false)
  })

  it('lets one call skip the delay', async () => {
    const lookup = vi.fn((query) => [query])
    const { search, results } = createDebouncedSearch(lookup)
    search('', 0)
    await vi.advanceTimersByTimeAsync(0)
    expect(lookup).toHaveBeenCalledWith('')
    expect(results.value).toEqual([''])
  })

  it('keeps the newest error and clears it on the next good answer', async () => {
    const lookup = vi.fn((query) => (query === 'x' ? Promise.reject(new Error('down')) : [query]))
    const { search, results, error } = createDebouncedSearch(lookup)
    search('x')
    await vi.advanceTimersByTimeAsync(SEARCH_DELAY_MS)
    expect(error.value?.message).toBe('down')
    search('y')
    await vi.advanceTimersByTimeAsync(SEARCH_DELAY_MS)
    expect(error.value).toBe(null)
    expect(results.value).toEqual(['y'])
  })
})

describe('useDebouncedSearch', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('cancels the timer when its scope is disposed (a component unmounting)', async () => {
    const lookup = vi.fn((query) => [query])
    const scope = effectScope()
    const { search } = scope.run(() => useDebouncedSearch(lookup))
    search('patel')
    scope.stop()
    await vi.advanceTimersByTimeAsync(SEARCH_DELAY_MS * 2)
    expect(lookup).not.toHaveBeenCalled()
  })

  it('ignores an answer that lands after the scope is disposed', async () => {
    const { lookup, calls } = deferredLookup()
    const scope = effectScope()
    const { search, results } = scope.run(() => useDebouncedSearch(lookup))
    search('patel')
    await vi.advanceTimersByTimeAsync(SEARCH_DELAY_MS)
    scope.stop()
    calls[0].resolve(['Patel'])
    await vi.advanceTimersByTimeAsync(0)
    expect(results.value).toBe(null)
  })

  it('works outside any scope', async () => {
    const lookup = vi.fn((query) => [query])
    const { search, results } = useDebouncedSearch(lookup, { delay: 10 })
    search('a')
    await vi.advanceTimersByTimeAsync(10)
    expect(results.value).toEqual(['a'])
  })
})

/**
 * The one way an answer from the server is written to state (specs/frontend-shared, "Late
 * answers"). A request is asked with the parts its answer belongs to, each captured at the
 * call: the newest request of its kind (`newest`), the work it was done under (`under`), the
 * document on show, the graph as it stood, a node, whether a field is being typed in, any
 * other state it was asked under (`watch`). Each store's part is made beside the store
 * (`documentPart`, `editPart`, `nodePart`, `draftPart`, …), so this module reads none. The
 * answer is written only while every part still holds; one that arrives after any has moved
 * is dropped, or handed to the caller's `stale` handler to settle a write that landed. What a
 * request set while it waited (`pending`) is reset the moment it stops being current, a read
 * is aborted then (`signal`), and a write's exclusion (`inFlight`) lasts until it settles.
 * `answersByHand.test.ts` fails for a function that writes state after a wait any other way.
 */

type Unsubscribe = () => void

/** One thing an answer belongs to, captured when it is asked: it holds until that thing moves. */
export interface Part {
  /** Whether it still holds now. */
  readonly holds: () => boolean
  /**
   * Call *listener* whenever it may have moved, until the returned function is called. A part
   * whose state nothing announces (a slot a primitive holds) gives a no-op: it is asked again
   * as the answer settles, and it must not move back once moved.
   */
  readonly subscribe: (listener: () => void) => Unsubscribe
}

/** What an answer belongs to: at least one part. */
export type BelongsTo = readonly [Part, ...Part[]]

/** A kind of request of which only the newest is current: a list read, a file opened, a lookup. */
export interface RequestKind {
  /** Put every request of this kind out of date, as leaving what they were asked in does. */
  bump(): void
}

interface KindState {
  turn: number
  readonly listeners: Set<() => void>
}

const kindStates = new WeakMap<RequestKind, KindState>()

function stateOf(kind: RequestKind): KindState {
  const state = kindStates.get(kind)
  if (state === undefined) throw new Error("Not a request kind: make one with requestKind()")
  return state
}

function advance(state: KindState): number {
  // The turn is this request's before anyone hears of it: a request a listener asks of the
  // same kind takes the next one, and this one is then out of date.
  state.turn += 1
  const turn = state.turn
  for (const listener of [...state.listeners]) listener()
  return turn
}

export function requestKind(): RequestKind {
  const kind: RequestKind = { bump: () => void advance(stateOf(kind)) }
  kindStates.set(kind, { turn: 0, listeners: new Set() })
  return kind
}

function kindPart(state: KindState, turn: number): Part {
  return {
    holds: () => state.turn === turn,
    subscribe: (listener) => {
      state.listeners.add(listener)
      return () => {
        state.listeners.delete(listener)
      }
    },
  }
}

/** The newest request of *kind*: taking its turn puts every earlier request of it out of date. */
export function newest(kind: RequestKind): Part {
  const state = stateOf(kind)
  return kindPart(state, advance(state))
}

/** The request of *kind* made last, without taking a turn: a further page of the list loaded. */
export function under(kind: RequestKind): Part {
  const state = stateOf(kind)
  return kindPart(state, state.turn)
}

/**
 * The asker's own life, as the abort signal it gave: a request made for a caller that ends
 * it with a signal (a job wait, an ensure pass) holds while that signal has not aborted.
 */
export function signalPart(signal: AbortSignal): Part {
  return {
    holds: () => !signal.aborted,
    subscribe: (listener) => {
      signal.addEventListener("abort", listener)
      return () => signal.removeEventListener("abort", listener)
    },
  }
}

/** A store an answer can be watched against: a zustand store, or anything shaped like one. */
export interface Watchable<S> {
  getState: () => S
  subscribe: (listener: (state: S, previous: S) => void) => Unsubscribe
}

/**
 * The part of *store* that *select* reads, as it reads now. It holds while *holds* says so of
 * the value read now and the value read when asked: while they are the same (`Object.is`)
 * unless told otherwise.
 */
export function watch<S, V>(
  store: Watchable<S>,
  select: (state: S) => V,
  holds: (asked: V, now: V) => boolean = Object.is,
): Part {
  const asked = select(store.getState())
  return {
    holds: () => holds(asked, select(store.getState())),
    subscribe: (listener) => store.subscribe(() => listener()),
  }
}

/** How an answer ended: applied, its failure handled, or late. */
export type Outcome = "ok" | "failed" | "stale"

/** An answer as it settled: its value, or why it failed. */
export type Settled<T> = { readonly value: T } | { readonly error: unknown }

/**
 * What is done with an answer. A handler is given the request it answers, to answer a further
 * request under it, and may return a promise, which `answer` waits for.
 */
export interface Handlers<T> {
  /** The answer, while what asked for it is current. */
  readonly ok?: (value: T, asked: Asked) => unknown
  /**
   * Its failure, while current, or what `ok` threw using the answer. Without it, a current
   * failure rejects `answer`'s promise.
   */
  readonly failed?: (error: unknown, asked: Asked) => unknown
  /**
   * A late answer, to settle one that must not be dropped (a write that landed); without it a
   * late answer is dropped. It runs also once the asker has let go.
   */
  readonly stale?: (settled: Settled<T>) => unknown
}

export interface AskOptions {
  /**
   * What the request sets while it waits: called with true when it is asked, and once with
   * false when it ends, the moment it stops being current or is let go.
   */
  readonly pending?: (waiting: boolean) => void
}

export interface AnswerOptions extends AskOptions {
  /**
   * A write's exclusion: called with true when it is asked, and once with false when its
   * answer has settled and been handled, current or not, so no other write starts meanwhile.
   */
  readonly inFlight?: (flying: boolean) => void
}

/** The request sent, or a function sending it with the request's abort signal. */
export type Request<T> = Promise<T> | ((signal: AbortSignal) => Promise<T>)

export interface Asked {
  /** Whether every part still holds, has held throughout, and the asker has not let go. */
  current(): boolean
  /** Aborts the moment the request stops being current or is let go. For a read, never a write. */
  readonly signal: AbortSignal
  /** Wait for *request* and apply its answer through *handlers* while current. */
  answer<T>(request: Request<T>, handlers: Handlers<T>): Promise<Outcome>
  /**
   * Run *write* now while the request is current, for what follows several answers (a run's
   * last step after its loop); whether it ran.
   */
  apply(write: () => void): boolean
  /** Let go: the request stops being current; only a `stale` handler runs after it. */
  release(): void
}

class Asking implements Asked {
  /** Set the first time a part is seen not to hold, or the asker lets go: never cleared. */
  private ended = false
  private readonly controller = new AbortController()
  private unsubscribes: Unsubscribe[] = []
  private readonly held: BelongsTo
  private readonly pending: ((waiting: boolean) => void) | undefined

  constructor(held: BelongsTo, pending: ((waiting: boolean) => void) | undefined) {
    this.held = held
    this.pending = pending
    pending?.(true)
    // Watched from the moment it is asked, so a part that moves and moves back never revives it.
    const check = () => {
      if (!this.ended && !this.held.every((part) => part.holds())) this.end()
    }
    for (const part of held) {
      if (this.ended) break
      this.unsubscribes.push(part.subscribe(check))
    }
    check()
  }

  current(): boolean {
    if (!this.ended && !this.held.every((part) => part.holds())) this.end()
    return !this.ended
  }

  get signal(): AbortSignal {
    return this.controller.signal
  }

  async answer<T>(request: Request<T>, handlers: Handlers<T>): Promise<Outcome> {
    let settled: Settled<T>
    try {
      settled = { value: await (typeof request === "function" ? request(this.signal) : request) }
    } catch (error: unknown) {
      settled = { error }
    }
    // The check and the handler run in the same turn, so nothing moves between them.
    if (!this.current()) {
      if (handlers.stale) await handlers.stale(settled)
      return "stale"
    }
    if ("value" in settled) {
      try {
        await handlers.ok?.(settled.value, this)
      } catch (error: unknown) {
        // An answer the caller cannot use is the request's failure, as one refused is.
        if (!handlers.failed) throw error
        await handlers.failed(error, this)
        return "failed"
      }
      return "ok"
    }
    if (!handlers.failed) throw settled.error
    await handlers.failed(settled.error, this)
    return "failed"
  }

  apply(write: () => void): boolean {
    if (!this.current()) return false
    write()
    return true
  }

  release(): void {
    this.end()
  }

  private unsubscribe(): void {
    for (const unsubscribe of this.unsubscribes) unsubscribe()
    this.unsubscribes = []
  }

  private end(): void {
    if (this.ended) return
    this.ended = true
    this.unsubscribe()
    this.controller.abort()
    this.pending?.(false)
  }
}

/** Ask for an answer that belongs to *belongsTo*; see `Asked`. An effect lets go in its cleanup. */
export function ask(belongsTo: BelongsTo, options: AskOptions = {}): Asked {
  if (belongsTo.length === 0) throw new Error("An answer must say what it belongs to")
  return new Asking(belongsTo, options.pending)
}

/** Ask, answer *request* once, and let go once its handler has run. */
export async function answer<T>(
  belongsTo: BelongsTo,
  request: Request<T>,
  handlers: Handlers<T>,
  options: AnswerOptions = {},
): Promise<Outcome> {
  const asked = ask(belongsTo, options)
  options.inFlight?.(true)
  try {
    return await asked.answer(request, handlers)
  } finally {
    asked.release()
    options.inFlight?.(false)
  }
}

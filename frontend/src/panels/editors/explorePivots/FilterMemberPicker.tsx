import { useEffect, useMemo, useState } from "react"
import { Loader2 } from "lucide-react"

import type { ExplorePivotMembersResponse } from "../../../api/types"
import type { PivotFilterPlacement, PivotMember } from "../../explore/pivotConfig"
import { INPUT_STYLE } from "../_shared"
import { memberIdentity, type LoadPivotFilterMembers } from "./placements"

const FILTER_MEMBER_SEARCH_DEBOUNCE_MS = 250

export default function FilterMemberPicker({
  placement,
  loadMembers,
  currentConfigHash,
  onChange,
}: {
  placement: PivotFilterPlacement
  loadMembers?: LoadPivotFilterMembers
  currentConfigHash: string | null
  onChange: (members: PivotMember[]) => void
}) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState("")
  const requestKey = `${placement.field}\u0000${search}`
  const [loadState, setLoadState] = useState<{
    requestKey: string | null
    // The Explore cache identity the response was loaded under: a response is
    // only rendered while that identity is still current, so members from a
    // previous graph/source are never left selectable, while display-only
    // pivot edits (which keep the identity) leave the list untouched.
    identity: string | null
    response: ExplorePivotMembersResponse | null
    error: string | null
  }>({ requestKey: null, identity: null, response: null, error: null })

  useEffect(() => {
    if (!open || !loadMembers) return

    const controller = new AbortController()
    const load = () => loadMembers(placement.field, search, controller.signal)
      .then((nextResponse) => {
        if (controller.signal.aborted) return
        if (nextResponse.field !== null && nextResponse.field !== placement.field) {
          throw new Error("Filter member response did not match the requested field.")
        }
        setLoadState({
          requestKey,
          identity: currentConfigHash,
          response: nextResponse,
          error: null,
        })
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return
        setLoadState({
          requestKey,
          identity: currentConfigHash,
          response: null,
          error: reason instanceof Error ? reason.message : String(reason),
        })
      })
    const timer = search === ""
      ? null
      : window.setTimeout(load, FILTER_MEMBER_SEARCH_DEBOUNCE_MS)
    if (timer === null) load()

    return () => {
      if (timer !== null) window.clearTimeout(timer)
      controller.abort()
    }
  }, [currentConfigHash, loadMembers, open, placement.field, requestKey, search])

  const currentLoadState =
    loadState.requestKey === requestKey && loadState.identity === currentConfigHash
      ? loadState
      : null
  const response = currentLoadState?.response ?? null
  const loading = open && !!loadMembers && currentLoadState === null
  const unavailableMessage = loadMembers
    ? null
    : "Filter members are unavailable until the Explore dataset is cached."
  const error = unavailableMessage ?? currentLoadState?.error ?? null

  const selected = useMemo(
    () => new Set(placement.members.map(memberIdentity)),
    [placement.members],
  )

  const toggleMember = (member: PivotMember) => {
    const identity = memberIdentity(member)
    if (selected.has(identity)) {
      onChange(placement.members.filter((candidate) => memberIdentity(candidate) !== identity))
    } else {
      onChange([...placement.members, { kind: member.kind, value: member.value }])
    }
  }

  const failure = response?.failure
  const summary =
    placement.members.length === 0
      ? "All members"
      : `${placement.members.length} selected`

  return (
    <div className="mt-2 w-full">
      <button
        type="button"
        aria-label={`Choose members for ${placement.field}`}
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        className="focus-ring rounded px-2 py-1 text-[10px] font-semibold"
        style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}
      >
        Choose members for {placement.field}: {summary}
      </button>

      {open && (
        <div
          className="mt-2 rounded-md p-2"
          style={{ background: "var(--bg-panel)", border: "1px solid var(--border)" }}
        >
          <label className="block text-[10px] font-semibold">
            Search members for {placement.field}
            <input
              type="search"
              aria-label={`Search members for ${placement.field}`}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              className="mt-1 block w-full rounded px-2 py-1 text-xs"
              style={INPUT_STYLE}
            />
          </label>

          {loading && (
            <div
              role="status"
              className="mt-2 flex items-center gap-1 text-[10px]"
              style={{ color: "var(--text-muted)" }}
            >
              <Loader2 size={11} className="animate-spin" aria-hidden="true" />
              Loading members
            </div>
          )}

          {(error || failure) && (
            <div
              role="alert"
              className="mt-2 rounded px-2 py-1.5 text-[10px] leading-relaxed"
              style={{ color: "var(--danger)", background: "var(--danger-soft)" }}
            >
              {error ?? failure?.message}
              {failure?.remediation ? ` ${failure.remediation}` : ""}
            </div>
          )}

          {!loading && response?.status === "ok" && response.members.length === 0 && (
            <div className="mt-2 text-[10px]" style={{ color: "var(--text-muted)" }}>
              No matching members.
            </div>
          )}

          {response?.status === "ok" && response.members.length > 0 && (
            <div className="mt-2 max-h-44 overflow-auto" role="group" aria-label="Filter members">
              {response.members.map((option) => {
                const member = option.key as PivotMember
                return (
                  <label
                    key={memberIdentity(member)}
                    className="flex items-center gap-2 rounded px-1.5 py-1 text-[11px]"
                  >
                    <input
                      type="checkbox"
                      aria-label={`${option.label} (${option.count})`}
                      checked={selected.has(memberIdentity(member))}
                      onChange={() => toggleMember(member)}
                    />
                    <span className="min-w-0 flex-1 truncate">{option.label}</span>
                    <span style={{ color: "var(--text-muted)" }}>{option.count}</span>
                  </label>
                )
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

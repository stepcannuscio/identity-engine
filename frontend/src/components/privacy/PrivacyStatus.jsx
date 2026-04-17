const MODE_LABELS = {
  local: 'Local',
  external: 'External',
  blocked: 'Blocked',
  unknown: 'Unknown',
}

export default function PrivacyStatus({
  privacy,
  compact = false,
  showSummary = true,
}) {
  if (!privacy) {
    return null
  }

  const mode = privacy.execution_mode ?? 'unknown'
  const label = MODE_LABELS[mode] ?? MODE_LABELS.unknown

  return (
    <div className={`privacy-status ${compact ? 'compact' : ''}`}>
      <div className="privacy-status-top">
        <span className={`privacy-badge ${mode}`}>{label}</span>
        {privacy.routing_enforced ? (
          <span className="privacy-inline-note">Privacy rules applied</span>
        ) : null}
      </div>
      {showSummary && privacy.summary ? (
        <p className="privacy-summary">{privacy.summary}</p>
      ) : null}
    </div>
  )
}

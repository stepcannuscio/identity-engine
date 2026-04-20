import { useMemo, useState } from 'react'
import PrivacyStatus from '../privacy/PrivacyStatus.jsx'

function formatSessionTitle(sessionType) {
  if (sessionType === 'interview') {
    return 'Guided interview'
  }
  if (sessionType === 'vault') {
    return 'Vault import'
  }
  return 'Freeform session'
}

function formatDate(value) {
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(new Date(value))
}

function formatTime(value) {
  return new Intl.DateTimeFormat('en-US', {
    hour: 'numeric',
    minute: '2-digit',
  }).format(new Date(value))
}

export default function SessionCard({ session }) {
  const [expanded, setExpanded] = useState(false)

  const coveredDomains = useMemo(() => {
    const domains = session.routing_log?.flatMap((entry) => entry.domains_referenced ?? []) ?? []
    return [...new Set(domains)]
  }, [session.routing_log])

  return (
    <article
      className={`session-card ${expanded ? 'expanded' : ''}`}
      onClick={() => setExpanded((current) => !current)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          setExpanded((current) => !current)
        }
      }}
      role="button"
      tabIndex={0}
    >
      <div className="session-card-top">
        <div>
          <div className="toggle-group">
            <h3 className="session-card-title">{formatSessionTitle(session.session_type)}</h3>
            <span className="session-badge">{session.session_type}</span>
          </div>
          <p className="session-meta">
            {session.attributes_created} attributes created &middot;{' '}
            <span
              className={`external-count ${
                session.external_calls_made > 0 ? 'external' : 'local'
              }`}
            >
              {session.external_calls_made} external calls
            </span>
          </p>
          <p className="session-covered">
            Covered: {coveredDomains.length ? coveredDomains.join(', ') : 'none recorded'}
          </p>
          <PrivacyStatus privacy={session.privacy} compact />
        </div>
        <span className="session-date">{formatDate(session.started_at)}</span>
      </div>

      {expanded ? (
        <div className="session-timeline">
          {session.routing_log?.length ? (
            session.routing_log.map((entry, index) => (
              <div key={`${entry.timestamp}-${index}`} className="timeline-item">
                <PrivacyStatus
                  privacy={entry.privacy}
                  compact
                  showSummary={entry.privacy?.execution_mode === 'blocked'}
                />
                <p className="timeline-meta">
                  {entry.backend} &middot; {entry.query_type} &middot;{' '}
                  {entry.attribute_count} attributes &middot; {formatTime(entry.timestamp)}
                </p>
                {entry.domains_referenced?.length ? (
                  <p className="field-help">
                    Domains: {entry.domains_referenced.join(', ')}
                  </p>
                ) : null}
              </div>
            ))
          ) : (
            <p className="field-help">No routing log entries recorded for this session.</p>
          )}
        </div>
      ) : null}
    </article>
  )
}

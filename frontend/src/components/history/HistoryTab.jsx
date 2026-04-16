import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getSessions } from '../../api/endpoints.js'
import SessionCard from './SessionCard.jsx'

export default function HistoryTab() {
  const { data = [], isLoading, isError } = useQuery({
    queryKey: ['sessions'],
    queryFn: getSessions,
  })

  const attributesCreated = useMemo(
    () => data.reduce((total, session) => total + session.attributes_created, 0),
    [data],
  )

  return (
    <section className="history-tab">
      <div className="history-inner">
        <div className="history-summary">
          <div className="history-header">
            <div>
              <p className="eyebrow">Session history</p>
              <h2 className="history-title">Recent sessions</h2>
            </div>
          </div>
          <p className="history-summary-text">
            {data.length} sessions &middot; {attributesCreated} attributes created
          </p>
        </div>

        {isLoading ? <div className="empty-state">Loading sessions...</div> : null}
        {isError ? <div className="empty-state">Unable to load history.</div> : null}
        {!isLoading && !isError && data.length === 0 ? (
          <div className="empty-state">No sessions have been recorded yet.</div>
        ) : null}

        {!isLoading && !isError ? (
          <div className="history-list">
            {data.map((session) => (
              <SessionCard key={session.id} session={session} />
            ))}
          </div>
        ) : null}
      </div>
    </section>
  )
}

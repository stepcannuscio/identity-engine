function relativeDate(value) {
  if (!value) {
    return 'not yet confirmed'
  }

  const then = new Date(value)
  const now = new Date()
  const diffDays = Math.max(0, Math.round((now - then) / (1000 * 60 * 60 * 24)))

  if (diffDays === 0) {
    return 'today'
  }
  if (diffDays === 1) {
    return '1 day ago'
  }
  return `${diffDays} days ago`
}

export default function AttributeCard({ attribute, onConfirm, onEdit, onReject }) {
  return (
    <article className="attribute-card">
      <div className="attribute-card-top">
        <div>
          <h4 className="attribute-label">{attribute.label.replaceAll('_', ' ')}</h4>
          <p className="attribute-card-secondary">{attribute.domain}</p>
        </div>
        <div className="attribute-meta">
          <button type="button" className="text-button attribute-edit" onClick={onConfirm}>
            confirm
          </button>
          <button type="button" className="text-button attribute-edit" onClick={onReject}>
            reject
          </button>
          <button type="button" className="text-button attribute-edit" onClick={onEdit}>
            edit
          </button>
        </div>
      </div>
      <p className="attribute-card-value">{attribute.value}</p>
      {attribute.elaboration ? (
        <p className="attribute-card-secondary">{attribute.elaboration}</p>
      ) : null}
      <div className="attribute-confidence-track" aria-hidden="true">
        <div
          className="attribute-confidence-bar"
          style={{ width: `${Math.max(0, Math.min(1, attribute.confidence)) * 100}%` }}
        />
      </div>
      <div className="attribute-meta">
        <span>{attribute.mutability}</span>
        <span>&middot;</span>
        <span>{attribute.source}</span>
        <span>&middot;</span>
        <span>{attribute.status}</span>
      </div>
      <div className="attribute-footer">
        <span
          className={`routing-badge ${
            attribute.routing === 'local_only' ? 'local' : 'external'
          }`}
        >
          {attribute.routing === 'local_only' ? 'local only' : 'external ok'}
        </span>
        <span>&middot;</span>
        <span>last confirmed {relativeDate(attribute.last_confirmed)}</span>
      </div>
    </article>
  )
}

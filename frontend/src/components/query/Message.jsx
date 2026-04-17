import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import PrivacyStatus from '../privacy/PrivacyStatus.jsx'

function formatDuration(durationMs) {
  if (!durationMs && durationMs !== 0) {
    return null
  }
  return `${(durationMs / 1000).toFixed(1)}s`
}

function renderMetadata(metadata) {
  if (!metadata) {
    return null
  }

  const domains = metadata.domains_referenced?.length
    ? metadata.domains_referenced.join(', ')
    : 'no domains'

  return (
    <div className="assistant-metadata">
      <span>[</span>
      <span className={`assistant-backend ${metadata.backend_used}`}>
        {metadata.backend_used}
      </span>
      <span>&middot;</span>
      <span>{metadata.attributes_used} attributes</span>
      <span>&middot;</span>
      <span>{domains}</span>
      <span>&middot;</span>
      <span>{formatDuration(metadata.duration_ms)}</span>
      <span>]</span>
    </div>
  )
}

export default function Message({ message }) {
  if (message.role === 'user') {
    return (
      <article className="message user">
        <div className="message-bubble">{message.content}</div>
      </article>
    )
  }

  return (
    <article className="message assistant">
      <div className="assistant-body">
        <div className="markdown-body">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {message.content}
          </ReactMarkdown>
        </div>
        <PrivacyStatus privacy={message.metadata?.privacy} />
        {renderMetadata(message.metadata)}
      </div>
    </article>
  )
}
